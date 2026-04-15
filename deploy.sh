#!/bin/bash
# deploy.sh - 將 events.json 更新推送到 GitHub Pages
#
# 使用方式：bash deploy.sh
# 需要事先設定好 git remote（指向 GitHub 的 livehouse repo）

# 切換到腳本所在的目錄（確保 git 指令在正確的 repo 內執行）
cd "$(dirname "$0")"

echo ""
echo "📤 準備推送到 GitHub Pages..."

# ── 確認 git remote 是否設定 ────────────────────────────────────────────────
if ! git remote get-url origin &>/dev/null; then
    echo "❌ 錯誤：尚未設定 git remote origin"
    echo "   請執行以下指令設定（替換 YOUR_USERNAME）："
    echo "   git remote add origin https://github.com/YOUR_USERNAME/livehouse.git"
    exit 1
fi

# ── 加入所有變更並 commit ────────────────────────────────────────────────────
# 只加入必要的檔案，避免意外提交敏感資料（如 .env、ig_session.json）
git add events.json livehouse_calendar.html

# 確認是否有變更可以 commit
if git diff --staged --quiet; then
    echo "ℹ️  events.json 沒有變更，略過 commit"
else
    # 以當天日期作為 commit 訊息
    COMMIT_MSG="update events $(date +'%Y-%m-%d %H:%M')"
    git commit -m "$COMMIT_MSG"
    echo "✅ Commit：$COMMIT_MSG"
fi

# ── 推送到 GitHub ────────────────────────────────────────────────────────────
echo "⬆️  推送到 GitHub..."
if git push origin main 2>&1; then
    echo "✅ 推送成功！"
    echo ""
    echo "🌐 GitHub Pages 網址（約 1 分鐘後更新）："
    REMOTE_URL=$(git remote get-url origin)
    # 從 remote URL 解析出 GitHub Pages 網址
    REPO_PATH=$(echo "$REMOTE_URL" | sed 's|.*github.com[:/]||' | sed 's|\.git$||')
    OWNER=$(echo "$REPO_PATH" | cut -d'/' -f1)
    REPO=$(echo "$REPO_PATH" | cut -d'/' -f2)
    echo "   https://${OWNER}.github.io/${REPO}/"
else
    echo "❌ 推送失敗，請確認："
    echo "   1. 是否已設定 SSH key 或 personal access token"
    echo "   2. 是否有 livehouse repo 的寫入權限"
    exit 1
fi
