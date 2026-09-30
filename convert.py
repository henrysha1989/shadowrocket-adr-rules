import datetime
import os
import re


def convert_adh_to_sr(input_file, output_file):
    if not os.path.exists(input_file):
        print(f"File {input_file} not found.")
        return

    stamp = datetime.datetime.now().astimezone().strftime('%Y-%m-%d %H:%M %z')
    sr_rules = [
        "# ====================================================",
        "# Auto-generated Shadowrocket Ruleset from AdGuard Home",
        f"# updated: {stamp}",
        "# ====================================================\n"
    ]

    with open(input_file, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    # 原样透传开关：AdGuard 注释 '! RAW-BEGIN' / '! RAW-END' 之间的行按 Shadowrocket
    # 语法**原样**写入输出（用于 REJECT-DROP、DOMAIN-KEYWORD 等 AdGuard 语法表达不了的规则）。
    # 2026-09-27：没有这个开关时，这类行会被下面的分支静默丢弃。
    raw_mode = False
    raw_pat = re.compile(r'^[A-Z][A-Z0-9-]*,')
    for line in lines:
        line = line.strip()
        if not line:
            continue
        upper = line.lstrip('!').strip().upper()
        if upper == 'RAW-BEGIN':
            raw_mode = True
            sr_rules.append('# ==== 原样规则（RAW 区，Shadowrocket 语法）====')
            continue
        if upper == 'RAW-END':
            raw_mode = False
            continue
        if raw_mode and raw_pat.match(line):
            sr_rules.append(line)
            continue
        # keep section / description comments (AdGuard '!' -> Shadowrocket '#')
        if re.match(r'^!\s*updated\b', line):
            continue
        if line.startswith('!'):
            body = line[1:].strip()
            sr_rules.append(f"# {body}" if body else "#")
            continue
        if line.startswith('#'):
            sr_rules.append(line)
            continue

        if line.startswith('@@||'):
            raw_domain = line[4:].rstrip('^').strip()
            if '*' in raw_domain:
                keyword = raw_domain.replace('*', '').strip('.')
                sr_rules.append(f"DOMAIN-KEYWORD,{keyword},DIRECT")
            else:
                sr_rules.append(f"DOMAIN-SUFFIX,{raw_domain},DIRECT")
            continue

        if line.startswith('||'):
            raw_domain = line[2:].rstrip('^').strip()
            if '*' in raw_domain:
                parts = raw_domain.split('*')
                valid_parts = [p for p in parts if p and p != '.']
                if valid_parts:
                    sr_rules.append(f"DOMAIN-KEYWORD,{valid_parts[0]},REJECT")
            else:
                sr_rules.append(f"DOMAIN-SUFFIX,{raw_domain},REJECT")
            continue

    with open(output_file, 'w', encoding='utf-8') as f:
        f.write('\n'.join(sr_rules) + '\n')
    print(f"Conversion finished: {output_file}")


if __name__ == '__main__':
    # ⚠️ 2026-09-30 起**停用**：reject-custom.list 改由 `adh_gist_sync.py --sr-analyze`
    #   从手机 db 直接生成（手工区 = 原 hongguo-ad.list 的红果/番茄专表内容；
    #   自动区 = 漏网之鱼，动作自动判定 REJECT / REJECT-DROP）。
    #   本脚本若继续转换，每次推 adh-custom.txt 都会把那边写的内容整体冲掉。
    #   convert_adh_to_sr() 原样保留（要恢复只需把下面这行换成调用），README 计数不受影响。
    convert_adh_to_sr  # noqa: B018 - 保留引用，避免 linter 误报未使用
    print("convert.py: reject-custom.list 的生成已于 2026-09-30 停用"
          "（改由 adh_gist_sync.py --sr-analyze 写）")
