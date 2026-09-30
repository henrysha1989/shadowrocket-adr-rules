#!/usr/bin/env python3
"""小火箭侧分析（**独立项目**）—— 手机 `proxy-*.db` → 体检报告 + 三张自建表。

与 ADH 侧**完全无关**：不读 ADH 的 querylog / user_rules / 过滤清单 / FORCE_DIRECT，
不需要 ADH 的地址与口令，也不写 `adh-custom.txt`。ADH 那边是另一个脚本
（`adh_gist_sync.py`，宿主 cron `0 */4 * * *` 调用）——两边各自独立、互不引用。

## 你要什么（owner 2026-10-01 定）
上传 db ⇒ 我要三件事：
  1. **拦截的漏网之鱼**：参考黑名单判广告、但手机实际没拦（放行了）的主机
  2. **直连域名滑落到代理**：
     ① 已在直连表里、手机却走了代理 ⇒ **规则没生效**（查缓存/顺序/冲突）
     ② 分类器判直连、却没在直连表里 ⇒ **需要新增直连规则**（否则会一直走代理）
  3. **新增 reject 时的冲突护栏**：任何要写进拦截表的域，先跟直连表（含手工区）与
     `SR_FORCE_DIRECT` 对撞；冲突的**一律不写**并在报告里列出来 ——
     绝不能让"拦广告"把正常上网的直连域给拦了。

## 数据源（唯一）

**手机导出的 `proxy-*.db`，落在固定目录里**（默认 `/vol1/1001/shadowrocket-db/`，glob `*.db`）：

- 脚本每轮**扫这个目录**，按「文件名 → size:mtime」签名（`sr/.sr-files.json`）判断哪些是**新库/变化过的库**；
- 只有一个新库时：只分析它；有一批：一起分析（跨库按主机合并，速率用各库自己的窗口分别算）；
- **没有新库就什么都不做**（不写仓库、不发通知、不落档）。

## 用法

```sh
# ① 无人值守（定时任务用这个）：扫新库 → 分析 → 落档 → 写三张表 → 推 GitHub
python3 sr_analyze.py

# ② 只看报告，绝不写仓库（人工核对用）
python3 sr_analyze.py --report-only

# ③ 常驻轮询（不想配 cron 时用；--interval 秒）
python3 sr_analyze.py --watch --interval 300

# 其它
python3 sr_analyze.py --db <某个.db>        # 只分析指定库（调试）
python3 sr_analyze.py --all                # 忽略签名，全部重扫
python3 sr_analyze.py --dry-run            # 算增删但不推仓库
python3 sr_analyze.py --force              # 越过"删太多"的安全阀（>30% 默认中止）
python3 sr_analyze.py --no-reject          # 不动拦截表（只写 direct/proxy）
python3 sr_analyze.py --auto-all           # 激进：所有"漏网之鱼"都自动写（默认只写高置信：信号族/命中≥50）
python3 sr_analyze.py --report-dir <目录>   # 报告落档位置（默认 sr/reports/）
python3 sr_analyze.py --selftest
```

**落档**：每轮写 `sr/reports/sr-report-<时间戳>.md`，并同步一份最新到 `sr/last-report.md`；
无人值守跑在 cron 里时，stdout 就是日志（cron 会邮寄/丢弃，建议重定向到文件）。

## 定时（三选一，推荐第一种）

```sh
# ① 宿主 cron（与 ADH 那套同一个套路；root 跑，好读 .env 与 000 权限的 db）
#    /etc/cron.d/sr-analyze  ← 内容一行：
*/30 * * * * root /usr/local/sbin/sr-analyze.sh >> /var/log/sr-analyze.log 2>&1
#    wrapper /usr/local/sbin/sr-analyze.sh：
#!/bin/sh
cd /vol1/1000/Docker/deepseek-harness/workspace && exec /usr/bin/python3 sr/sr_analyze.py

# ② systemd timer（等价，略）
# ③ 常驻：python3 sr/sr_analyze.py --watch --interval 300（放 supervisor/docker 里）
```

脚本内有**单实例锁**（`sr/.sr-lock`）：定时任务重叠时后一个直接退出，不会两个进程一起改仓库。

状态文件（都在 `sr/` 下）：`.sr-files.json`（已消费的 db 签名）、`.sr-state.json`（主机证据池）。
凭据只从环境变量 / `sr/sr.env` 读（`REPO_TOKEN`），**不读** ADH 的 `.env`。
"""

import base64
import glob
import glob as _glob
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

DEFAULTS = {
    # ── 仓库（三张自建表）───────────────────────────────────────────────
    "REPO": "henrysha1989/shadowrocket-adr-rules",
    "REPO_BRANCH": "main",
    "REPO_TOKEN": "",                      # 从环境 / sr.env 读；不写进仓库
    "REPO_REJECT_PATH": "reject-custom.list",
    # ── 联动：配置仓库（shadowrocket-config）。列表里每条动作**必须**与配置里那一行的
    #    集合动作一致 —— 因为小火箭的 `RULE-SET,url,ACTION` 会用 ACTION 覆盖列表内每条动作
    #    （2026-09-30 的重试风暴就是这么来的：列表里写 DROP、配置行写成 REJECT ⇒ 全部变普通 REJECT）。
    "CONFIG_REPO": "henrysha1989/shadowrocket-config",
    "CONFIG_FILES": "shadowrocket-白名单.通用版.conf,shadowrocket-白名单.测试版.conf",
    "RAW_PREFIX": "https://git.521989.xyz/https://raw.githubusercontent.com/",
    "REPO_DIRECT_PATH": "direct-custom.list",
    "REPO_PROXY_PATH": "proxy-custom.list",
    # ── 数据源 ─────────────────────────────────────────────────────────
    "SR_DB_DIR": "/vol1/1001/shadowrocket-db",   # ★ 数据源目录（手机导出的 proxy-*.db 落这里）
    "SR_DB_GLOB": "*.db",
    "SR_INTERVAL": "300",                        # --watch 的轮询间隔（秒）
    # ── 判据 ───────────────────────────────────────────────────────────
    "SR_MIN_HITS": "3",                    # 低于这次数的域名不参与（噪声）
    "SR_TTL_DAYS": "90",                   # 证据池保留期
    "SR_DROP_MIN_HITS": "50",              # 库内命中 ≥ 此数 ⇒ 用 REJECT-DROP
    "SR_AUTO_REJECT_MIN_HITS": "50",        # ★ 自动写拦截表的门槛：命中 ≥ 此数、或命中信号族名，才**自动写**；
                                            #   低频/存疑的只进报告（"候选，等你定"），不写死 —— 避免误伤 CDN
    "SR_REJECT_WRITE": "1",                # 无人值守默认把**漏网之鱼**写进拦截表（带冲突护栏）
    # 信号/埋点**族名**优先于域名族（否则同族在不同域名下一边 DROP 一边直连）
    "SR_SIGNAL_PATTERNS": "-misc-lf,-misc-lq,-applog,live-player-log,-ad-sign,reading-ad,ads-normal,telemetry",
    "SR_DROP_PATTERNS": "-ad-sign,ads-normal,reading-ad,reading-sign,applog,telemetry,-log,-misc-,live-player-log,logbk",
    # 小火箭侧「强制直连」登记表（后缀匹配）：命中的域跳过广告判定、并写进直连表。
    # 这里是 owner 明确批准过的"别拦/直连"意图；**只属于本项目**，与 ADH 无关。
    "SR_FORCE_DIRECT": (
        "ecombdapi.com,tesla.com,tesla.cn,teslamotors.com,teslamotors.cn,"
        "megarobo.com,megarobo.tech,megarobo.info,yunmart.com,"
        "polaris3-normal-gl2.zijieapi.com,polaris3-normal-hl.zijieapi.com,"
        "polaris3-normal-lf.zijieapi.com,polaris3-normal-lq.zijieapi.com,"
        "polaris3-normal-xh.zijieapi.com,polaris3-normal-zb.zijieapi.com,"
        "polaris5-normal-gl2.zijieapi.com,polaris5-normal-hl.zijieapi.com,"
        "polaris5-normal-lf.zijieapi.com,polaris5-normal-lq.zijieapi.com,"
        "polaris5-normal-xh.zijieapi.com,polaris5-normal-zb.zijieapi.com,"
        "mssdk3-normal-hj.zijieapi.com,mssdk3-normal-hl.zijieapi.com,"
        "mssdk3-normal-lf.zijieapi.com,"
        "mum.alibabachengdun.com,mum.alibabachengdun.net,"
        "szlong.weixin.qq.com,amdc.m.taobao.com,"
        "apd-pcdnwxlogin.teg.tencent-cloud.net"
    ),
    # ── 参考黑名单（判"是不是广告"用的公开清单，**不是 ADH 的数据**）──────
    "ADLIST_ENABLE": "1",
    "ADLIST_URLS": "https://git.521989.xyz/https://raw.githubusercontent.com/hagezi/dns-blocklists/main/wildcard/multi.txt",
    "ADLIST_CACHE_HOURS": "24",
    # ── 写库安全阀 ─────────────────────────────────────────────────────
    "SAFETY_MIN_DROP": "5",
    "SAFETY_MAX_DROP_PCT": "30",
}

PROTECTED = [
    "reading", "novel", "douyin", "snssdk", "byteimg",
    "volccdn", "apple", "zijieapi", "bytegecko", "ecombdimg",
    # 2026-09-25：遥测词上线后实测会命中 `log.fnnas.com`（飞牛 NAS 自家服务域）→ 加豁免，
    # 避免把 NAS 的云端/远程访问埋点当广告拦掉。
    "fnnas",
]

MULTI_TLD = {
    "com.cn", "net.cn", "org.cn", "gov.cn", "edu.cn", "com.hk", "net.hk",
    "org.hk", "com.tw", "com.au", "co.uk", "org.uk", "co.jp", "co.kr",
    "com.sg", "com.my",
}

_SR_HOST_RE = re.compile(r"^[a-z0-9]([a-z0-9\-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9\-]*[a-z0-9])?)+$")

ADLIST = set()
EXEMPT = set()


def load_env(path=None):
    """读 KEY=VALUE；真实环境变量优先。

    查找顺序：`$SR_ENV_FILE` → `sr/sr.env` → 兜底 `$ROOT/.env`。
    兜底那一步**只为省掉你再配一份 `REPO_TOKEN`**（你原来跑的脚本就是读它），
    本项目**只取 `REPO_TOKEN`**，不读任何 ADH 相关的键（ADH_URL/USER/PASS 一律不碰）。
    想彻底独立：在 `sr/sr.env` 里写一行 `REPO_TOKEN=...` 即可，兜底自然失效。"""
    cands = [path] if path else []
    cands += [os.environ.get("SR_ENV_FILE"), os.path.join(HERE, "sr.env"), os.path.join(ROOT, ".env")]
    for p in [c for c in cands if c]:
        try:
            with open(p, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    k = k.strip()
                    if k != "REPO_TOKEN" and p != os.path.join(HERE, "sr.env") and p != os.environ.get("SR_ENV_FILE"):
                        continue          # 兜底文件里只认 REPO_TOKEN
                    os.environ.setdefault(k, v.strip().strip('"').strip("'"))
        except OSError:
            continue


AD_RE = re.compile("|".join([
    # 2026-09-25 审计：`\.dsp\.` / `-dsp\.` 是纯冗余 —— re.search 子串匹配下 `dsp\.` 已覆盖
    # 二者；实测（3562 allowed + 709 blocked）两条的增量命中均为 0，故删除。`pangle`/`zztfly`/
    # `aiclk` 本次两集合均 0 命中，但属家族特征（穿山甲等），保留作未来覆盖。
    r"dsp\.",
    r"pangolin", r"pangle", r"dailygn",
    r"ecombdapi", r"zztfly",
    # 2026-09-20: 'ydycdn' REMOVED - it is 亿点云计算(珠海) 的 edge/PCDN CDN (SaaS CDN),
    # not an ad domain; blocking it throttled apps (phone hit *.ydycdn.com 285x) and
    # matched no external ad list. Re-add only with real evidence.
    r"ugsdk", r"aiclk", r"analytics",
    # 2026-09-25（审计后 owner 同意加）：通用遥测/埋点子域。**只匹配整段标签**（锚定 `(^|\.)…(\.|$)`），
    # 避免 09-21 那种「子串命中误伤 CDN」的坑（例：`scdn.co` 命中 `xhscdn.com`）。
    # 实测影响（3,562 allowed 域名）：ad 16→41、proxy 31→9 —— 多出的是 22 个 `*.metric.gstatic.com`
    # 加 wechatpay/qq/microsoft 各 1；`fnnas.com` 同时进 PROTECTED 豁免（飞牛自家服务域）。
    r"(^|\.)(log|logs|log[0-9]+|mon|mon[0-9]+|monitor[0-9]*|metric|metrics|stat|stats|stat[0-9]+"
    r"|track|tracker|tracking|report|reports|collect|beacon|pixel|telemetry|event|events)(\.|$)",
]))


DIRECT_RE = re.compile("|".join([
    r"rtcxyz\.com", r"volccdn\.com", r"bytevcloud\.com",
    r"amemv\.com", r"zijieapi\.com", r"snssdk\.com",
    r"douyinvod\.com", r"douyinpic\.com",
]))


_PROXY_DOMAINS = [
    "google.com", "googlevideo.com", "gstatic.com", "googleapis.com",
    "youtube.com", "ytimg.com", "ggpht.com",
    "openai.com", "chatgpt.com", "anthropic.com", "claude.ai",
    "github.com", "githubusercontent.com", "githubassets.com",
    "telegram.org", "t.me", "twitter.com", "x.com", "twimg.com",
    "facebook.com", "fbcdn.net", "instagram.com", "cdninstagram.com",
    "whatsapp.net", "whatsapp.com", "netflix.com", "nflxvideo.net", "nflximg.net",
    "wikipedia.org", "wikimedia.org", "reddit.com", "redditstatic.com",
    "discord.com", "discordapp.com", "spotify.com", "scdn.co",
]


PROXY_RE = re.compile("|".join(r"(^|\.)%s($|\.)" % re.escape(d) for d in _PROXY_DOMAINS))


def host_ok(host):
    """A hostname usable in rules/logs: non-empty, no wildcard/space, no NUL byte and no
    literal `\\000` escape (ADH/手机日志里偶发 `ffvl\\000w-...` 这类非法名字). 2026-09-26."""
    return bool(host) and not any(c in host for c in ("*", " ", "\x00", "\\"))


def in_domset(host, domset):
    """True if host equals, or is a subdomain of, any entry in domset."""
    host = host.lower()
    parts = host.split(".")
    return any(".".join(parts[i:]) in domset for i in range(len(parts)))


def is_suspected(domain):
    """Ad-looking domain that is not on the do-not-touch allowlist."""
    return bool(AD_RE.search(domain)) and not any(k in domain for k in PROTECTED)


def _signal_patterns():
    """`SR_SIGNAL_PATTERNS` -> 小写子串列表（埋点/信号族名）。每次现算，避免测试里改 env 不生效。"""
    return [p.strip().lower() for p in cfg("SR_SIGNAL_PATTERNS").split(",") if p.strip()]


def classify(domain):
    """Return "direct", "ad", "proxy", or None.

    Precedence: reference-blocklist match (ad/privacy) > **signal-family name (ad)** > direct
    > ad-pattern > proxy. Domains under a manual-section domain (EXEMPT) are never auto-rejected."""
    d = domain.lower()
    # EXEMPT 用后缀匹配：FORCE_DIRECT / 手工区父域的**子域**同样豁免（如 polaris.zijieapi.com
    # 覆盖 polaris5-normal-zb.zijieapi.com）。2026-09-21：修直播误杀时引入。
    if ADLIST and in_domset(d, ADLIST) and not in_domset(d, EXEMPT):
        return "ad"
    # 🎯 2026-09-30 owner「准确」：信号/埋点**族名**优先于域名族（见 SR_SIGNAL_PATTERNS 注释）。
    # 放在 DIRECT_RE 之前，是为了让 `mon*-misc` / `-applog` / `live-player-log` 这类
    # 无论挂在哪个 CDN 域名下都判 ad —— 而不是"挂在字节系域名上就直连"。
    if not in_domset(d, EXEMPT):
        for p in _signal_patterns():
            if p in d:
                return "ad"
    if DIRECT_RE.search(d):
        return "direct"
    if is_suspected(d):
        return "ad"
    if PROXY_RE.search(d):
        return "proxy"
    return None


def load_adlist():
    """Fetch (and cache) the reference ad/privacy blocklists; return a suffix set.

    Graceful degradation: on fetch failure fall back to the cached copy; if none,
    return an empty set so the built-in AD_RE patterns still work (no hard failure)."""
    if not cfg_bool("ADLIST_ENABLE", True):
        return set()
    urls = [u.strip() for u in cfg("ADLIST_URLS").split(",") if u.strip()]
    if not urls:
        return set()
    cache = os.path.join(HERE, ".adlist-cache.txt")
    meta_path = os.path.join(HERE, ".adlist-cache.json")
    hours = float(cfg("ADLIST_CACHE_HOURS") or 24)
    try:
        meta = json.load(open(meta_path, encoding="utf-8"))
    except Exception:  # noqa: BLE001
        meta = {}
    if (meta.get("urls") == urls and os.path.exists(cache)
            and (time.time() - meta.get("ts", 0)) < hours * 3600):
        with open(cache, encoding="utf-8") as fh:
            return {l.strip() for l in fh if l.strip()}
    entries, ok = set(), False
    print(f"adlist cache stale/missing -> downloading {len(urls)} list(s) "
          f"(~4 MB, through the accelerator) ...", flush=True)
    for u in urls:
        status, text = http("GET", u, timeout=60, headers={"User-Agent": "Mozilla/5.0"})
        if status != 200:
            print(f"adlist fetch failed: {u} -> HTTP {status}")
            continue
        ok = True
        for line in text.splitlines():
            s = line.strip().lower()
            if not s or s[0] in "!#[":
                continue
            if s.startswith("*."):
                s = s[2:]
            if s:
                entries.add(s)
    if not ok or not entries:
        if os.path.exists(cache):
            print("adlist refresh failed; using cached copy")
            with open(cache, encoding="utf-8") as fh:
                return {l.strip() for l in fh if l.strip()}
        print("adlist unavailable; built-in patterns only")
        return set()
    try:
        with open(cache, "w", encoding="utf-8") as fh:
            fh.write("\n".join(sorted(entries)) + "\n")
        with open(meta_path, "w", encoding="utf-8") as fh:
            json.dump({"ts": time.time(), "urls": urls}, fh)
    except Exception:  # noqa: BLE001
        pass
    print(f"adlist loaded: {len(entries)} entr(ies)")
    return entries


def prune_subsumed(domset, label="reject"):
    """Drop entries that are a subdomain of another entry in the same set: the broader rule
    already covers them (DOMAIN-SUFFIX matches subdomains), so they are pure duplicates.
    Lossless. 2026-09-20."""
    out = set()
    for d in sorted(domset, key=lambda x: (x.count("."), x)):
        if not any(d.endswith("." + o) for o in out):
            out.add(d)
    dropped = len(domset) - len(out)
    if dropped:
        print(f"subsume: dropped {dropped} redundant {label} entr(ies) already covered by a parent rule")
    return out


def prune_shadowed(domset, higher, label=""):
    """跨清单遮蔽过滤：丢掉那些**父域在更高优先级清单里**的条目。

    为什么需要它（2026-09-27 发现）：`prune_subsumed` 只在**同一集合内**做父域塌缩，
    所以像 `csi.gstatic.com`（父域 `gstatic.com` 在 proxy）不会被它清掉。
    但配置里清单顺序是 reject → proxy → direct，SHADOW 清单在前 ⇒ 这些 direct 条目
    **永远命中不到**，是死规则（实测 5 条：csi.gstatic.com / t.e.x.com /
    analytics.pgncs.notion.so / exp.notion.so / intake-analytics.wikimedia.org）。

    安全性：只影响**仓库写入**；`OWNER_ALLOW`（白名单）用的是未过滤的集合，
    所以 ADH 侧这些域仍然解析正常、不会被重新拦截。"""
    out = {d for d in domset if not any(d.endswith("." + h) for h in higher)}
    dropped = len(domset) - len(out)
    if dropped:
        print(f"shadow: dropped {dropped} {label} entr(ies) 被更高优先清单的父域遮蔽（死规则）")
    return out


def repo_manual_keywords(owner, name, token, branch, path):
    """read `DOMAIN-KEYWORD,x,ACTION` lines from a list file's manual region."""
    st, tx = http("GET", f"https://api.github.com/repos/{owner}/{name}/contents/{urllib.parse.quote(path)}"
                         + (f"?ref={branch}" if branch else ""),
                  {"Authorization": f"token {token}", "Accept": "application/vnd.github+json"})
    if st != 200:
        return set()
    try:
        content = base64.b64decode(json.loads(tx)["content"]).decode()
    except Exception:  # noqa: BLE001
        return set()
    man, _ = split_manual(content, "#")
    out = set()
    for line in man:
        parts = [x.strip() for x in line.split(",")]
        if len(parts) >= 3 and parts[0] == "DOMAIN-KEYWORD":
            out.add(parts[1].lower())
    return out


def repo_manual_domains_of(owner, name, token, branch, path, comment="#"):
    """指定仓库文件「手工区」（标记之上）里的域名集合。

    只用于「跳过 owner 手工已明确处理的域」这类判断 —— 读失败返回空集，**不参与任何收敛**。"""
    headers = {"Authorization": f"token {token}", "Accept": "application/vnd.github+json"}
    api = f"https://api.github.com/repos/{owner}/{name}/contents/{path}"
    status, text = http("GET", api + (f"?ref={branch}" if branch else ""), headers)
    if status != 200:
        return set()
    manual, _ = split_manual(base64.b64decode(json.loads(text)["content"]).decode(), comment)
    return parse_domains("\n".join(manual))


def repo_manual_allow_domains(owner, name, token, branch=""):
    """owner 手工写在**放行清单**（`direct-custom.list` / `proxy-custom.list`）里的域名。

    ⚠️ 必须与 `repo_manual_domains()` 区分开：后者读的是三份文件的全部手工区，
    **把「放行」和「拦截」混在一起**，调用方却当"人工区拦截"用 ——
    结果 owner 手工写的放行条目会被并进 reject（2026-09-27 owner 指出）。

    本函数只收放行意图；读失败就 loud-fail（返回空集会让放行被忽略）。"""
    def content(path):
        headers = {"Authorization": f"token {token}", "Accept": "application/vnd.github+json"}
        api = f"https://api.github.com/repos/{owner}/{name}/contents/{path}"
        last = ""
        for _ in range(3):
            status, text = http("GET", api + (f"?ref={branch}" if branch else ""), headers)
            if status == 200:
                return base64.b64decode(json.loads(text)["content"]).decode()
            if status == 404:
                return ""
            last = f"HTTP {status} {text[:120]}"
            time.sleep(2)
        sys.exit(f"repo manual-allow read failed: {path} -> {last}")
    out = set()
    for path in (cfg("REPO_DIRECT_PATH"), cfg("REPO_PROXY_PATH")):
        manual, _ = split_manual(content(path), "#")
        out |= parse_domains("\n".join(manual))
    return out


def base_domain(host):
    """Collapse a hostname to its registrable base domain (e.g. a.b.douyinvod.com -> douyinvod.com)."""
    parts = host.strip(".").split(".")
    if len(parts) <= 2:
        return host
    if ".".join(parts[-2:]) in MULTI_TLD:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def cfg(key):
    return os.environ.get(key) or DEFAULTS.get(key, "")


def cfg_bool(key, default=False):
    """Strict boolean flag: '1/true/yes/on' -> True; anything else ('0/false/no/off' or an
    empty value) -> False. Use for feature toggles -- `cfg()` returns raw strings and '0'
    is truthy, so `cfg()` cannot disable anything. A value present in the environment (or
    .env) is authoritative even when empty, so `X=""` / `X="0"` now really disables X.
    2026-09-21."""
    v = os.environ[key] if key in os.environ else DEFAULTS.get(key)
    if v is None:
        return default
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def http(method, url, headers=None, body=None, timeout=30):
    headers = dict(headers or {})
    # ⚠️ 必须带 UA：加速站（Cloudflare）对 urllib 默认 UA(`Python-urllib/3.x`) 直接 403
    headers.setdefault("User-Agent", "dsh-rules-sync/1.0")
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def sr_extract_host(url):
    """Hostname from a Shadowrocket log `url` value. '' for IP/non-domain/empty.

    The `logging.url` column holds a bare host or `host:port` (TCP/UDP), or a full URL
    (some HTTP rows). Raw IP literals are dropped -- they carry no rule value."""
    if not url:
        return ""
    u = url.strip()
    if "://" in u:
        u = u.split("://", 1)[1]
    u = re.split(r"[/?#]", u, 1)[0]
    if "@" in u:
        u = u.rsplit("@", 1)[1]
    if u.startswith("["):
        return ""                                    # IPv6 literal
    if ":" in u:
        u = u.split(":", 1)[0]                       # strip port
    u = u.strip(".").lower()
    if not u or u.replace(".", "").isdigit():        # IPv4 / numeric
        return ""
    return u if _SR_HOST_RE.match(u) else ""


def cfg_csv(key):
    """逗号配置项 -> 小写集合。"""
    return {x.strip().lower() for x in cfg(key).split(",") if x.strip()}


def _sr_ts(s):
    """'YYYY-MM-DD HH:MM:SS'（主机本地时钟）→ epoch 秒。"""
    try:
        return datetime.strptime(str(s)[:19], "%Y-%m-%d %H:%M:%S").timestamp()
    except Exception:  # noqa: BLE001
        return None


def _sr_open(path):
    """只读打开导出库：只读优先；只读挂载（无 -shm/-wal 可写）上退回 immutable=1。"""
    con = None
    for uri in (f"file:{path}?mode=ro", f"file:{path}?mode=ro&immutable=1"):
        try:
            con = sqlite3.connect(uri, uri=True)
            con.execute("select 1 from logging limit 1")
            return con
        except sqlite3.Error:
            if con is not None:
                con.close()
            con = None
    raise sqlite3.OperationalError(f"unable to open database file: {path}")


def shadowrocket_evidence(path):
    """读一个手机日志库 → (每主机证据, 会话时长分钟)。

    证据 = {"hits": n, "verdicts": Counter(DIRECT/PROXY/REJECT), "rule": 最后一次命中的规则原文}。
    ⚠️ 与已删除的 shadowrocket_domains() 不同：这里**不**分桶、**不**写证据池，
    只产出「手机实际怎么处理的」事实，供 --sr-analyze 判定。"""
    con = _sr_open(path)
    try:
        try:
            rows = con.execute(
                "select c0url, c2result, c3type, c4created from logging_content").fetchall()
        except sqlite3.Error:      # 更老的导出库没有 logging_content
            rows = [(u, r, t, None) for u, r, t in
                    con.execute("select url, result, type from logging").fetchall()]
    finally:
        con.close()
    per, ts = {}, []
    for url, rule, typ, created in rows:
        h = sr_extract_host(url)
        if not h:
            continue
        e = per.get(h)
        if e is None:
            e = {"hits": 0, "verdicts": Counter(), "rule": ""}
            per[h] = e
        e["hits"] += 1
        e["verdicts"][(typ or "?").upper()] += 1
        if rule:
            e["rule"] = str(rule)
        t = _sr_ts(created) if created is not None else None
        if t is not None:
            ts.append(t)
    span = (max(ts) - min(ts)) / 60.0 if len(ts) > 1 else 0.0
    return per, span


def sr_pick_action(host, hits, span):
    """自动判定「漏网之鱼」该用哪种拦截动作 → (action, 理由)。

    REJECT-DROP（丢包）留给**重试/信号型**的域：实测普通 REJECT 会让广告/埋点 SDK 秒级重连
    （重试风暴），DROP 让 App 等超时、重试量少约两个数量级；其余用普通 REJECT（失败更快）。
    判据（按优先级，都可复核）：
      ① 命中 `SR_DROP_PATTERNS`（信号/埋点族：-ad-sign / applog / reading-ad / -log / mon*-misc …）
      ② 库内命中次数 ≥ `SR_DROP_MIN_HITS`（默认 50）
         ⚠️ 用**绝对次数**而不是"次/分"：手机导出的库时长差别很大（实测有 0.2 分也有 617 分的），
            按分钟算会把长库里的热点稀释掉（同一族的域在 10 小时库里只有 0.2 次/分）。
      ③ 其余 → REJECT。"""
    for p in cfg_csv("SR_DROP_PATTERNS"):
        if p in host:
            return "REJECT-DROP", f"信号/埋点族（模式 `{p}`）"
    floor = int(float(cfg("SR_DROP_MIN_HITS") or 50))
    if hits >= floor:
        return "REJECT-DROP", f"库内高频（{hits} 次 ≥ 门槛 {floor}）"
    return "REJECT", f"低频（{hits} 次 < 门槛 {floor}）"


def _state_load(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:  # noqa: BLE001 - 缺文件/坏了都当空
        return {}


def _state_save(path, state):
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(state, fh, ensure_ascii=False)
    except Exception as e:  # noqa: BLE001
        print(f"sr-analyze: 状态写入失败 ({e})")


def sr_db_state_load(path):
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:  # noqa: BLE001
            return {}
    return {}


def sr_db_state_save(path, state):
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(state, fh, ensure_ascii=False, indent=1, sort_keys=True)
    except Exception as e:  # noqa: BLE001
        print(f"shadowrocket: state save failed ({e})")


def shadowrocket_scan(directory, glob_pat, state):
    """Return (todo_paths, sig_by_path) for DB exports new or changed since last run.

    `state` maps basename -> "size:mtime"; only new/changed files are returned, so already
    ingested exports are not re-read. Missing/unreadable dir yields nothing (no hard fail).

    The host sees this share as `/vol01/...` while the dsh container mounts it as `/vol1/...`
    (same files, different mount names) -- retry the sibling spelling so the same script works
    from both sides. 2026-09-27."""
    cands = [directory]
    for a, b in (("/vol01/", "/vol1/"), ("/vol1/", "/vol01/")):
        if directory.startswith(a):
            cands.append(b + directory[len(a):])
    for d in cands:
        if d and os.path.isdir(d):
            directory = d
            break
    else:
        print(f"shadowrocket: directory not found -> {directory!r}")
        return [], {}
    todo, sigs = [], {}
    files = sorted(glob.glob(os.path.join(directory, glob_pat)))
    for p in files:
        try:
            st = os.stat(p)
        except OSError:
            continue
        sig = f"{st.st_size}:{int(st.st_mtime)}"
        sigs[p] = sig
        if state.get(os.path.basename(p)) != sig:
            todo.append(p)
    print(f"shadowrocket: {len(files)} db file(s), {len(todo)} new/changed")
    return todo, sigs


def parse_domains(text):
    """Collect domains from an AdGuard (`||d^`) or Shadowrocket (`DOMAIN-X,d,ACTION`) file.

    ⚠️ 2026-09-30 修：动作原先写成 `([A-Z]+)$`，**不认带连字符的 `REJECT-DROP`**
    ⇒ hongguo-ad.list / adh-custom.txt RAW 区那些 DROP 规则一直被静默忽略
    （manual_dom 为空 ⇒ 手工区去重失效、写入护栏也形同虚设）。改成 `[A-Z][A-Z-]*`。
    仍**不**识别裸 `DOMAIN,host,ACTION`（只有 `DOMAIN-XXX`）—— 既有行为，本轮不动；
    RAW 区那几条裸 DOMAIN 靠 `raw_domains` 单独保护，不依赖这里。"""
    out = set()
    for line in text.splitlines():
        s = line.strip()
        if not s or s[0] in "!#" or s.startswith("@@||"):
            continue
        if s.startswith("||"):
            d = s[2:].rstrip("^").strip()
            if d and "*" not in d:
                out.add(d)
        else:
            # ⚠️ 2026-09-30 补：**裸 `DOMAIN,host,ACTION` 也要认**（原来只认 `DOMAIN-XXX,`）。
            #    漏认的代价实测可见：`reject-custom.list` 手工区那条 `DOMAIN,i.snssdk.com,REJECT-DROP`
            #    因此不算"手工管辖" ⇒ `--sr-analyze` 每轮都把它报成「疑似误伤 i.snssdk.com ×184」，
            #    而这其实是 owner 2026-09-29 用 CLIENT_DROP_ONLY 配方**故意**做的客户端丢包（只报不写，但吵）。
            #    注意方向：多认出来的都是**手工区**的域名 ⇒ 只会让分析更保守（跳过、不自动删）。
            m = re.match(r"^DOMAIN(-[A-Z]+)?,[^,]+,([A-Z][A-Z-]*)$", s)
            # `DOMAIN-KEYWORD`（以及将来的 DOMAIN-REGEX）的值是**关键字**不是域名，正则却一样匹配
            # ⇒ 会把 `tesla` / `reading-ad` 当域名塞进 EXEMPT/OWNER_ALLOW，并让 ADH 收到
            # `@@||tesla^$important` 这种无意义的放行。列表里已开始用关键字，在这里挡掉。
            if m and s.split(",", 1)[0] not in ("DOMAIN-KEYWORD", "DOMAIN-REGEX"):
                out.add(s.split(",")[1])
    return out


AUTO_MARK = {
    "#": "# ===== 自动收集（以下内容由脚本管理，勿手改）=====",
    "!": "! ===== 自动收集（以下内容由脚本管理，勿手改）=====",
}


def split_manual(existing, comment):
    """Split a list file into (manual lines, auto lines) around the AUTO marker.

    FAIL-SAFE: if the marker is missing (legacy file, or someone edited it away) the WHOLE
    file is treated as MANUAL and nothing is converged. The opposite direction (whole file =
    auto) silently deleted hand-curated rules on 2026-09-19; it is never taken again."""
    mark = AUTO_MARK.get(comment)
    lines = existing.splitlines()
    if mark:
        stripped = [l.strip() for l in lines]
        if mark in stripped:
            i = stripped.index(mark)
            return lines[:i], lines[i + 1:]
    return lines, []


def guard_drop(label, before, removed_n):
    """Refuse a runaway deletion: abort if a write would remove >= SAFETY_MIN_DROP entries
    AND more than SAFETY_MAX_DROP_PCT% of the existing set, unless --force is given."""
    pct = float(cfg("SAFETY_MAX_DROP_PCT") or 30)
    floor = int(float(cfg("SAFETY_MIN_DROP") or 5))
    if not (removed_n >= floor and removed_n > before * pct / 100.0):
        return
    msg = f"{label}: would drop {removed_n}/{before} entries (>{pct:g}%)"
    if "--dry-run" in sys.argv:
        print(f"\u26a0\ufe0f  [dry-run] {msg} - a real run would ABORT here (use --force to allow)")
        return
    if "--force" in sys.argv:
        print(f"\u26a0\ufe0f  {msg} (--force)")
        return
    sys.exit(f"\u26d4 {msg}. Refusing (safety valve). Re-run with --force if intended.")


def repo_sync_set(owner, name, path, token, desired, line_fmt="||{d}^", comment="!",
                  branch="", dry_run=False):
    """Converge only the AUTO section of a repo file to `desired` (add new, drop stale);
    the manual section above the marker is preserved verbatim. Header refreshed on change."""
    headers = {"Authorization": f"token {token}", "Accept": "application/vnd.github+json"}
    api = f"https://api.github.com/repos/{owner}/{name}/contents/{path}"
    status, text = http("GET", api + (f"?ref={branch}" if branch else ""), headers)
    if status == 200:
        meta = json.loads(text)
        sha = meta["sha"]
        existing = base64.b64decode(meta["content"]).decode()
    elif status == 404:
        sha, existing = None, ""
    else:
        sys.exit(f"repo read failed: HTTP {status} {text[:200]}")

    manual, auto = split_manual(existing, comment)
    manual = [l.rstrip() for l in manual
              if l.strip() and not re.match(r"^[!#]\s*(updated|auto-added)\b", l.strip())]
    have = parse_domains("\n".join(auto))
    manual_dom = parse_domains("\n".join(manual))
    # ⚠️ 2026-09-27 加的护栏：AUTO 标记缺失时 split_manual 会 fail-safe 把**整个文件**当人工区，
    # 于是 auto 为空 ⇒ 本函数变成"只追加、永不删除"，而且**静默无声**。
    # 实测 `reject-custom.list` 就是这种状态（它是 CI 从 adh-custom.txt 转换生成的，用 `#` 前缀，
    # 从不带我们期望的 `! ===== 自动收集…` 标记）⇒ 想从里面删 kv501… 永远删不掉。
    # 这里做两件事：① 检出这种情况就**拒绝写入**（不追加 494 条把文件搞成 1000+ 行）；
    # ② 明确打印是哪个文件、该怎么修。生成的 list 本该由 CI 重新生成，脚本不该去收敛它。
    # ⚠️ 2026-09-30 修：判据原先用 `not auto`，但「标记在、自动区还是空的」同样满足 `not auto`
    #    ⇒ **首次给一个新文件建自动区时会被自己挡住**（hongguo-ad.list 就是这么卡住的；
    #    那 3 个老文件自动区早已非空，所以这个坑一直没暴露）。改成**真的去找标记行**。
    has_mark = bool(AUTO_MARK.get(comment)) and \
        AUTO_MARK[comment] in [l.strip() for l in existing.splitlines()]
    if not has_mark and existing.strip() and have == set() and manual_dom:
        print(f"repo_sync_set: ⚠️ {path} 缺 AUTO 标记（`{comment} ===== 自动收集…`）——"
              f"整个文件被当作人工区，本函数无法收敛（只追加不删除）。")
        print(f"repo_sync_set: ⚠️ 跳过 {path} 的写入以免重复追加（该文件应由上游重新生成）")
        return [], []
    want = {d for d in desired if d not in manual_dom}
    added, removed = sorted(want - have), sorted(have - want)
    guard_drop(path, len(have), len(removed))
    mark = AUTO_MARK.get(comment, "")
    # line_fmt 可以是模板串，也可以是**可调用**（--sr-analyze 需要每个域自带动作：
    # 同一个 reject 清单里 REJECT 与 REJECT-DROP 混排）。2026-09-30
    if callable(line_fmt):
        fmt = lambda doms: sorted(line_fmt(d) for d in doms)
    else:
        fmt = lambda doms: sorted(line_fmt.format(d=d) for d in doms)
    body_old = "\n".join(manual + ([mark] if mark else []) + fmt(have))
    body_new = "\n".join(manual + ([mark] if mark else []) + fmt(want))
    if body_old == body_new:
        print(f"no change; {path} up to date")
        return [], []
    stamp = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %z")
    head = f"{comment} updated: {stamp}"
    content = head + ("\n" + body_new if body_new else "") + "\n"
    if dry_run:
        print(f"[dry-run] {path}: +{len(added)} / -{len(removed)}")
        return added, removed

    body = {"message": f"{path}: +{len(added)} / -{len(removed)} @ {stamp}",
            "content": base64.b64encode(content.encode()).decode()}
    if sha:
        body["sha"] = sha
    if branch:
        body["branch"] = branch
    status, text = http("PUT", api, headers, body)
    if status not in (200, 201):
        sys.exit(f"repo write failed: HTTP {status} {text[:200]}")
    print(f"{path}: +{len(added)} / -{len(removed)}")
    return added, removed


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def _rate(n, span):
    return f"{n / span:.2f}/分" if span else f"{n} 次"


_CONFIG_ORDERS = {}


def file_auto_domains(path):
    """线上某张表**自动区**里的域名集合（用来判断"规则已存在但手机没拦"）。"""
    url = f"{cfg('RAW_PREFIX')}{cfg('REPO')}/main/" + urllib.parse.quote(path)
    try:
        st, tx = http("GET", url)
    except Exception:  # noqa: BLE001
        return set()
    if st != 200:
        return set()
    man, auto = split_manual(tx, "#")
    return parse_domains("\n".join(auto))


def file_actions(path):
    """线上某张表里用到的动作集合（None = 读不到）。"""
    repo = cfg("REPO")
    url = f"{cfg('RAW_PREFIX')}{repo}/main/" + urllib.parse.quote(path)
    try:
        st, tx = http("GET", url)
    except Exception:  # noqa: BLE001
        return None
    if st != 200:
        return None
    acts = set()
    for line in tx.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(",")
        if len(parts) >= 3 and parts[0].startswith("DOMAIN"):
            acts.add(parts[-1].strip().upper())
    return acts


def config_rows():
    """从 shadowrocket-config 的两个配置里读出 `[Rule]` 的规则集动作。

    返回 (rows, problems)：
      rows     = {列表文件名: 动作}（两个配置若不一致，会记进 problems 并以**通用版**为准）
      problems = 人类可读的问题列表
    """
    repo, files = cfg("CONFIG_REPO"), [f.strip() for f in cfg("CONFIG_FILES").split(",") if f.strip()]
    per_file, seen = {}, {}
    for fn in files:
        # RAW_PREFIX 已含 `https://raw.githubusercontent.com/`，别再拼一遍
        url = f"{cfg('RAW_PREFIX')}{repo}/main/" + urllib.parse.quote(fn)
        try:
            st, tx = http("GET", url)
        except Exception as e:  # noqa: BLE001
            return {}, [f"配置读取失败 {fn}: {e}"]
        if st != 200:
            return {}, [f"配置读取失败 {fn}: HTTP {st}"]
        rows, order, idx, in_rule = {}, [], 0, False
        for line in tx.splitlines():
            line = line.split("#")[0].strip()
            if line.startswith("["):
                in_rule = line == "[Rule]"
                continue
            if not in_rule or not line:
                continue
            parts = [x.strip() for x in line.split(",")]
            if parts[0] in ("RULE-SET", "DOMAIN-SET") and len(parts) >= 3:
                nm = parts[1].split("/")[-1].split("?")[0]
                rows[nm] = parts[2].upper()
                idx += 1
                order.append((idx, nm, parts[2].upper()))
        per_file[fn] = rows
        _CONFIG_ORDERS[fn] = order
        for k, v in rows.items():
            seen.setdefault(k, {})[fn] = v
    problems = []
    for k, d in seen.items():
        vals = set(d.values())
        if len(vals) > 1:
            problems.append(f"两个配置对 `{k}` 的动作不一致：{d}")
    base = per_file.get(files[0], {})
    for k, d in seen.items():
        if k not in base:
            base[k] = list(d.values())[0]
    return base, problems


def check_config(rows=None, problems=None, state=None):
    """跨仓库一致性检查：列表内动作 = 配置里的集合动作；顺序安全；覆盖完整。"""
    rows, problems = (rows, list(problems or [])) if rows is not None else config_rows()
    notes = []
    if not rows:
        return problems or ["读不到配置，跳过一致性检查"]
    # ★ 真正要校验的不变式：**文件里每条的动作 = 配置里那一行的集合动作**。
    #   为什么：小火箭用集合动作覆盖列表内每条动作（2026-09-30 重试风暴就是这么来的）。
    #   所以这里直接读线上文件，把每条动作取出来对比 —— 而不是看状态池里"手机观测到什么"。
    for path in (cfg("REPO_REJECT_PATH"), cfg("REPO_DIRECT_PATH"), cfg("REPO_PROXY_PATH")):
        want = rows.get(path)
        if not want:
            if path == cfg("REPO_PROXY_PATH"):
                notes.append(f"`{path}` 未被配置引用 —— 属设计（FINAL,PROXY 兜底）；"
                             f"想显式控制就在配置里加一行")
            else:
                problems.append(f"配置里没有引用 `{path}`（规则写了却没人订阅）")
            continue
        acts = file_actions(path)
        if acts is None:
            notes.append(f"`{path}` 读不到，跳过动作校验")
        elif acts and acts != {want}:
            if want == "REJECT-DROP":
                notes.append(f"`{path}` 里有 {sorted(acts)}，配置行是 REJECT-DROP ⇒ "
                             f"实际全部按 DROP 执行（安全，但文件自身不自洽）")
            else:
                problems.append(f"`{path}` 里有 {sorted(acts)}，而配置行是普通 {want} ⇒ "
                                f"DROP 类条目会退化成 RST（重试风暴风险）")
        else:
            notes.append(f"`{path}` 动作一致 ✓（{want}）")
    if rows.get(cfg("REPO_REJECT_PATH")) != "REJECT-DROP":
        problems.append(f"拦截表的集合动作是 `{rows.get(cfg('REPO_REJECT_PATH'))}`——"
                        f"普通 REJECT 会让 SDK 秒级重连（实测 29,700 次/分），应为 REJECT-DROP")
    if rows.get(cfg("REPO_DIRECT_PATH")) != "DIRECT":
        problems.append(f"直连表的集合动作是 `{rows.get(cfg('REPO_DIRECT_PATH'))}`，应为 DIRECT")
    # 顺序：自建直连表必须排在拦截段（第一张 reject 表）之前，否则"自建放行"压不过订阅广告表
    for fn in [f.strip() for f in cfg("CONFIG_FILES").split(",") if f.strip()]:
        order = _CONFIG_ORDERS.get(fn) or []
        if not order:
            continue
        pos = {nm: i for i, nm, _ in order}
        d, r = pos.get(cfg("REPO_DIRECT_PATH")), pos.get(cfg("REPO_REJECT_PATH"))
        if d is None or r is None:
            continue
        if d > r:
            problems.append(f"{fn}：`direct-custom.list` 排在第 {d} 行、拦截表在第 {r} 行"
                            f"—— 顺序反了（自建放行必须排在拦截段之前）")
    for n in notes:
        print("   ℹ️ " + n)
    return problems


_OBVIOUS_AD = re.compile(
    r"^(ads?\d*|advert\w*|adx\d*|adsystem\w*|adservice\w*|adnxs\w*|adcolony\w*|admob\w*)[-.0-9]",
    re.I)
_OBVIOUS_AD_FAMILY = ("doubleclick", "googlesyndication", "adservice", "adsystem", "adnxs",
                      "applovin", "moloco", "bytedance.com/ads", "pangle", "unityads", "vungle",
                      "supersonicads", "ironsrc", "tapjoy", "chartboost", "mintegral", "adcolony",
                      "unity3d.com/ads", "criteo", "taboola", "outbrain")


def obvious_ad(h):
    """主机名一看就是广告（owner 2026-10-01：「明显的 ads 就写」）。"""
    if _OBVIOUS_AD.match(h):      # 对整串匹配：`ads3-normal-lf…` / `ads.example.com` 都算
        return True
    return any(f in h for f in _OBVIOUS_AD_FAMILY)


_LIST_CACHE = {}


def _load_list(url):
    if url not in _LIST_CACHE:
        try:
            st, tx = http("GET", url)
            _LIST_CACHE[url] = [l.split("#")[0].strip() for l in tx.splitlines()] if st == 200 else []
        except Exception:  # noqa: BLE001
            _LIST_CACHE[url] = []
    return _LIST_CACHE[url]


def chain_direct_matches(hosts):
    """把配置的规则链跑一遍（**跳过我们自己的 reject 表**），找出"会被判直连"的候选。

    owner 要求「新增 reject 时注意不能出现直连冲突导致无法正常上网」——上游直连表
    （China_Domain / ChinaMedia / Apple / Lan / STUN / Download…）都排在拦截段**之后**，
    所以我们的 reject 一进去就把它们压掉了。这里按配置顺序逐条匹配，命中 DIRECT 的记下来。
    """
    repo, fn = cfg("CONFIG_REPO"), [f.strip() for f in cfg("CONFIG_FILES").split(",") if f.strip()][0]
    url = f"{cfg('RAW_PREFIX')}{repo}/main/" + urllib.parse.quote(fn)
    try:
        st, tx = http("GET", url)
    except Exception:  # noqa: BLE001
        return {}
    if st != 200:
        return {}
    rules, in_rule = [], False
    for line in tx.splitlines():
        line = line.split("#")[0].strip()
        if line.startswith("["):
            in_rule = line == "[Rule]"
            continue
        if not in_rule or not line:
            continue
        p = [x.strip() for x in line.split(",")]
        if p[0] in ("RULE-SET", "DOMAIN-SET"):
            if cfg("REPO_REJECT_PATH") in p[1]:
                continue                      # 纯看"没有我们的拦截表时"会怎样
            act = p[2].upper() if len(p) >= 3 else ""
            for l in _load_list(p[1]):
                q = [x.strip() for x in l.split(",")]
                if p[0] == "DOMAIN-SET":
                    if q[0]:
                        rules.append(("DOMAIN-SUFFIX", q[0].lstrip(".").lower(), act))
                elif len(q) >= 2 and q[0] in ("DOMAIN", "DOMAIN-SUFFIX", "DOMAIN-KEYWORD"):
                    rules.append((q[0], q[1].lstrip(".").lower(), act))
        elif p[0] in ("DOMAIN", "DOMAIN-SUFFIX", "DOMAIN-KEYWORD") and len(p) >= 3:
            rules.append((p[0], p[1].lstrip(".").lower(), p[2].upper()))
        elif p[0] == "FINAL" and len(p) >= 2:
            rules.append(("FINAL", "", p[1].upper()))
    out = {}
    for h in hosts:
        for t, v, act in rules:
            if t == "FINAL":
                break
            if (t == "DOMAIN" and h == v) or (t == "DOMAIN-SUFFIX" and (h == v or h.endswith("." + v))) \
                    or (t == "DOMAIN-KEYWORD" and v in h):
                if act == "DIRECT":
                    out[h] = v
                break
    return out


def report(mins, hosts, reject_cand, direct_cand, slipped_rule, slipped_new, conflicts, extra,
           processed=(), wrote=(), finished=None, stale_phone=()):
    """打印 + 返回 markdown（调用方负责落档）。"""
    L = []

    def p(s=""):
        print(s)
        L.append(s)

    p(f"# 小火箭 db 体检 —— {_now().strftime('%Y-%m-%d %H:%M:%S')} CST")
    p()
    p(f"- 数据源目录：`{cfg('SR_DB_DIR')}`（glob `{cfg('SR_DB_GLOB')}`）")
    p(f"- 本轮处理的库：{('、'.join(processed) if processed else '无新库')}")
    p(f"- 窗口 {mins:.1f} 分 · 域名事件 {extra['events']} · 主机 {len(hosts)} · 被拒主机 {len(extra['rej_hosts'])} 个")
    p(f"- 动作分布：DIRECT {extra['act']['DIRECT']} / REJECT {extra['act']['REJECT']} / PROXY {extra['act'].get('PROXY', 0)}")
    p(f"- 重试速率：被拒 {extra['rej_total'] / mins:.2f}/分（判据 ≤5/分/主机）")
    p()
    p(f"## ① 漏网之鱼（判广告 + 手机没拦）：{len(reject_cand)} 条")
    p()
    for h, o in sorted(reject_cand, key=lambda x: -x[1]["n"])[:40]:
        p(f"- `{h}` {o['n']} 次（{_rate(o['n'], mins)}）→ {o['act']}"
          + ("  ← 自动写" if o.get("auto") else "  ← 低频，只报不写")
          + (f"；{o['note']}" if o.get("note") else ""))
    p()
    if stale_phone:
        p(f"## ⏳ 规则已存在、但手机没拦（多半是规则集没刷新）：{len(stale_phone)} 条")
        p()
        for h, _v, why in sorted(stale_phone, key=lambda x: -sum(x[1].values()))[:20]:
            p(f"- `{h}` ← 已由「{why}」覆盖")
        p()
    p(f"## ② 直连域名滑落到代理")
    p()
    p(f"### ① 已在直连表、手机却走代理（规则没生效）：{len(slipped_rule)} 条")
    p()
    for h, o in sorted(slipped_rule, key=lambda x: -x[1]["proxy"])[:40]:
        p(f"- `{h}` {o['n']} 次（代理 {o['proxy']} : 直连 {o['direct']}）")
    p()
    p(f"### ② 判直连但不在直连表（需新增，否则一直走代理）：{len(direct_cand)} 条")
    p()
    for h, o in sorted(direct_cand, key=lambda x: -x[1]["n"])[:40]:
        p(f"- `{h}` {o['n']} 次（代理 {o['proxy']} : 直连 {o['direct']}）")
    p()
    p(f"## ③ 冲突护栏：与直连表/放行表冲突、**拒绝写入 reject** 的域：{len(conflicts)} 条")
    p()
    for d, why in conflicts[:40]:
        p(f"- `{d}` ← {why}")
    if not conflicts:
        p("- 无冲突 ✓")
    if wrote:
        p()
        p("## 落盘 / 推送")
        p()
        for line in wrote:
            p(f"- {line}")
    if finished is not None:
        p()
        p(f"（本轮耗时 {finished:.1f}s）")
    return "\n".join(L)


CST = timezone(timedelta(hours=8))


def _now():
    """统一用**北京时间** —— 容器是 UTC、宿主机是 CST，混着写日志会看不懂。"""
    return datetime.now(CST)


def heartbeat(msg):
    """每轮都留一行痕迹 —— 否则"没有新库就什么都不做"会让定时任务完全无痕、无法验证。"""
    try:
        os.makedirs(os.path.join(HERE, "reports"), exist_ok=True)
        who = "cron/root" if os.geteuid() == 0 else f"uid{os.geteuid()}"
        with open(os.path.join(HERE, "reports", "heartbeat.log"), "a", encoding="utf-8") as fh:
            fh.write(f"{_now().strftime('%Y-%m-%d %H:%M:%S')} CST  [{who}]  {msg}\n")
    except OSError:
        pass


def _lock(path):
    """单实例锁：定时任务重叠时直接退出，别两个进程一起改仓库。"""
    try:
        import fcntl
        fh = open(path, "w")
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fh.write(str(os.getpid()))
        fh.flush()
        return fh
    except OSError:
        return None
    except ImportError:
        return open(os.devnull, "w")


def run_once(opts):
    """跑一轮：扫新库 → 分析 → 落档 → 写表 → 推 GitHub。返回处理的库数。"""
    t0 = time.time()
    do_write = opts["write"]
    dry_run = opts["dry_run"]
    owner, name = cfg("REPO").split("/", 1)
    tok, br = cfg("REPO_TOKEN"), cfg("REPO_BRANCH")
    # 启动自检：即使这轮因配置不对而退出，心跳里也留下原因（cron 排障用）
    _chk = []
    if not os.path.isdir(cfg("SR_DB_DIR")):
        _chk.append(f"数据源目录不存在：{cfg('SR_DB_DIR')}")
    else:
        try:
            os.listdir(cfg("SR_DB_DIR"))
        except OSError as e:
            _chk.append(f"数据源目录打不开：{e}")
    if not tok:
        _chk.append("REPO_TOKEN 缺失（读不到三张表手工区，护栏会失真）")
    heartbeat("启动自检：" + ("全部通过" if not _chk else "；".join(_chk)))
    if _chk:
        print("⚠️ 自检问题：" + "；".join(_chk))
    # ⚠️ 没有 token 就是"半盲"跑：读不到三张表的手工区 ⇒ 冲突护栏、滑落判断都会失真。
    #    所以除非显式 --offline，一律要求 token（cron 以 root 跑时能从 .env 读到）。
    if not tok and not opts.get("offline"):
        sys.exit("REPO_TOKEN 缺失：读不到三张表的手工区，冲突护栏会失真。"
                 "\n  设环境变量 REPO_TOKEN、或写 sr/sr.env、或以 root 运行（可读 workspace/.env）。"
                 "\n  只想离线看看分类结果：加 --offline")

    global ADLIST, EXEMPT
    ADLIST = load_adlist()
    allow_direct = repo_manual_allow_domains(owner, name, tok, br) if tok else set()
    dir_manual = repo_manual_domains_of(owner, name, tok, br, cfg("REPO_DIRECT_PATH"), "#") if tok else set()
    rej_manual = repo_manual_domains_of(owner, name, tok, br, cfg("REPO_REJECT_PATH"), "#") if tok else set()
    rej_kw = repo_manual_keywords(owner, name, tok, br, cfg("REPO_REJECT_PATH")) if tok else set()
    if rej_kw:
        print(f"[..] 拦截表手工区关键字 {len(rej_kw)} 条：{', '.join(sorted(rej_kw))}")
    forced = cfg_csv("SR_FORCE_DIRECT") | dir_manual
    EXEMPT = set(allow_direct) | set(forced)

    state_path = os.path.join(HERE, ".sr-state.json")
    files_path = os.path.join(HERE, ".sr-files.json")
    files_state = {} if opts["all"] else sr_db_state_load(files_path)
    state = _state_load(state_path)
    if opts["db"]:
        todo, sigs = [opts["db"]], {}
    else:
        todo, sigs = shadowrocket_scan(cfg("SR_DB_DIR"), cfg("SR_DB_GLOB"), files_state)

    if not todo:
        print(f"[{time.strftime('%H:%M:%S')}] 无新库（{cfg('SR_DB_DIR')}）—— 什么都不做")
        heartbeat(f"无新库（{cfg('SR_DB_DIR')}）· 池 {len(state)} 条")
        return 0
    print(f"[{time.strftime('%H:%M:%S')}] 发现 {len(todo)} 个新/变化库，开始分析 ...")
    heartbeat(f"处理 {len(todo)} 个库：{', '.join(os.path.basename(p) for p in todo)}")

    min_hits = int(float(cfg("SR_MIN_HITS") or 3))
    span_by_db, stats = {}, {"events": 0, "act": Counter(), "rej_hosts": set(), "rej_total": 0}
    for path in todo:
        try:
            per, span = shadowrocket_evidence(path)
        except Exception as e:  # noqa: BLE001 - 半拷贝/被占用的库跳过，下轮重试
            print(f"   skip {os.path.basename(path)} ({e})")
            continue
        span_by_db[path] = span
        for h, e in per.items():
            stats["events"] += e["hits"]
            for k, v in e["verdicts"].items():
                stats["act"][k] += v
            if e["verdicts"].get("REJECT"):
                stats["rej_hosts"].add(h)
                stats["rej_total"] += e["verdicts"]["REJECT"]

    rej_auto = file_auto_domains(cfg("REPO_REJECT_PATH")) if tok else set()
    reject_cand, stale_phone, direct_cand, slipped_rule, slipped_new = [], [], [], [], []
    act_by_host = {}
    total_min = sum(span_by_db.values()) or 1.0
    old_kind = {h: (v[1] if isinstance(v, list) and len(v) >= 2 else "") for h, v in state.items()}
    for path, span in span_by_db.items():
        try:
            per, _ = shadowrocket_evidence(path)
        except Exception:
            continue
        for h, e in per.items():
            if e["hits"] < min_hits or in_domset(h, rej_manual):
                continue
            if any(k in h for k in rej_kw):
                stale_phone.append((h, ver, "族关键字"))
                continue
            if in_domset(h, rej_auto):
                stale_phone.append((h, ver, "拦截表自动区"))
                continue
            ver = e["verdicts"]
            kind = "direct" if in_domset(h, forced) else classify(h)
            act, why = sr_pick_action(h, e["hits"], span)   # ⚠️ 返回 (动作, 依据) —— 别把元组当动作写进规则
            act_by_host[h] = act
            o = {"n": e["hits"], "direct": ver.get("DIRECT", 0), "proxy": ver.get("PROXY", 0),
                 "reject": ver.get("REJECT", 0), "kind": kind, "act": act, "why": why}
            if kind is None:
                # "分类器没意见" 不是删除理由（可能只是参考清单没收录）
                state[h] = [time.time(), old_kind.get(h, ""), act]
            else:
                state[h] = [time.time(), kind, act]
            if kind == "ad" and not o["reject"]:
                reject_cand.append((h, o))
            elif kind == "direct" and o["proxy"] > o["direct"]:
                # ⚠️ 只认"分类器判直连"或"已在直连表"的；`kind is None` + 走代理 = FINAL,PROXY 的正常结果，
                #    不是滑落（那类域名本来就没规则，走代理是设计行为）
                in_list = in_domset(h, forced) or in_domset(h, dir_manual)
                (slipped_rule if in_list else slipped_new).append((h, o))
                direct_cand.append((h, o))

    # ★ 族一致性：统计每个基础域下的主机判定。整个族**全是广告** ⇒ 无歧义，可自动写；
    #   族里混着正常服务（如 zijieapi.com 下既有广告埋点也有 gecko/ma 这类接口）⇒ 只报等你定。
    fam = {}
    for _p, _span in span_by_db.items():
        try:
            _per, _ = shadowrocket_evidence(_p)
        except Exception:
            continue
        for _h, _e in _per.items():
            if _e["hits"] < min_hits or in_domset(_h, rej_manual):
                continue
            _k = "direct" if in_domset(_h, forced) else classify(_h)
            fam.setdefault(base_domain(_h), []).append(_k)
    pure_ad = {b for b, ks in fam.items() if ks and all(k == "ad" for k in ks)}

    kept = {h: v for h, v in state.items() if isinstance(v, list) and len(v) >= 2}
    conflicts, reject_ok = [], []
    direct_dom = set(dir_manual) | set(forced)
    # 上游直连表（China_Domain 等）是**整族粗粒度白名单**（如 `.zijieapi.com`），
    # 它判直连**不等于**不能拦 —— 项目本身就是"白名单保护业务 + 黑名单拦广告"，
    # 拦的是具体广告主机、不动族内其它域。所以这里只作**注解**（帮 owner 判断），不做否决。
    upstream_direct = chain_direct_matches([h for h, _ in reject_cand])
    for h, o in reject_cand:
        if h in upstream_direct:
            o["note"] = f"上游 `{upstream_direct[h]}` 属直连族（粗粒度），拦它只影响这条主机"
        hit = next((d for d in direct_dom if h == d or h.endswith("." + d)), None)
        if hit:
            conflicts.append((h, f"与直连/放行域 `{hit}` 冲突（拦了会打断正常上网）"))
        else:
            reject_ok.append((h, o))

    # 自动写拦截的门槛：命中信号族（明确无疑）或库内命中足够多 ⇒ 自动写；其余只进报告等 owner 定
    floor = int(float(cfg("SR_AUTO_REJECT_MIN_HITS") or cfg("SR_DROP_MIN_HITS") or 50))
    aggressive = "--auto-all" in sys.argv        # 激进模式：所有候选都自动写（默认只写高置信的）

    def _confident(h, o):
        if aggressive:
            return True
        # 三条任一满足就自动写：① 命中信号族（动作判成 DROP）② 库内够频繁 ③ **名字明显是广告**
        return o["n"] >= floor or o["act"] == "REJECT-DROP" or obvious_ad(h)
    auto_rej = [(h, o) for h, o in reject_ok if _confident(h, o)]
    lowconf = [(h, o) for h, o in reject_ok if not _confident(h, o)]
    for h, o in auto_rej:
        o["auto"] = True
    if pure_ad:
        print(f"（纯广告族 {len(pure_ad)} 个：{', '.join(sorted(pure_ad)[:6])}…；"
              f"默认仍按门槛写，加 --auto-all 才全写）")
    wrote = []
    cons = check_config(state=state)
    if cons:
        for prob in cons:
            wrote.append(f"⚠️ **配置一致性**：{prob}")
    else:
        wrote.append("配置一致性检查：全部通过 ✓（列表动作 = 配置里那一行的集合动作；直连表在拦截段之前）")
    wrote.append(f"漏网候选 {len(reject_ok)} 条 = 高置信 {len(auto_rej)}（自动写） + 低频/存疑 {len(lowconf)}（只报等你定）")

    if lowconf:
        print(f"\n（低频/存疑候选 {len(lowconf)} 条，未写；要写就手动加进 reject-custom.list 手工区）")
        for h, o in sorted(lowconf, key=lambda x: -x[1]["n"])[:10]:
            print(f"   {o['n']:>4} 次  {h}→{o['act']}")
    if do_write:
        # 拦截表自动区 = **只收本轮分析出的"漏网之鱼"**（判广告 + 手机没拦 + 与直连不冲突）。
        #   为什么不是"池子里所有判广告的域"：那些大多已被上游 AdvertisingLite/Privacy 覆盖，
        #   重复写一遍既不准也不精简。direct/proxy 仍按池子收敛（它们没有上游表兜底）。
        rows, row_problems = config_rows()
        forced_act = {"reject": rows.get(cfg("REPO_REJECT_PATH"), "REJECT-DROP")}
        if row_problems:
            print("⚠️ 配置不一致：" + "；".join(row_problems))
        diff = {o["act"] for _, o in auto_rej} - {forced_act["reject"]}
        if diff:
            print(f"⚠️ 报告建议 {sorted(diff)}，但配置里 `{cfg('REPO_REJECT_PATH')}` 的集合动作是 "
                  f"{forced_act['reject']} ⇒ **以配置为准**（列表里写成别的也没用，会被覆盖）")
        # ★ 拦截表自动区 = 本轮候选 ∪ 既有条目中"仍然成立"的。
        #   踩过的坑：只按"本轮候选"重建 ⇒ 已写的条目因为被自己判成"已覆盖"而**被删掉**（+0/-3）。
        #   规则：既有条目在池里仍判 ad、且没被族关键字覆盖 ⇒ 保留；被关键字覆盖 / 判定变了 ⇒ 移除。
        keep_existing = {h for h in rej_auto
                         if not any(k in h for k in rej_kw)
                         and (state.get(h) or ["", ""])[1] == "ad"}
        sets = {"reject": {h for h, _ in auto_rej} | keep_existing, "direct": set(), "proxy": set()}
        for h, v in kept.items():
            if len(v) >= 2 and v[1] in ("direct", "proxy") \
                    and not in_domset(h, EXEMPT) and not in_domset(h, rej_manual):
                sets[v[1]].add(h)
        sets["direct"] |= {d for d in cfg_csv("SR_FORCE_DIRECT") if not in_domset(d, rej_manual)}
        for path, fmt in ((cfg("REPO_REJECT_PATH"),
                           lambda d: f"DOMAIN-SUFFIX,{d},{forced_act['reject']}"),
                          (cfg("REPO_DIRECT_PATH"), "DOMAIN-SUFFIX,{d},DIRECT"),
                          (cfg("REPO_PROXY_PATH"), "DOMAIN-SUFFIX,{d},PROXY")):
            key = "reject" if "reject" in path else ("direct" if "direct" in path else "proxy")
            if key == "reject" and not cfg_bool("SR_REJECT_WRITE", False):
                wrote.append(f"{path}: 跳过（SR_REJECT_WRITE=0；要开加 --write-reject）")
                continue
            a, r = repo_sync_set(owner, name, path, tok, prune_subsumed(sets[key], label=key),
                                 fmt, "#", br, dry_run)
            wrote.append(f"{path}: +{len(a)} / -{len(r)}" + ("  [dry-run]" if dry_run else ""))
    else:
        wrote.append("未写仓库（--report-only）")

    md = report(total_min, kept, reject_ok, direct_cand, slipped_rule, slipped_new, conflicts, stats,
                processed=[os.path.basename(p) for p in span_by_db], wrote=wrote,
                finished=time.time() - t0, stale_phone=stale_phone)
    # ── 落档 ──
    rdir = opts["report_dir"]
    os.makedirs(rdir, exist_ok=True)
    stamp = _now().strftime("%Y-%m-%d-%H%M%S")
    with open(os.path.join(rdir, f"sr-report-{stamp}.md"), "w", encoding="utf-8") as fh:
        fh.write(md + "\n")
    with open(os.path.join(HERE, "last-report.md"), "w", encoding="utf-8") as fh:
        fh.write(md + "\n")
    print(f"报告已落档：{rdir}/sr-report-{stamp}.md（同时更新 {HERE}/last-report.md）")

    if do_write and not dry_run:
        _state_save(state_path, state)
        if todo and not opts["db"]:
            for p in todo:
                files_state[os.path.basename(p)] = sigs[p]
            sr_db_state_save(files_path, files_state)
    return len(span_by_db)


def main():
    load_env()
    argv = sys.argv[1:]
    if "--selftest" in argv:
        return selftest()
    if "--status" in argv:
        hb = os.path.join(HERE, "reports", "heartbeat.log")
        print("最近心跳（cron 只要跑过就会有）：")
        try:
            lines = open(hb, encoding="utf-8").read().splitlines()[-10:]
            print("\n".join("  " + l for l in lines) or "  （心跳日志为空 —— cron 还没跑过或没跑起来）")
        except OSError:
            print("  （还没有 sr/reports/heartbeat.log —— cron 还没跑过或没跑起来）")
        rd = os.path.join(HERE, "reports")
        n = len([f for f in os.listdir(rd) if f.startswith("sr-report-")]) if os.path.isdir(rd) else 0
        print(f"\n落档报告 {n} 份（最新在 sr/last-report.md）")
        print(f"已消费库 {len(sr_db_state_load(os.path.join(HERE, '.sr-files.json')))} 个")
        return 0
    if "--check-config" in argv:
        rows, probs = config_rows()
        print("配置里的规则集动作：")
        for k, v in sorted(rows.items()):
            if "custom" in k or "adh" in k:
                print(f"   {k:22s} → {v}")
        errs = check_config(rows, probs)
        print("\n一致性检查：" + ("全部通过 ✓" if not errs else ""))
        for e in errs:
            print("  ⚠️ " + e)
        return 0 if not errs else 1
    if "--help" in argv or "-h" in argv:
        print(__doc__)
        return 0

    report_only = "--report-only" in argv
    opts = {
        "write": not report_only,
        "dry_run": "--dry-run" in argv,
        "force": "--force" in argv,
        "all": "--all" in argv,
        "db": argv[argv.index("--db") + 1] if "--db" in argv else None,
        "offline": "--offline" in argv,
        "report_dir": (argv[argv.index("--report-dir") + 1] if "--report-dir" in argv
                       else os.path.join(HERE, "reports")),
    }
    # 拦截表：无人值守默认**开**（带冲突护栏）；要关就 --no-reject
    if report_only or "--no-reject" in argv:
        os.environ["SR_REJECT_WRITE"] = "0"
    else:
        os.environ.setdefault("SR_REJECT_WRITE", "1")   # 无人值守默认写（脏数据由冲突护栏挡住）

    lock = _lock(os.path.join(HERE, ".sr-lock"))
    if lock is None:
        print("另一个实例在跑（sr/.sr-lock 被占用）—— 退出")
        return 0

    watch = "--watch" in argv
    interval = int(argv[argv.index("--interval") + 1]) if "--interval" in argv else int(cfg("SR_INTERVAL") or 300)
    if not watch:
        run_once(opts)
        return 0
    print(f"[watch] 每 {interval}s 扫一次 {cfg('SR_DB_DIR')}（Ctrl-C 退出）")
    while True:
        try:
            run_once(opts)
        except Exception as e:  # noqa: BLE001 - 守护进程不能被单轮异常打死
            print(f"!! 本轮失败：{e!r}")
        time.sleep(interval)


def selftest():
    assert split_manual("a\nb\n", "#") == (["a", "b"], [])
    assert parse_domains("DOMAIN-KEYWORD,foo,DIRECT") == set()
    assert parse_domains("DOMAIN-SUFFIX,a.com,DIRECT") == {"a.com"}
    assert in_domset("x.y.com", {"y.com"}) is True
    print("selftest ok")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
