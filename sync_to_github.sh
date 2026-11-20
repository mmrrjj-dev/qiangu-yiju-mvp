#!/usr/bin/env bash
set -euo pipefail

# 《千古一局：暗合推演》一键同步推送至私有仓库 https://github.com/mmrrjj-dev/qiangu-yiju-mvp

REMOTE_URL="https://github.com/mmrrjj-dev/qiangu-yiju-mvp.git"
COMMIT_MSG="${1:-chore: sync v3.0.0 build, docs, art assets, and handoff}"

if [ ! -f ".gitignore" ]; then
  echo "[ERROR] 请在解压后的仓库根目录（包含 .gitignore 与 HANDOFF.md）下运行此脚本。"
  exit 1
fi

if [ ! -d ".git" ]; then
  git init -b main
fi

if git ls-files --error-unmatch server/.env .env >/dev/null 2>&1; then
  echo "[SECURITY ABORT] 检测到 .env 已被 Git 追踪，请先执行 git rm --cached .env！"
  exit 1
fi

git add .
if git diff --cached --quiet; then
  echo ">>> 工作区无新变更需要提交。"
else
  git commit -m "$COMMIT_MSG"
fi

if ! git remote get-url origin >/dev/null 2>&1; then
  git remote add origin "$REMOTE_URL"
else
  git remote set-url origin "$REMOTE_URL"
fi

echo ">>> 正在推送全量文件至 $REMOTE_URL (branch: main)..."
git push -u origin main
echo ">>> 同步完成！仓库地址: https://github.com/mmrrjj-dev/qiangu-yiju-mvp"
