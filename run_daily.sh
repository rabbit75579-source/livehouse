#!/bin/bash
# run_daily.sh - 台北 Livehouse 節目彙整系統｜自動排程版
#
# 此腳本由 launchd 每天 08:00 自動呼叫，不含互動式 read -p。
# 執行結果會寫入 logs/daily_YYYYMMDD.log

# ── 切換到腳本所在目錄 ────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# ── 建立 logs 資料夾（若不存在）──────────────────────────────────────────────
mkdir -p "$SCRIPT_DIR/logs"

# 每日 log 檔（同一天多次執行會 append 到同一個檔）
LOG_FILE="$SCRIPT_DIR/logs/daily_$(date +'%Y%m%d').log"
exec >> "$LOG_FILE" 2>&1

echo ""
echo "==== 自動排程執行 $(date +'%Y-%m-%d %H:%M:%S') ===="

# ── 讀取 .env 環境變數 ────────────────────────────────────────────────────────
if [ -f "$SCRIPT_DIR/.env" ]; then
    while IFS='=' read -r key value; do
        [[ "$key" =~ ^#.*$ ]] && continue
        [[ -z "$key" ]] && continue
        export "$key"="$value"
    done < "$SCRIPT_DIR/.env"
fi

if [ -z "$ANTHROPIC_API_KEY" ]; then
    echo "❌ 找不到 ANTHROPIC_API_KEY，終止執行"
    exit 1
fi

# ── 補充 PATH（launchd 環境 PATH 較短）───────────────────────────────────────
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"

# ── 啟用虛擬環境（直接使用 .venv，避免系統 Python 版本問題）────────────────
VENV_PYTHON="$SCRIPT_DIR/.venv/bin/python3"

if [ ! -f "$VENV_PYTHON" ]; then
    echo "❌ 找不到 .venv，請先在專案目錄執行："
    echo "   python3 -m venv .venv && source .venv/bin/activate"
    echo "   pip install playwright anthropic && playwright install chromium"
    exit 1
fi

source "$SCRIPT_DIR/.venv/bin/activate"
PYTHON="$VENV_PYTHON"

# ── 執行爬蟲 ──────────────────────────────────────────────────────────────────
echo "▶ 執行爬蟲..."
"$PYTHON" "$SCRIPT_DIR/scraper.py"
SCRAPER_EXIT=$?

if [ $SCRAPER_EXIT -ne 0 ]; then
    echo "❌ 爬蟲失敗（exit code: $SCRAPER_EXIT）"
    exit 1
fi

# ── 推送到 GitHub Pages ───────────────────────────────────────────────────────
echo "▶ 推送到 GitHub..."
bash "$SCRIPT_DIR/deploy.sh"

echo "==== 完成 $(date +'%H:%M:%S') ===="
echo ""
