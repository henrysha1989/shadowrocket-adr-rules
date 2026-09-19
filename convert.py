import os

def convert_adh_to_sr(input_file, output_file):
    if not os.path.exists(input_file):
        print(f"File {input_file} not found.")
        return

    sr_rules = [
        "# ====================================================",
        "# Auto-generated Shadowrocket Ruleset from AdGuard Home",
        "# ====================================================\n"
    ]

    with open(input_file, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    for line in lines:
        line = line.strip()
        # 跳过空行和注释行
        if not line or line.startswith(('!', '#')):
            continue

        # 1. 白名单处理 (@@||domain^) -> DIRECT
        if line.startswith('@@||'):
            raw_domain = line[4:].rstrip('^').strip()
            if '*' in raw_domain:
                keyword = raw_domain.replace('*', '').strip('.')
                sr_rules.append(f"DOMAIN-KEYWORD,{keyword},DIRECT")
            else:
                sr_rules.append(f"DOMAIN-SUFFIX,{raw_domain},DIRECT")
            continue

        # 2. 拦截处理 (||domain^) -> REJECT
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
    convert_adh_to_sr('adh-custom.txt', 'reject-custom.list')
