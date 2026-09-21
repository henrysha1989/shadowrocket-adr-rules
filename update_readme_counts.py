#!/usr/bin/env python3
"""Refresh the "当前规则量" line in README.md from the generated rule lists.

Counts DOMAIN-* lines in reject-custom.list / direct-custom.list / proxy-custom.list
and rewrites the README summary line in place. Idempotent.
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


def count(path):
    if not os.path.exists(path):
        return 0
    with open(path, encoding="utf-8") as f:
        return sum(1 for line in f if line.startswith("DOMAIN-"))


def main():
    counts = [(label, count(path)) for label, path in TARGETS]
    line = "> 当前规则量：" + " / ".join(f"{label} **{c}** 条" for label, c in counts) + "。"

    if not os.path.exists(README):
        print("README.md not found; nothing to do.")
        return
    text = open(README, encoding="utf-8").read()
    new, n = re.subn(PATTERN, line, text, flags=re.M)
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
