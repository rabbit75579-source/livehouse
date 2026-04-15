#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scraper.py - 台北 Livehouse Instagram 爬蟲
自動抓取各場地 IG 貼文圖片，用 Claude Vision 辨識演出節目資訊，
並將結果合併寫入 events.json。
"""

import asyncio
import json
import os
import re
import sys
import base64
from pathlib import Path
from datetime import datetime

# ── 讀取 .env 設定檔（若存在）──────────────────────────────────────────────
# 格式：ANTHROPIC_API_KEY=sk-ant-xxxx
_env_path = Path(__file__).parent / '.env'
if _env_path.exists():
    for _line in _env_path.read_text(encoding='utf-8').splitlines():
        _line = _line.strip()
        if _line and not _line.startswith('#') and '=' in _line:
            _k, _v = _line.split('=', 1)
            os.environ.setdefault(_k.strip(), _v.strip())

from playwright.async_api import async_playwright
import anthropic

# ── 基本設定 ───────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent

# IG 帳號 → 場地 ID 對照表（往後可在此補充）
IG_ACCOUNTS = {
    'thewallmusic':       'wall',
    'legacy_taipei':      'legacy',
    'riverside_music':    'riverside',
    'kkboxstudio':        'kkbox',
    'pipelivetw':         'pipe',
    'clapperboard.taipei':'clapper',
    'cornermusictw':      'corner',
}

# 每個帳號最多抓幾篇最新貼文
POSTS_PER_ACCOUNT = 5

# 各種檔案路徑
SESSION_FILE      = BASE_DIR / 'ig_session.json'       # 儲存 IG 登入 Session
PROCESSED_IDS_FILE= BASE_DIR / 'processed_ids.json'    # 已處理的貼文 ID（避免重複）
EVENTS_FILE       = BASE_DIR / 'events.json'            # 輸出的演出資料


# ══════════════════════════════════════════════════════════════════════════════
# 資料讀寫工具函式
# ══════════════════════════════════════════════════════════════════════════════

def load_processed_ids() -> set:
    """讀取已處理的貼文 ID 集合，避免重複分析同一篇貼文"""
    if PROCESSED_IDS_FILE.exists():
        return set(json.loads(PROCESSED_IDS_FILE.read_text(encoding='utf-8')))
    return set()


def save_processed_ids(ids: set):
    """儲存已處理的貼文 ID"""
    PROCESSED_IDS_FILE.write_text(
        json.dumps(sorted(ids), ensure_ascii=False, indent=2),
        encoding='utf-8'
    )


def load_events() -> list:
    """讀取現有 events.json，若不存在則回傳空列表"""
    if EVENTS_FILE.exists():
        return json.loads(EVENTS_FILE.read_text(encoding='utf-8'))
    return []


def save_events(events: list):
    """儲存演出資料，依日期 + 場地排序"""
    events.sort(key=lambda e: (e.get('date', ''), e.get('venue', '')))
    EVENTS_FILE.write_text(
        json.dumps(events, ensure_ascii=False, indent=2),
        encoding='utf-8'
    )
    print(f'💾 events.json 已更新，共 {len(events)} 筆')


# ══════════════════════════════════════════════════════════════════════════════
# Instagram 登入與瀏覽
# ══════════════════════════════════════════════════════════════════════════════

async def ensure_login(page, context):
    """
    前往 IG 首頁，判斷是否已登入。
    若未登入，開啟登入頁等待使用者手動完成，完成後儲存 session。
    之後每次執行都自動載入 session，不需再手動登入。
    """
    print('🔑 檢查 Instagram 登入狀態...')
    await page.goto('https://www.instagram.com/', wait_until='domcontentloaded', timeout=30000)
    await page.wait_for_timeout(3000)

    # 偵測登入表單是否存在
    login_input = await page.query_selector('input[name="username"]')
    if not login_input:
        print('✅ 已使用儲存的 Session 登入 Instagram')
        return

    # 需要手動登入
    print()
    print('╔══════════════════════════════════════════╗')
    print('║  請在瀏覽器視窗中登入 Instagram          ║')
    print('║  登入成功並進入首頁後，                  ║')
    print('║  回到終端機按 Enter 繼續...               ║')
    print('╚══════════════════════════════════════════╝')
    print()

    # 等待 URL 離開登入頁（最多等 3 分鐘）
    try:
        await page.wait_for_url(
            lambda url: 'accounts/login' not in url and 'accounts/onetap' not in url,
            timeout=180000
        )
        await page.wait_for_timeout(2000)
    except Exception:
        pass  # 若超時，仍讓使用者按 Enter 確認

    input('✅ 確認已登入後，按 Enter 繼續... ')
    print()

    # 儲存 session（後續執行自動載入，不再需要手動登入）
    await context.storage_state(path=str(SESSION_FILE))
    print(f'💾 Session 已儲存到 {SESSION_FILE.name}')


async def dismiss_popups(page):
    """關閉可能出現的彈窗（通知詢問、Cookie 通知等）"""
    popup_texts = ['以後再說', 'Not Now', '稍後再說', '關閉', '拒絕']
    for text in popup_texts:
        try:
            btn = await page.query_selector(f'button:has-text("{text}")')
            if btn:
                await btn.click()
                await page.wait_for_timeout(500)
                break
        except Exception:
            pass


# ══════════════════════════════════════════════════════════════════════════════
# 圖片抓取
# ══════════════════════════════════════════════════════════════════════════════

async def fetch_image_as_base64(page, img_url: str) -> str | None:
    """
    在瀏覽器內透過 fetch 下載圖片，回傳 base64 字串。
    利用已登入的瀏覽器 session，避免跨域或 cookie 問題。
    """
    try:
        result = await page.evaluate(
            '''async (url) => {
                try {
                    const resp = await fetch(url);
                    if (!resp.ok) return null;
                    const buf   = await resp.arrayBuffer();
                    const bytes = new Uint8Array(buf);
                    // 轉為 base64
                    let binary = '';
                    const CHUNK = 8192;
                    for (let i = 0; i < bytes.length; i += CHUNK) {
                        binary += String.fromCharCode(...bytes.subarray(i, i + CHUNK));
                    }
                    return btoa(binary);
                } catch (e) {
                    return null;
                }
            }''',
            img_url
        )
        return result
    except Exception as e:
        print(f'    ⚠️  圖片下載失敗: {e}')
        return None


async def get_post_images(page, username: str, processed_ids: set, count: int = 5) -> list:
    """
    前往指定 IG 帳號頁面，抓取最新 count 篇貼文的圖片。
    回傳格式：[{'id': 'POST_ID', 'b64': 'base64字串', 'media_type': 'image/jpeg'}]
    已在 processed_ids 中的貼文 ID 會自動跳過。
    """
    results = []
    profile_url = f'https://www.instagram.com/{username}/'

    print(f'  📲 前往 @{username}')
    try:
        await page.goto(profile_url, wait_until='domcontentloaded', timeout=30000)
        await page.wait_for_timeout(3000)
        await dismiss_popups(page)

        # ── 從頁面取得貼文連結（格式：/p/XXXX/）────────────────────────────
        post_links = await page.query_selector_all('a[href*="/p/"]')

        seen_ids   = set()
        post_pairs = []   # [(href, post_id), ...]

        for link in post_links:
            href = await link.get_attribute('href')
            if not href:
                continue
            m = re.search(r'/p/([A-Za-z0-9_-]+)/', href)
            if not m:
                continue
            post_id = m.group(1)
            if post_id in seen_ids:
                continue
            seen_ids.add(post_id)
            post_pairs.append((href, post_id))
            if len(post_pairs) >= count:
                break

        if not post_pairs:
            print(f'  ⚠️  @{username} 找不到貼文連結（可能需要重新登入）')
            return results

        print(f'  找到 {len(post_pairs)} 篇貼文')

        # ── 逐篇處理 ────────────────────────────────────────────────────────
        for href, post_id in post_pairs:

            # 已處理過的貼文直接跳過
            if post_id in processed_ids:
                print(f'  ⏭  貼文 {post_id} 已分析過，跳過')
                continue

            print(f'  🖼  處理貼文 {post_id}...')

            try:
                post_url = f'https://www.instagram.com{href}'
                await page.goto(post_url, wait_until='domcontentloaded', timeout=30000)
                await page.wait_for_timeout(2000)
                await dismiss_popups(page)

                # 嘗試多種選擇器找主圖
                img_src    = None
                media_type = 'image/jpeg'

                selectors = [
                    'article div[role="button"] img[src*="cdninstagram"]',
                    'article div[role="button"] img[src*="fbcdn"]',
                    'article img[src*="cdninstagram"]',
                    'article img[src*="fbcdn"]',
                ]
                for sel in selectors:
                    img = await page.query_selector(sel)
                    if img:
                        src = await img.get_attribute('src')
                        if src:
                            img_src = src
                            # 判斷格式
                            if '.png' in src:
                                media_type = 'image/png'
                            elif '.webp' in src:
                                media_type = 'image/webp'
                            break

                if img_src:
                    b64 = await fetch_image_as_base64(page, img_src)
                else:
                    b64 = None

                if not b64:
                    # 備用方案：直接截圖貼文 article 區域
                    print(f'    ↩ 改用截圖方式')
                    article = await page.query_selector('article')
                    if article:
                        screenshot = await article.screenshot(type='jpeg', quality=85)
                    else:
                        screenshot = await page.screenshot(
                            type='jpeg', quality=85,
                            clip={'x': 0, 'y': 0, 'width': 800, 'height': 800}
                        )
                    b64        = base64.b64encode(screenshot).decode('utf-8')
                    media_type = 'image/jpeg'

                results.append({'id': post_id, 'b64': b64, 'media_type': media_type})

            except Exception as e:
                print(f'    ❌ 貼文 {post_id} 處理失敗: {e}')

            # 每篇貼文之間延遲，降低被封鎖風險
            await page.wait_for_timeout(1800)

    except Exception as e:
        print(f'  ❌ @{username} 讀取失敗: {e}')

    return results


# ══════════════════════════════════════════════════════════════════════════════
# Claude Vision 分析
# ══════════════════════════════════════════════════════════════════════════════

def analyze_with_claude(
    client: anthropic.Anthropic,
    b64: str,
    media_type: str,
    venue_id: str,
    source_ig: str
) -> dict | None:
    """
    將圖片傳給 claude-sonnet-4-6，請它判斷是否為演出海報。
    若是，提取日期、表演者、時間、票價，回傳格式化的事件 dict。
    若否，回傳 None。
    """
    prompt = (
        '這是台北 Livehouse 的 Instagram 貼文圖片。\n\n'
        '請判斷這張圖片是否包含演出節目資訊（例如：演唱會 / 音樂表演海報 / 售票公告）。\n\n'
        '【若是演出節目資訊】請只回傳以下 JSON，不要加任何說明文字或 markdown：\n'
        '{"is_event":true,"date":"YYYY-MM-DD","name":"表演者名稱","time":"HH:MM","price":"$金額"}\n\n'
        '【若不是演出節目資訊】請只回傳：\n'
        '{"is_event":false}\n\n'
        '注意事項：\n'
        '- date 請轉為 YYYY-MM-DD 格式；若無法判斷年份，預設為 2026 年\n'
        '- 若有多組表演者，用 " × " 連接\n'
        '- price 若有多種票價，取最低價，格式如 "$500"\n'
        '- 若某欄位無法辨識，該欄位填 null\n'
        '- 只回傳 JSON，不要其他任何文字'
    )

    try:
        response = client.messages.create(
            model='claude-sonnet-4-6',
            max_tokens=300,
            messages=[{
                'role': 'user',
                'content': [
                    {
                        'type': 'image',
                        'source': {
                            'type': 'base64',
                            'media_type': media_type,
                            'data': b64
                        }
                    },
                    {'type': 'text', 'text': prompt}
                ]
            }]
        )

        raw = response.content[0].text.strip()

        # 清除 Claude 偶爾加上的 markdown code block
        raw = re.sub(r'^```(?:json)?\s*\n?', '', raw)
        raw = re.sub(r'\n?\s*```$', '', raw)

        data = json.loads(raw)

        if data.get('is_event') and data.get('date'):
            return {
                'date':      data['date'],
                'venue':     venue_id,
                'name':      data.get('name') or '未知表演者',
                'time':      data.get('time') or '',
                'price':     data.get('price') or '',
                'source_ig': source_ig
            }

    except json.JSONDecodeError as e:
        print(f'    ⚠️  JSON 解析失敗（原始回應：{raw[:80]}）: {e}')
    except Exception as e:
        print(f'    ⚠️  Claude 分析失敗: {e}')

    return None


# ══════════════════════════════════════════════════════════════════════════════
# 主程式
# ══════════════════════════════════════════════════════════════════════════════

async def main():
    print()
    print('╔══════════════════════════════════════════╗')
    print('║  台北 Livehouse 節目彙整系統 — 爬蟲      ║')
    print(f'║  {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}                    ║')
    print('╚══════════════════════════════════════════╝')
    print()

    # ── 確認 API Key ─────────────────────────────────────────────────────────
    api_key = os.environ.get('ANTHROPIC_API_KEY', '').strip()
    if not api_key:
        print('❌ 錯誤：找不到 ANTHROPIC_API_KEY')
        print('   請在專案資料夾建立 .env 檔，內容：')
        print('   ANTHROPIC_API_KEY=sk-ant-...')
        sys.exit(1)

    # ── 載入現有資料 ──────────────────────────────────────────────────────────
    processed_ids = load_processed_ids()
    events        = load_events()
    print(f'📋 現有演出資料：{len(events)} 筆')
    print(f'📋 已處理貼文：{len(processed_ids)} 篇')
    print()

    # ── 初始化 Anthropic client ───────────────────────────────────────────────
    client = anthropic.Anthropic(api_key=api_key)

    new_events       = []
    new_processed    = set()

    async with async_playwright() as pw:

        # 啟動 Chromium（需要顯示視窗，IG 對 headless 有更多限制）
        browser = await pw.chromium.launch(
            headless=False,
            args=[
                '--no-sandbox',
                '--disable-blink-features=AutomationControlled',
                '--window-position=50,50',
            ]
        )

        # 若已有 session 檔，自動載入；否則開啟新 context 等待手動登入
        ctx_kwargs = {
            'viewport':   {'width': 1280, 'height': 800},
            'user_agent': (
                'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/124.0.0.0 Safari/537.36'
            ),
            'locale': 'zh-TW',
        }
        if SESSION_FILE.exists():
            ctx_kwargs['storage_state'] = str(SESSION_FILE)

        context = await browser.new_context(**ctx_kwargs)
        page    = await context.new_page()

        # 隱藏 webdriver 特徵，降低被 IG 偵測為機器人的機率
        await page.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )

        # 確認已登入（首次需手動登入）
        await ensure_login(page, context)

        # ── 逐一巡覽各 IG 帳號 ───────────────────────────────────────────────
        for username, venue_id in IG_ACCOUNTS.items():
            print(f'\n{'─'*48}')
            print(f'🏠 帳號：@{username}  →  場地 ID：{venue_id}')
            print(f'{'─'*48}')

            posts = await get_post_images(page, username, processed_ids, POSTS_PER_ACCOUNT)

            for post in posts:
                post_id = post['id']

                print(f'  🤖 送 Claude 分析中...')
                event = analyze_with_claude(
                    client,
                    post['b64'],
                    post['media_type'],
                    venue_id,
                    username
                )

                if event:
                    print(f'  ✅ 演出資訊：{event["name"]}  {event["date"]}  {event["venue"]}')
                    new_events.append(event)
                else:
                    print(f'  ➖ 非演出貼文，略過')

                # 記錄已處理（無論是否為演出，都不重複處理）
                new_processed.add(post_id)

            # 帳號間延遲 2 秒，降低 IG 封鎖風險
            await page.wait_for_timeout(2000)

        await browser.close()

    # ── 合併演出資料（依 date+venue+name 去重）───────────────────────────────
    existing_keys = {(e['date'], e['venue'], e['name']) for e in events}
    added = 0
    for ev in new_events:
        key = (ev['date'], ev['venue'], ev['name'])
        if key not in existing_keys:
            events.append(ev)
            existing_keys.add(key)
            added += 1

    # ── 儲存結果 ──────────────────────────────────────────────────────────────
    processed_ids.update(new_processed)
    save_processed_ids(processed_ids)
    save_events(events)

    print()
    print('╔══════════════════════════════════════════╗')
    print(f'║  完成！新增 {added} 筆演出（已去重）')
    print(f'║  events.json 共 {len(events)} 筆資料')
    print(f'║  已處理貼文總計 {len(processed_ids)} 篇')
    print('╚══════════════════════════════════════════╝')
    print()


if __name__ == '__main__':
    asyncio.run(main())
