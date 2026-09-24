#!/usr/bin/env python3
"""Refresh the "当前规则量" line in README.md from the generated rule lists.

Counts DOMAIN-* lines in reject-custom.list / direct-custom.list / proxy-custom.list
and rewrites the README summary line in place. Idempotent.

2026-09-24（owner 需求）：每个分类后面可以带一个由**同步脚本**写的「（+a/-b）」标记，
表示本次同步的增删。本脚本**原样保留**它 —— 计数归 CI、增删归脚本，两边各管一段，
不会互相当成噪声擦掉。标记缺失时行为与旧版完全一致（不生成括号）。
"""
import os
import re

TARGETS = [
    ("拦截", "reject-custom.list"),
    ("直连", "direct-custom.list"),
    ("代理", "proxy-custom.list"),
]
README = "README.md"
PATTERN = r"^> 当前规则量：.*$"
DELTA = r"（\+\d+/-\d+）"


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
    if new == text:
        print("rule counts unchanged:", line)
        return
    open(README, "w", encoding="utf-8").write(new)
    print("rule counts updated:", line)


if __name__ == "__main__":
    main()
