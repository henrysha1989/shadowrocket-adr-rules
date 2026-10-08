# 模块专区（Shadowrocket `.module`）

清单（`*.list`）管**域名级**，模块管**路径级**。两边分工，不互相重复：

| | 文件 | 拦什么 | 前提 |
|---|---|---|---|
| 域名级 | `bytedance-ad.list`（配置里的 `RULE-SET`，动作 `REJECT-DROP`） | 一眼就是广告/埋点的**整个主机** | 无（纯清单） |
| 路径级 | `module/bytedance-ad.module` | 内容和广告**同域**时，只砍广告**路径** | **必须开 HTTPS 解密（MITM）** |

为什么非要有路径级：字节的 `pstatp` / `byteimg` / `snssdk` / `amemv` 这些 CDN 与 API **既发内容也发广告**，
域名级一刀切会连内容一起拦 —— 2026-10-08 那次红果/番茄"网络异常"就是裸父域 `qznovelvod.com` /
`byteimg.com` 连内容视频一起断了。路径级是这类域名的唯一正解。

---

## `bytedance-ad.module` —— 字节系去广告（路径级）

### 它做什么

14 条 `[URL Rewrite]`，打字节系 App（抖音 / 红果 / 番茄 / 皮皮虾 / 头条 …）**内容和广告同域**的那些 CDN / API：

| 组 | 条数 | 动作 | 打什么 |
|---|---|---|---|
| 广告接口 / 上报 | 5 | `reject-dict` ×3 · `reject-200` ×1 · `reject` ×1 | `snssdk` 与 `amemv` 的 `/api/ad/`、`motor/.../V2/`（JSON 接口回 `200 + {}`）、`track-log/src`（上报回 `200` 空体）、`gurd.../v3/package` |
| 广告素材 / 安装包 | 4 | `reject` | `pstatp` 的 `ad-app-package`、`/obj|img/ad/`、`ad-pattern/renderer`、`mosaic-legacy?from=ad`、`byteimg` 的 apk 路径 |
| 广告图 | 3 | `reject-img` | `pstatp` 的 `web.business.image`、`byteimg` 的 `tos-cn-i-…-jpeg.jpeg`（回 1 像素图：App 拿到 200，比 `reject` **温和**、不触发重试） |
| 广告视频 | 1 | `reject` | `snssdk` 的 `/video/play/1/toutiao/*/mp4`（就是"有画面有声音"那种） |

**只留"域名级拦不到"的族**（设计总则见仓库根目录 [`bytedance-ad.说明.md`](../bytedance-ad.说明.md)）：
本模块只覆盖 `pstatp` / `byteimg` / `snssdk` / `amemv` 四个**既发内容又发广告**的族；
已经被 `bytedance-ad.list` 整族拦掉的（`pangolin-sdk-toutiao`、`pglstatp-toutiao`）**不在这里重复** ——
请求根本到不了 MITM，写了只会白白扩大解密面（校验器的 `--against` 会把这种"死规则"报出来）。

**动作分级（2026-10-09 完善）**：按"响应该长什么样"选动作，而不是一律 `reject`：

| 响应类型 | 动作 | 为什么 |
|---|---|---|
| JSON 接口（广告拉取、配置） | `reject-dict`（200 + `{}`） | SDK 拿到合法空对象 ⇒ 无广告、**不重试**；`reject`(404) 反而可能触发 SDK 重连 |
| 上报 / 日志 | `reject-200`（200 空体） | SDK 认为上报成功，最不容易重试 |
| 素材 / 安装包 / 视频 | `reject`（404） | 需要真的断掉，别让播放器/下载器拿到半成品 |
| 图片 | `reject-img`（200 + 1 像素） | 拿不到图但不显示裂图 |

这套分级抄自可莉插件中心（它的 `reject_dict(200)` 就是这个思路），动作语义见本仓库 `Shadowrocket-手册` 的「规则策略」一节。

### 它**不**做什么（故意的）

不含 `[Rule]`、不含 `IP-CIDR`、不含 `DOMAIN-KEYWORD`、不含任何 `[Script]`（**纯静态 rewrite，不跑第三方 JS**）。
域名级拦截继续住 `bytedance-ad.list` —— 字节系规则只有这一个家。

### 前提：MITM 必须自己开

本模块 `[MITM]` 只写 `hostname = %APPEND% …`，**不会**帮你打开开关（`enable` 仍在配置自己的 `[MITM]` 段）。
如果配置里 `[MITM] enable = false`，装上它**只会**让域名清单那半生效——也就是白装。

手机侧步骤（配置里先写 `enable = true`）：

1. 小火箭 → 配置 → 点配置文件右侧 ⓘ → **HTTPS 解密** → 生成新证书 → 允许安装；
2. iPhone 设置 → 已下载描述文件 → 安装；
3. 设置 → 通用 → 关于本机 → 证书信任设置 → 勾选信任；
4. 回小火箭确认。之后重载/编译一次配置。

### 安装地址

```
https://git.521989.xyz/https://raw.githubusercontent.com/henrysha1989/shadowrocket-adr-rules/main/module/bytedance-ad.module
```

（`git.521989.xyz/` 是自建加速前缀；不带前缀的 `raw.githubusercontent.com` 在国内手机上不一定能下。）

### 代价（照实写）

- **会解密 4 个域名**：`*.pstatp.com`、`*.byteimg.com`、`*.snssdk.com`、`*.amemv.com`。
  它们流量都不小（图片/视频 CDN、抖音 API）⇒ 手机要多做 TLS 解密，**耗电、发热**。
- 想省点：把 `[MITM] hostname` 里的某个域名删掉，同时注释掉只用到它的 rewrite
  （`*.byteimg.com` → 3 条；`*.amemv.com` → 1 条；`*.snssdk.com` → 5 条；`*.pstatp.com` → 5 条）。
- **回滚**：关模块或关 `[MITM] enable` 即可，配置和清单都不用动。

### 怎么验证生效

- **`proxy-*.db` 报告看不出来**：URL Rewrite 发生在分流**之后**，手机日志只记主机级结果（DIRECT/PROXY/REJECT），
  路径级的拦截不会留下独立记录。只能看"广告有没有消失 + 有没有副作用"。
- 该见效的地方：开屏后的广告图文、信息流里的广告图/广告视频（含带声音那种）、广告安装包下载。

### 出问题先怀疑这两条

| 规则 | 风险 |
|---|---|
| `*.snssdk.com/video/play/1/toutiao/.+/mp4` | 视频路径，最可能误伤内容视频 |
| `*.snssdk.com/api/ad/` · `*.amemv.com/api/ad/` | `/api/ad/` 若不是纯广告接口会误伤 |

注释掉（行首加 `#`）重载即可，其余规则不受影响。

### 出处与改动（两个社区各取了什么）

**第一次（2026-10-09）：yfamilys.com / deezertidal 的 `fanqie.module`（番茄小说）**

只取它的 `[URL Rewrite]`，做了四处清理：

1. **丢掉整个 `[Rule]` 段**：原版有 4 条 **两段式裸父域** `DOMAIN-SUFFIX … REJECT`
   （`bytedance.com` / `bytegoofy.com` / `byteorge.com` / `pglstatp-toutiao.com`），
   前三个正是我们 2026-10-08 从 `bytedance-ad.list` 删掉的 5 个裸父域中的 3 个；原版把 `byteimg.com` 写成 DIRECT
   —— 两段式父域在这类混用 CDN 上不可控，一律不要。
2. **丢掉 `DOMAIN-KEYWORD,zijieapi,REJECT`**：实测在本机状态池 949 台里命中 **40 台**，其中 **9 台当时走 DIRECT**。
3. **丢掉 6 条硬编码 `IP-CIDR,/32,REJECT,no-resolve`**：CDN 共享 IP，写死会误伤。
4. **`[MITM] hostname` 从 9 条收成 5 条**：原版含 `*.pstatp.com.*`、`*.pangolin-sdk-toutiao.*`、
   `*default.ixigua.com` 这类怪写法，且缺 `*.byteimg.com`（正则用到了却没解密）—— 补齐必需项、去掉多余项。

**第二次（2026-10-09）：可莉插件中心 `hub.kelee.one`（Loon 社区，见 `ops/可莉插件中心-评估-2026-10-09.md`）**

- **取**：`HKDouYin_remove_ads.lpx` 的 `api5-normal-lq.amemv.com/api/ad/`（并入我们的 `*.amemv.com/api/ad/` 一条），
  以及它/`BlockAdvertisers.lpx` 的**动作思路**（`reject_dict(200)` → `reject-dict`）—— 上面那张分级表就是照它改的。
  取值依据：两个最新库里 `amemv.com` **16 台 / 181 次请求全是 DIRECT**（域名没被拦、又确实是字节广告接口的宿主）。
- **不取**（各有理由）：
  - `PiPiXia_remove_ads.lpx`（皮皮虾）：3 条规则本身干净，但**本机 0 次请求**（库内 0 台 pipix 主机）⇒ 不为一台没人用的 App 扩 MITM 面；
  - `SodaMusic_remove_ads.lpx`（汽水音乐）：`luna/...` 系列在库里 **0 台**（`qishui` 只有 1 台 16 次，还不是 `luna` 接口）⇒ 证据不足；
    它的 `webcast-open.douyin.com/webcast/openapi/feed/` 是**直播 feed**，拦了可能伤直播 ⇒ 不碰；
  - `HKDouYin` 的 `[Rule]` 段：5 条硬编码 `IP-CIDR + DEST-PORT` 组合 + 1 条 `(DOMAIN-SUFFIX bytegecko/byteeffecttos) AND (DOMAIN-KEYWORD ncdn)`——IP 与父域混搭，正是我们踩过的雷；
  - `DragonRead_remove_ads.lpx`（番茄小说）的 **34 条 `DOMAIN…REJECT`**：那是**域名级**，按我们的不变量只能进 `bytedance-ad.list`，不进模块。
    （已核对：9 条与我们的直连表冲突，16 条是新增，其中只有 4 台在库内且现走 DIRECT ⇒ 收益很小，且 `is/vas/effect.snssdk.com` 这类我们**故意放行**，需要单独决策，见评估报告。）
  - 它的 jq 改响应体规则（抖音首页 tab 精简、"我的"页借款入口）：**不是广告**，是界面清理。
- **纠正一个我先前的误判**：小火箭**支持 jq** —— `[Body Rewrite]` 段有 `http-response-jq`（见 `Shadowrocket-手册`「正文重写」）。
  所以可莉那批字段级规则**技术上能搬**，只是本模块定位是"去广告"，暂不放界面清理类规则；
  真要用，`[Body Rewrite]` + 可莉原文照抄即可（`response.json.jq(...)` → `http-response-jq`）。

**第三次（2026-10-09，跳出社区、按自己的设计收敛）**：不再问"社区还有什么"，改成用我们自己的库证据 + 分层模型
（见仓库根目录 [`bytedance-ad.说明.md`](../bytedance-ad.说明.md)）回头审自己：

- **删掉 pangolin 那条**（`*.pangolin-sdk-toutiao.com/api/ad/union/sdk`）**和 `pglstatp-toutiao` 的全部分支**：
  这两个族已被 `bytedance-ad.list` 整族拦掉（`DOMAIN-SUFFIX,pangolin-sdk-toutiao.com`、`DOMAIN-SUFFIX,pglstatp-toutiao.com`），
  请求在域名级就被 DROP，**永远到不了 MITM** ⇒ 死规则，只会扩大解密面。`[MITM] hostname` 顺势 6 → 4。
- **删掉 `pglstatp-toutiao/.+/toutiao.mp4` 整条**（同上，宿主已被域名级拦掉）。
- 其余规则把 `(pglstatp-toutiao|pstatp)` 收成 `pstatp`，语义不变、少一段死分支。
- 校验器新增 `--against`，把"死规则 / 与直连表冲突"变成可自动检查的项（见下）。

另外：3 条纯广告图规则用 `reject-img`，其余动作按上表分级。

**本模块不跟随上游自动更新**：只有 owner 点名才改。

### 校验

`module/validate-module.mjs` 会检查：正则能否编译、每条能否命中自己的样例 URL、内容侧负样本不误伤
（含 `amemv`/`snssdk` 内容接口、`byteimg` 内容图、`reading-video` 等 10 条）、
不许出现 `[Rule]` / `IP-CIDR` / `DOMAIN-KEYWORD` / 第三方 JS、MITM 域名与正则互相覆盖。

再加 `--against` 做**分层归属**检查（设计的核心不变量）：

```sh
node module/validate-module.mjs module/bytedance-ad.module \
     --against bytedance-ad.list,direct-custom.list
```

- ⚠️ **死规则**：规则里的域名已被拦截清单整族拦掉 ⇒ 该删（或把该族从清单里挪出来，二选一）；
- ❌ **冲突**：规则里的域名被直连清单放行 ⇒ 先判谁对，别两边都留。

当前模块跑出来是 **0 提示**（14 条规则全部落在"域名级拦不到"的族上）。
