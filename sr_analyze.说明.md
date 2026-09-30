# 小火箭侧分析（独立项目）

> 2026-10-01 从 `adh_gist_sync.py` 拆出来。**两个项目互不引用、不共享状态**：
>
> | 项目 | 入口 | 输入 | 输出 |
> |---|---|---|---|
> | ADH 侧 | `adh_gist_sync.py`（宿主 cron `0 */4 * * *`，路径不能动） | ADH querylog | `adh-custom.txt`（ADH 里当过滤清单订阅） |
> | **小火箭侧** | **`sr/sr_analyze.py`（本项目）** | 你手动上传的 `proxy-*.db` | 三张自建表 + 体检报告 |
>
> 本项目**不读** ADH 的 querylog / user_rules / adh-custom.txt / FORCE_DIRECT，不需要 ADH 的地址与口令。

## 数据源（唯一）

**手机导出的 `proxy-*.db`，落在固定目录里**（默认 `/vol1/1001/shadowrocket-db/`，glob `*.db`）：

- 每轮**扫这个目录**，按「文件名 → size:mtime」签名（`sr/.sr-files.json`）判断哪些是**新库/变化过的库**；
- 只处理新库；**没有新库就什么都不做**（不写仓库、不落档）；
- 首次运行会消费目录里的全部库。

## 用法

```sh
python3 sr/sr_analyze.py                  # ① 无人值守：扫新库 → 分析 → 落档 → 写表 → 推 GitHub
python3 sr/sr_analyze.py --report-only     # ② 只看报告，绝不写仓库（人工核对）
python3 sr/sr_analyze.py --watch --interval 300   # ③ 常驻轮询
python3 sr/sr_analyze.py --db <某个.db>     # 调试：只分析指定库
python3 sr/sr_analyze.py --all             # 忽略签名，全部重扫
python3 sr/sr_analyze.py --dry-run         # 算增删但不推仓库
python3 sr/sr_analyze.py --no-reject       # 不动拦截表
python3 sr/sr_analyze.py --force           # 越过"删太多"的安全阀
python3 sr/sr_analyze.py --offline         # 没 token 时离线看分类（护栏会失真，仅调试）
```

**落档**：每轮写 `sr/reports/sr-report-<时间戳>.md`，同步一份最新到 `sr/last-report.md`。
**单实例锁** `sr/.sr-lock`：定时任务重叠时后一个直接退出。

## 定时（推荐宿主 cron，和 ADH 那套同一套路）

```sh
install -m 755 sr/run-sr-analyze.sh /usr/local/sbin/sr-analyze.sh
printf '*/30 * * * * root /usr/local/sbin/sr-analyze.sh >> /var/log/sr-analyze.log 2>&1\n' > /etc/cron.d/sr-analyze
chmod 644 /etc/cron.d/sr-analyze
```

root 跑才能读 000 权限的手机 db 与 `workspace/.env`（取 `REPO_TOKEN`）。
不想用 cron 就 `--watch` 常驻（supervisor/docker 托管）。

## 写什么、不写什么（门槛）

- **direct / proxy 自动区**：按证据池收敛（TTL 90 天）。
- **reject 自动区**：**只收本轮分析出的"漏网之鱼"**（判广告 + 手机没拦 + 与直连不冲突）。
  不把"池子里所有判广告的域"都写进去 —— 那些大多已被上游 AdvertisingLite/Privacy 覆盖，重复写既不准也不精简。
- **自动写的门槛**：命中信号族名（明确无疑）或库内命中 ≥ `SR_AUTO_REJECT_MIN_HITS`（默认 50）才**自动写**；
  低频/存疑的**只进报告**（"候选，等你定"），不写死 —— 避免把偶发域名或疑似误伤写成规则。

## 报告给什么（owner 2026-10-01 定的三件事）

1. **漏网之鱼**：参考黑名单判广告、但手机实际没拦（放行）的主机 —— 带次数与次/分。
2. **直连域名滑落到代理**，分两种，因为处理方法不同：
   - ① **已在直连表、手机却走了代理** ⇒ 规则没生效（查缓存/规则顺序/冲突）
   - ② **判直连但不在直连表** ⇒ 需要新增直连规则（否则会一直走代理）
3. **新增 reject 的冲突护栏**：任何要写进拦截表的域，先跟**直连表（含手工区）**和
   `SR_FORCE_DIRECT` 对撞；冲突的**一律不写**并在报告里列出来 ——
   绝不能让"拦广告"把正常上网的直连域给拦了。

顺带输出体检指标：窗口时长 / 域名事件数 / 动作分布 / 被拒主机数 / 重试速率（判据 ≤5 次/分/主机）。

## 判定口径（改之前先读这段）

- `classify()` 优先级：**参考黑名单(ad) > 信号/埋点族名(ad) > 直连族 > 广告特征 > 代理特征**。
  - **信号/埋点族名优先于域名族**（`SR_SIGNAL_PATTERNS`：`-misc-lf` / `-misc-lq` / `-applog` /
    `live-player-log` / `-ad-sign` / `reading-ad` / `ads-normal` / `telemetry`）——
    否则同一个 `mon*-misc` 族挂在字节系域名下会被判直连（实测踩过：一边 DROP 一边直连）。
  - 但 `*-sign.douyinpic.com`、`*-reading-sign.fqnovelpic.com` 这类是**图片 URL 签名**（内容 CDN），
    判据是**在不在参考黑名单里**，不是名字里有没有 "sign"。
- **"分类器没意见" 不是删除理由**：已写进直连表的域，只有当新判定**明确相反**（ad/proxy）时才移除；
  否则保留（避免 owner 批准过的域悄悄掉回 `FINAL,PROXY`）。
- `SR_FORCE_DIRECT`：owner 批准过的「别拦/直连」域（含公司自建系统、Tesla、字节 polaris/mssdk、
  阿里风控、微信子域等）。命中的域跳过广告判定、并**本身写进直连表**。
- 手工区（AUTO 标记之上）永远由 owner 维护，脚本只读不写。

## 状态文件（都在本目录）

| 文件 | 作用 |
|---|---|
| `.sr-files.json` | 已消费的 db 签名（basename → size:mtime），保证同一个库不重复消费 |
| `.sr-state.json` | 主机证据池（host → [时间戳, 判定, 动作]），TTL `SR_TTL_DAYS`（默认 90 天） |

## 与 ADH 侧的边界（踩过的教训）

- 曾经 SR 分析会读 ADH 的 `FORCE_DIRECT`，结果：把父域加进 ADH 的放行表，会让 SR 自动区里的子域
  被收敛删掉、而手机上没有任何父域规则 ⇒ 那一族掉回 `FINAL,PROXY`。**两边必须各管各的表。**
- 需要"两边都放行"的域，**分别登记**：ADH 侧 `adh_gist_sync.py` 的 `FORCE_DIRECT`；
  小火箭侧本项目的 `SR_FORCE_DIRECT`。这是一次性的意图搬迁，不是运行时耦合。

## 与 `shadowrocket-config` 的联动（**必须**，否则会再出风暴）

小火箭的 `RULE-SET,url,ACTION` 行会用 **ACTION 覆盖列表里每一条的动作**
（2026-09-30 的重试风暴就是这么来的：列表里写 `REJECT-DROP`、配置行写成 `REJECT` ⇒ 全部退化成 RST）。

所以两个仓库有一条硬不变式：

> **每张表里用到的动作 = 配置里那一行的集合动作**

本项目把这条不变式做成机器检查（每次跑都会做，也会写进落档报告）：

```sh
python3 sr_analyze.py --check-config      # 通过 exit 0 / 不通过 exit 1（可挂 CI）
```

检查内容：

| 检查 | 说明 |
|---|---|
| 动作一致 | 读**线上文件**里每条动作的集合，跟配置里那一行的动作对比 |
| 危险退化 | 配置行是普通 `REJECT` 而列表里有 `REJECT-DROP` ⇒ **判失败**（DROP 退化成 RST = 风暴风险） |
| 必备动作 | 拦截表必须是 `REJECT-DROP`；直连表必须是 `DIRECT` |
| 顺序（方案 B） | `direct-custom.list` 必须排在拦截段**之前**（自建放行要压过订阅广告表） |
| 覆盖 | 配置必须引用拦截表/直连表（`proxy-custom.list` 未被引用属设计：`FINAL,PROXY` 兜底） |

**写入时以配置为准**：脚本写拦截表时先读配置里那一行的动作，把条目就写成那个动作 ——
这样文件永远不会跟配置打架（报告里"建议动作"只作参考，并会提示两者不一致）。

已知的一个"设计如此"：`proxy-custom.list` 没被配置引用（代理侧靠配置末尾的 `FINAL,PROXY` 兜底）；
要显式控制就在配置里加一行 `RULE-SET,…,proxy-custom.list,PROXY`。
