#!/bin/bash
# run_all.command - 台北 Livehouse 節目彙整系統｜手動執行版
#
# 使用方式：在 Finder 中雙擊此檔案即可執行完整流程
# 執行順序：讀取 .env → 執行爬蟲 → git push 到 GitHub Pages
#
# 首次使用前請確認：
#   1. 在同資料夾建立 .env 檔，填入 ANTHROPIC_API_KEY=sk-ant-...
#   2. 已安裝套件：pip3 install playwright anthropic && playwright install chromium
#   3. 已設定 git remote：git remote add origin https://github.com/YOUR_USERNAME/livehouse.git

# ── 切換到腳本所在目錄 ────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# ── 建立 logs 資料夾（若不存在）──────────────────────────────────────────────
mkdir -p "$SCRIPT_DIR/logs"

# 將本次執行記錄到 log 檔
LOG_FILE="$SCRIPT_DIR/logs/run_$(date +'%Y%m%d_%H%M%S').log"
exec > >(tee -a "$LOG_FILE") 2>&1

echo ""
echo "╔══════════════════════════════════════════════╗"
echo "║   台北 Livehouse 節目彙整系統                ║"
echo "║   手動執行                                   ║"
echo "╚══════════════════════════════════════════════╝"
echo "開始時間：$(date +'%Y-%m-%d %H:%M:%S')"
echo ""

# ── 讀取 .env 環境變數 ────────────────────────────────────────────────────────
if [ -f "$SCRIPT_DIR/.env" ]; then
    # 逐行讀取，忽略註解行和空行
    while IFS='=' read -r key value; do
        [[ "$key" =~ ^#.*$ ]] && continue
        [[ -z "$key" ]] && continue
        export "$key"="$value"
    done < "$SCRIPT_DIR/.env"
    echo "✅ 已載入 .env 設定"
else
    echo "⚠️  找不到 .env 檔，將使用系統環境變數"
fi

# 確認 API Key 存在
if [ -z "$ANTHROPIC_API_KEY" ]; then
    echo ""
    echo "❌ 錯誤：找不到 ANTHROPIC_API_KEY"
    echo "   請在 $SCRIPT_DIR/.env 加入："
    echo "   ANTHROPIC_API_KEY=sk-ant-..."
    echo ""
    read -p "按 Enter 關閉視窗..."
    exit 1
fi

# ── 確認並啟用虛擬環境 ───────────────────────────────────────────────────────
VENV_PYTHON="$SCRIPT_DIR/.venv/bin/python3"

if [ ! -f "$VENV_PYTHON" ]; then
    echo "❌ 找不到虛擬環境，請先執行："
    echo "   cd \"$SCRIPT_DIR\""
    echo "   python3 -m venv .venv"
    echo "   source .venv/bin/activate"
    echo "   pip install playwright anthropic"
    echo "   playwright install chromium"
    echo ""
    read -p "按 Enter 關閉視窗..."
    exit 1
fi

source "$SCRIPT_DIR/.venv/bin/activate"
PYTHON="$VENV_PYTHON"
echo "✅ Python：$($PYTHON --version)（.venv 虛擬環境）"

# ── 步驟 1：執行爬蟲 ──────────────────────────────────────────────────────────
echo ""
echo "────────────────────────────────────────────────"
echo "步驟 1／2：抓取 Instagram 貼文並辨識演出資訊"
echo "────────────────────────────────────────────────"
echo ""

"$PYTHON" "$SCRIPT_DIR/scraper.py"
SCRAPER_EXIT=$?

if [ $SCRAPER_EXIT -ne 0 ]; then
    echo ""
    echo "❌ 爬蟲執行失敗（exit code: $SCRAPER_EXIT）"
    echo "   請查看上方錯誤訊息，或檢查 logs/ 資料夾"
    echo ""
    read -p "按 Enter 關閉視窗..."
    exit 1
fi

# ── 步驟 2：推送到 GitHub Pages ───────────────────────────────────────────────
echo ""
echo "────────────────────────────────────────────────"
echo "步驟 2／2：推送更新到 GitHub Pages"
echo "────────────────────────────────────────────────"
echo ""

bash "$SCRIPT_DIR/deploy.sh"
DEPLOY_EXIT=$?

# ── 完成 ─────────────────────────────────────────────────────────────────────
echo ""
if [ $DEPLOY_EXIT -eq 0 ]; then
    echo "╔══════════════════════════════════════════════╗"
    echo "║  ✅ 全部完成！                               ║"
    echo "╚══════════════════════════════════════════════╝"
else
    echo "╔══════════════════════════════════════════════╗"
    echo "║  ⚠️  爬蟲完成，但 GitHub 推送失敗            ║"
    echo "║     請手動執行 deploy.sh                     ║"
    echo "╚══════════════════════════════════════════════╝"
fi

echo "結束時間：$(date +'%Y-%m-%d %H:%M:%S')"
echo "記錄檔：$LOG_FILE"
echo ""
read -p "按 Enter 關閉視窗..."
