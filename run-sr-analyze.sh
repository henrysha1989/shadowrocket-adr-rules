#!/bin/sh
# 小火箭侧分析 —— 定时任务 wrapper（宿主 cron 用）
#
# 安装（root）：
#   install -m 755 sr/run-sr-analyze.sh /usr/local/sbin/sr-analyze.sh
#   printf '*/30 * * * * root /usr/local/sbin/sr-analyze.sh >> /var/log/sr-analyze.log 2>&1\n' > /etc/cron.d/sr-analyze
#   chmod 644 /etc/cron.d/sr-analyze
#
# 说明：root 跑才能读 000 权限的手机 db；REPO_TOKEN 由脚本自己从
#       /vol1/1000/Docker/deepseek-harness/workspace/.env（或 sr/sr.env）取。
set -eu
WS=/vol1/1000/Docker/deepseek-harness/workspace
cd "$WS"
exec /usr/bin/python3 "$WS/sr/sr_analyze.py" "$@"
