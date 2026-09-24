#!/usr/bin/env python3
"""Refresh the "当前规则量" line in README.md from the generated rule lists.

Counts DOMAIN-* lines in reject-custom.list / direct-custom.list / proxy-custom.list
and rewrites the README summary line in place. Idempotent.

2026-09-24（owner 需求）：每个分类后面可以带一个由**同步脚本**写的「（+a/-b）」标记，
表示本次同步的增删。本脚本**原样保留**它 —— 计数归 CI、增删归脚本，两边各管一段，
不会互相当成噪声擦掉。标记缺失时行为与旧版完全一致（不生成括号）。

同时在计数行下面维护一行「更新时间」（CI 跑一次 = 规则真的变了一次，所以这就是
最近一次规则变更时间；时区取 runner 的本地时区，workflow 里已固定 TZ=Asia/Shanghai）。
"""
import os
import re
from datetime import datetime

TARGETS = [
    ("拦截", "reject-custom.list"),
    ("直连", "direct-custom.list"),
    ("代理", "proxy-custom.list"),
]
README = "README.md"
PATTERN = r"^> 当前规则量：.*$"
TS_PATTERN = r"^> 更新时间：.*$"
DELTA = r"（\+\d+/-\d+）"


def stamp():
    now = datetime.now().astimezone()
    return "> 更新时间：" + now.strftime("%Y-%m-%d %H:%M:%S（UTC%z）")


def count(path):
    if not os.path.exists(path):
        return 0
    with open(path, encoding="utf-8") as f:
        return sum(1 for line in f if line.startswith("DOMAIN-"))


def keep_delta(old_line, label):
    """取出上一版行里该分类的（+a/-b）标记；没有（或格式不符）就返回空串。"""
    m = re.search(rf"{re.escape(label)} \*\*\d+\*\* 条({DELTA})", old_line)
    return m.group(1) if m else ""


def main():
    counts = [(label, count(path)) for label, path in TARGETS]

    if not os.path.exists(README):
        print("README.md not found; nothing to do.")
        return
    text = open(README, encoding="utf-8").read()
    m = re.search(PATTERN, text, flags=re.M)
    old_line = m.group(0) if m else ""
    line = "> 当前规则量：" + " / ".join(
        f"{label} **{c}** 条{keep_delta(old_line, label)}" for label, c in counts) + "。"

    new, n = re.subn(PATTERN, lambda _m: line, text, flags=re.M)
    if n == 0:
        print("count line not found in README; nothing to do.")
        return

    # 更新时间行：有就改写，没有就插在计数行下面。
    ts = stamp()
    if re.search(TS_PATTERN, new, flags=re.M):
        new, _ = re.subn(TS_PATTERN, lambda _m: ts, new, flags=re.M)
    else:
        m2 = re.search(PATTERN, new, flags=re.M)
        new = new[:m2.end()] + "\n" + ts + new[m2.end():]

    if new == text:
        print("rule counts unchanged:", line)
        return
    open(README, "w", encoding="utf-8").write(new)
    print("rule counts updated:", line)
    print("updated at:", ts)


if __name__ == "__main__":
    main()
