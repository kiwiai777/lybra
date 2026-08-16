#!/usr/bin/env bash
# AIPOS-R6M 大项B: 安装 governance-pre-commit 到治理仓 .git/hooks/pre-commit
#
# 注意: executor 对治理仓只读(AGENTS.md 红线②), 无权写 .git/hooks/。
# 本脚本由顾问/Owner 在治理仓侧执行(治理仓 git 根, 非产品仓)。
#
# 用法:
#   bash tools/hooks/install-governance-pre-commit.sh [治理仓根路径]
#
#   不传参时从当前目录向上找 .git (在治理仓内任意目录执行即可)。
#   若治理仓根不是 /home/kiwi/ai-project-os, 请显式传参。

set -euo pipefail

HOOK_SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/governance-pre-commit"

if [ ! -f "$HOOK_SRC" ]; then
    echo "❌ 找不到 hook 源文件: $HOOK_SRC" >&2
    exit 1
fi

GOV_REPO="${1:-}"
if [ -z "$GOV_REPO" ]; then
    GOV_REPO="$(git rev-parse --show-toplevel 2>/dev/null || true)"
fi

if [ -z "$GOV_REPO" ] || [ ! -d "$GOV_REPO/.git" ]; then
    echo "❌ 无法定位治理仓 .git (用法: bash $0 <治理仓根路径>)" >&2
    exit 1
fi

TARGET="$GOV_REPO/.git/hooks/pre-commit"

# 备份旧 hook (R6A 版), 便于回滚
if [ -f "$TARGET" ]; then
    cp "$TARGET" "$TARGET.bak.$(date +%Y%m%d_%H%M%S)"
    echo "ℹ️  已备份旧 hook → ${TARGET}.bak.*"
fi

cp "$HOOK_SRC" "$TARGET"
chmod +x "$TARGET"
echo "✅ 已安装 governance-pre-commit → $TARGET"
