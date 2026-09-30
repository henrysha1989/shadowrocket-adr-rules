# 小火箭侧分析（独立项目）

> 2026-10-01 从 `adh_gist_sync.py` 拆出来。**两个项目互不引用、不共享状态**：
>
> | 项目 | 入口 | 输入 | 输出 |
> |---|---|---|---|
> | ADH 侧 | `adh_gist_sync.py`（宿主 cron `0 */4 * * *`，路径不能动） | ADH querylog | `adh-custom.txt`（ADH 里当过滤清单订阅） |
> | **小火箭侧** | **`sr/sr_analyze.py`（本项目）** | 你手动上传的 `proxy-*.db` | 三张自建表 + 体检报告 |
>
> 本项目**不读** ADH 的 querylog / user_rules / adh-custom.txt / FORCE_DIRECT，不需要 ADH 的地址与口令。

## 用法

```sh
cd /vol1/1000/Docker/deepseek-harness/workspace

python3 sr/sr_analyze.py                  # ① 只看报告（不写任何东西）
python3 sr/sr_analyze.py --write          # ② 分析 + 写 direct/proxy 两张表的自动区
python3 sr/sr_analyze.py --write --write-reject   # ③ 连 intercept 表也写（带冲突护栏）
python3 sr/sr_analyze.py --write --dry-run        # 只算增删、不推仓库
python3 sr/sr_analyze.py --db <某个.db>   # 只分析指定库（调试）
python3 sr/sr_analyze.py --all            # 忽略"已消费"记录，全部重扫
python3 sr/sr_analyze.py --force          # 越过"删太多"的安全阀（>30% 时默认中止）
python3 sr/sr_analyze.py --selftest
```

凭据：环境变量 `REPO_TOKEN` > `sr/sr.env` > 兜底读 `$ROOT/.env`（**只取 REPO_TOKEN**，不碰 ADH 的键）。

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
