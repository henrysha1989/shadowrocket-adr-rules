#!/bin/sh
# main 分支的 Shadowrocket 规则表 → mihomo/Clash classical 文本规则集。
#   - 丢掉注释行与空行
#   - 丢掉第 3 列的策略（REJECT-DROP / DIRECT / PROXY ...）
#   - IP-CIDR 保留 no-resolve
# 用法：cd 仓库根目录 && sh mihomo/convert.sh
set -eu

REPO=https://raw.githubusercontent.com/henrysha1989/shadowrocket-adr-rules/main
cd "$(dirname "$0")"

for f in bytedance-ad soul-ad reject-custom; do
    curl -fsSL "$REPO/$f.list" | awk -F, '
        /^[[:space:]]*(#|$)/ { next }
        $1 == "IP-CIDR"      { printf "%s,%s,no-resolve\n", $1, $2; next }
        NF >= 2              { printf "%s,%s\n", $1, $2 }
    ' > "$f.list.tmp"
    mv "$f.list.tmp" "$f.list"
    printf '%s.list  %s 条\n' "$f" "$(wc -l < "$f.list")"
done
