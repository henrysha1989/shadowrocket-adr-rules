# 模块专区（Shadowrocket `.module`）

清单（`*.list`）管**域名级**，模块管**路径级**。两边分工，不互相重复：

| | 文件 | 拦什么 | 前提 |
|---|---|---|---|
| 域名级 | `bytedance-ad.list`（配置里的 `RULE-SET`，动作 `REJECT-DROP`） | 一眼就是广告/埋点的**整个主机** | 无（纯清单） |
| 路径级 | `module/bytedance-ad.module` | 内容和广告**同域**时，只砍广告**路径** | **必须开 HTTPS 解密（MITM）** |

为什么非要有路径级：字节的 `pstatp` / `byteimg` / `snssdk` 这些 CDN **既发内容也发广告**，
域名级一刀切会连内容一起拦 —— 2026-10-08 那次红果/番茄"网络异常"就是裸父域 `qznovelvod.com` /
`byteimg.com` 连内容视频一起断了。路径级是这类域名的唯一正解。

---

## `bytedance-ad.module` —— 字节系去广告（路径级）

### 它做什么

15 条 `[URL Rewrite]`，打字节系 App（抖音 / 红果 / 番茄 / 皮皮虾 / 头条 …）**共用**的广告链路：

| 组 | 条数 | 动作 | 打什么 |
|---|---|---|---|
| 广告接口 / 上报 | 5 | `reject` | 穿山甲 SDK 的 `get_ads/stats/settings`、`/api/ad/`、`motor/.../V2/`、`track-log/src`、`gurd.../v3/package` |
| 广告素材 / 安装包 | 5 | `reject` | `ad-app-package`、`/obj|img/ad/`、`ad-pattern/renderer`、`mosaic-legacy?from=ad`、`byteimg` 的 apk 路径 |
| 广告图 | 3 | `reject-img` | `web.business.image`、`byteimg` 的 `tos-cn-i-…-jpeg.jpeg`（回 1×1 空图：App 拿到 200，比 `reject` **温和**、不触发重试） |
| 广告视频 | 2 | `reject` | `toutiao.mp4`、`/video/play/1/toutiao/*/mp4`（就是"有画面有声音"那种） |

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

- **会解密 5 个域名**：`*.pangolin-sdk-toutiao.com`、`*.pglstatp-toutiao.com`、`*.pstatp.com`、
  `*.byteimg.com`、`*.snssdk.com`。后三个流量不小（图片/视频 CDN）⇒ 手机要多做 TLS 解密，**耗电、发热**。
- 想省点：把 `[MITM] hostname` 里的 `*.byteimg.com, *.snssdk.com` 删掉，同时注释掉用到它们的 5 条 rewrite。
- **回滚**：关模块或关 `[MITM] enable` 即可，配置和清单都不用动。

### 怎么验证生效

- **`proxy-*.db` 报告看不出来**：URL Rewrite 发生在分流**之后**，手机日志只记主机级结果（DIRECT/PROXY/REJECT），
  路径级的拦截不会留下独立记录。只能看"广告有没有消失 + 有没有副作用"。
- 该见效的地方：开屏后的广告图文、信息流里的广告图/广告视频（含带声音那种）、广告安装包下载。

### 出问题先怀疑这两条

| 规则 | 风险 |
|---|---|
| `*.snssdk.com/video/play/1/toutiao/.+/mp4` | 视频路径，最可能误伤内容视频 |
| `*.snssdk.com/api/ad/.+` | `/api/ad/` 若不是纯广告接口会误伤 |

注释掉（行首加 `#`）重载即可，其余规则不受影响。

### 出处与改动（对比社区原版）

正则来自 yfamilys.com（deezertidal 社区的模块站）的 `fanqie.module`（番茄小说模块），只取它的
`[URL Rewrite]` 部分，并做了四处清理：

1. **丢掉整个 `[Rule]` 段**：原版有 4 条 **两段式裸父域** `DOMAIN-SUFFIX … REJECT`
   （`bytedance.com` / `bytegoofy.com` / `byteorge.com` / `pglstatp-toutiao.com`），
   前三个正是我们 2026-10-08 从 `bytedance-ad.list` 删掉的 5 个裸父域中的 3 个；原版把 `byteimg.com` 写成 DIRECT
   —— 两段式父域在这类混用 CDN 上不可控，一律不要。
2. **丢掉 `DOMAIN-KEYWORD,zijieapi,REJECT`**：实测在本机状态池 949 台里命中 **40 台**，其中 **9 台当时走 DIRECT**。
3. **丢掉 6 条硬编码 `IP-CIDR,/32,REJECT,no-resolve`**：CDN 共享 IP，写死会误伤。
4. **`[MITM] hostname` 从 9 条收成 5 条**：原版含 `*.pstatp.com.*`、`*.pangolin-sdk-toutiao.*`、
   `*default.ixigua.com` 这类怪写法，且缺 `*.byteimg.com`（正则用到了却没解密）—— 补齐必需项、去掉多余项。

另外把 3 条纯广告图规则从 `reject` 改成 `reject-img`（返回 1×1 空图，避免 App 重试）。

**本模块不跟随上游自动更新**：只有 owner 点名才改。

### 校验

`module/validate-module.mjs` 会检查：正则能否编译、每条能否命中自己的样例 URL、内容侧负样本不误伤、
不许出现 `[Rule]` / `IP-CIDR` / `DOMAIN-KEYWORD` / 第三方 JS、MITM 域名与正则互相覆盖。

```sh
node module/validate-module.mjs module/bytedance-ad.module
```
