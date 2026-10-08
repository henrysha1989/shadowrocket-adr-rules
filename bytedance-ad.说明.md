# 字节系拦截设计（`bytedance-ad.list` + `module/bytedance-ad.module`）

> 2026-10-09 立。此前是"照着社区模块抄"；这份文档改成**先立规矩、再按证据填**。
> 所有数字都来自手机导出的 `proxy-*.db`（本仓库脚本 `sr_analyze.py` 的同一数据源），不是估计。

## 一、三层模型：谁管什么

| 层 | 载体 | 拦什么 | 前提 | 动作 |
|---|---|---|---|---|
| **L1 域名级** | `bytedance-ad.list`（配置里 `RULE-SET …,REJECT-DROP`） | ① 名字即语义的广告/埋点主机 ② 已证实的广告/埋点主机 ③ **整族纯广告**的两段式父域 | 无 | `REJECT-DROP`（丢包，防重试风暴） |
| **L2 路径级** | `module/bytedance-ad.module`（+ 手机 MITM） | 内容与广告**同域**的 CDN/API，只砍广告**路径** | `[MITM] enable = true` + 手机信任证书 | `reject-dict` / `reject-200` / `reject` / `reject-img` |
| **L3 放行** | `direct-custom.list`（及上游 DIRECT 清单） | 内容域：不许出现在 L1/L2 里 | 无 | `DIRECT` |

三条铁律：

1. **一族只有一个 owner。** 已经被 L1 整族拦掉的族，L2 里不许再写路径规则 —— 请求根本到不了 MITM，
   写了只是白白扩大解密面（`validate-module.mjs --against` 会把这种叫"死规则"报出来）。
2. **L2 只放 L1 拦不到的路径。** 判断依据是"该族域名级能不能一刀切"：能（纯广告族）就进 L1；
   不能（内容+广告同域）才进 L2。
3. **L1 里禁止新增两段式裸父域**，除非该族**整族**都是广告/埋点且库内无一直连主机
   （2026-10-08 就是裸父域 `qznovelvod.com`/`byteimg.com` 把内容视频一起断了，红果/番茄"网络异常"）。

## 二、判据：一个新主机该进哪层

```
只看名字就能定的（进 L1）
  ├─ 广告 SDK：pangolin / pglstatp / pangle / panplayable / oceanengine / smadex / applovin …
  ├─ 埋点上报：*-applog* / *-misc-lf|-lq / *-ad-sign / ads-normal / *-log* / mon* / timon / analytics*
  └─ 反广告小说站群：basic-novel.pro / novelpair.com / scareshortnovel.com …（这类整族父域可拦）
与内容同域的 CDN/API（只能进 L2，且必须给出具体路径）
  ├─ pstatp.com / byteimg.com / snssdk.com / amemv.com
  └─ qznovelvod.com / fqnovelpic.com / douyinpic.com / douyinvod.com / bytegecko.com（内容为主，目前全放行）
拿不准
  └─ 先放行进 L3，等库里有证据再动 —— 宁可漏拦，不要误伤
```

## 三、实测基线（2026-10-09，两个最新库合计 3898 台主机）

两库：`proxy-2026-10-08-133957.db`（5.9h）+ `proxy-2026-10-08-193657.db`（4.7h）。
全局裁决 DIRECT 3710 / REJECT 129 / PROXY 59。

**字节系 47 个族、2429 次拦截，其中 1922 次（79%）由我们自己的规则命中**（其余走上游清单）。

| 分类 | 族数 | 例子 |
|---|---|---|
| 纯广告族（全拦） | 7 | pangolin-sdk-toutiao* ×3、pglstatp-toutiao、ctobsnssdk、volceapplog、oceanengine |
| **混合族**（有拦有放） | 5 | fqnovel.com、byteimg.com、snssdk.com、ecombdapi.com、zijieapi.com |
| 内容族（全放行） | 35 | qznovelvod、douyinpic、amemv、douyinvod、fqnovelpic、bytegecko、volces、douyin.com … |

### 5 个混合族的覆盖现状

| 族 | 库内（主机/请求/拦/放） | L1 | L2 | 说明 |
|---|---|---|---|---|
| fqnovel.com（番茄小说） | 9 / 1105 / 1096 / 9 | ✅ 关键字 `-misc-lf` 621 + `-applog` 475 | — | 拦截全靠 L1 名字匹配，尚无路径级证据 |
| byteimg.com | 14 / 578 / 107 / 471 | ✅ 关键字 `-ad-sign` 107 | ✅ 2 条图片 + 1 条 apk | 内容图片 471 次照放 —— 分工正确的样板 |
| snssdk.com | 9 / 514 / 478 / 36 | ✅ `DOMAIN,i.snssdk.com` 478 | ✅ `/api/ad/`、`motor…/V2/`、`video/play/1/toutiao` | 单条规则贡献最大 |
| ecombdapi.com（抖音电商 API） | 11 / 208 / 164 / 44 | ✅ 2 条 `-lf` 主机 | — | 其余主机是否带广告路径未知 |
| zijieapi.com | 23 / 159 / 61 / 98 | ✅ `ads3/ads5-normal-lf` | — | 详见"已知缺口" |

### 拦截集中度（判断清单是否臃肿）

- `bytedance-ad.list` 249 条规则里，**只有 15 条**在这两个窗口被命中过。
- 贡献最大的五者：`-misc-lf`(621) · `-applog`(475) · `i.snssdk.com`(478) · `-ad-sign`(107) · `isaas5-normal-lf.ecombdapi.com`(107)。
- 结论：**关键字 + 少数主机扛了绝大部分**。休眠规则不删 —— 广告主机是轮换的，今天不出现不代表明天不出现。

### 26 个两段式父域复核（2026-10-09）

全部属于纯广告族（pangolin/pglstatp/pangle/panplayable/toutiaopage/ctobsnssdk + 反广告小说站群），
**库内无一有直连主机** ⇒ 目前是安全的，保留。

## 四、已知缺口（宁可空着，不硬造）

1. **fqnovel.com / ecombdapi.com / zijieapi.com 的路径级**：yfamilys 与可莉两个社区都**没有**这三族的路径情报，
   我们自己也没有路径证据（库只记主机级动作）⇒ 不写规则。等哪天有抓包/社区情报再补。
2. **抖音直播 feed**（`webcast-open.douyin.com/webcast/openapi/feed/`）：可莉有这条，但它明显是直播数据接口，拦了伤功能 ⇒ 不收。
3. **可莉的字段级 jq 规则**（抖音首页 tab 精简、"我的"页借款入口）：小火箭的 `[Body Rewrite]` 支持 `http-response-jq`，
   技术上能搬，但那是**界面清理**不是去广告 ⇒ 不放进本模块（要的话另开 `ui-tidy.module`）。
4. **皮皮虾**：本机 0 次请求 ⇒ 不为它扩 MITM 面。

## 五、维护规则

改任何一处之后跑一遍：

```sh
node module/validate-module.mjs module/bytedance-ad.module \
     --against bytedance-ad.list,direct-custom.list
```

它会检查：正则能否编译、每条能否命中自己的样例、内容侧负样本不误伤、结构红线（无 `[Rule]`/IP/关键字/JS）、
MITM 域名与规则互相覆盖，以及**分层归属**：

- ⚠️ 死规则：规则里的域名已被 L1 整族拦掉 ⇒ 删掉它，或把该族从 L1 挪出来（只能二选一）。
- ❌ 冲突：规则里的域名被 L3 放行 ⇒ 先判断谁对，不要两边都留。

其他约定：

- 新增 L1 主机：**三段式起**（禁止裸父域），先与 `direct-custom.list` 对撞，动作一律 `REJECT-DROP`。
- 新增 L2 规则：必须能说出**具体广告路径**，且该族的 MITM 域名要一并加上。
- 改动推上去后，**手机必须重载/编译一次配置**才生效（远程规则集要重新拉取）。
- 判断"手机到底拦没拦"，只认 `proxy-*.db` 的 `logging_content.c3type`；
  `.sr-state.json` 的第三列是**建议动作**，不是手机动作（2026-10-09 踩过）。

## 六、配置里的顺序要求（重要）

白名单·测试版（owner 日常）—— 顺序正确：

```
direct-custom(1) → bytedance-ad(2) → Lan/STUN/Apple(3-5) → reject-custom(6)
→ BlockHttpDNS(7) → AdvertisingLite_Domain(8) → AdvertisingLite(9) → …
```

⚠️ 黑名单·极简版 —— **顺序是反的**：

```
BlockHttpDNS(1) → AdvertisingLite_Domain(2) → AdvertisingLite(3) → Privacy_Domain(4) → Privacy(5)
→ Lan(6) → direct-custom(7) → bytedance-ad(8) → … → FINAL,DIRECT
```

后果：同一个主机若同时被上游广告表和 `bytedance-ad.list` 覆盖，**上游先命中**，而上游那张表是 `REJECT`（RST），
我们的 `REJECT-DROP`（丢包防风暴）永远轮不到 ⇒ 黑名单模式下"防重试风暴"的设计意图失效。
建议把 `bytedance-ad.list` 与 `reject-custom.list` 提到广告段之前（一行位置调整，**待 owner 决定**）。

（附：2026-10-08 库①里 pangolin 三个主机 407 次全部命中的是上游 `DOMAIN-KEYWORD,pangolin-sdk-toutiao,REJECT`，
而不是我们 L1 里的同名主机条目 —— 当时手机还没重载到含这些条目的规则集，属于"规则已存在、手机没刷新"的老毛病，
不是引擎优先级问题。库②（19:37 之后，重载过）该族 0 次请求，符合 DROP 之后 SDK 放弃重试的表现。）

## 七、变更记录

- **2026-10-09 立**：三层模型；`bytedance-ad.list` +5 埋点主机（`mon.toutiaocloud.com/.net`、
  `mon3/mon11-misc.fqnovel.com`、`timon.zijieapi.com`）；模块收敛为 14 条（删掉已被 L1 整族拦掉的
  pangolin/pglstatp 分支，MITM 域名 6 → 4）；校验器加 `--against` 分层检查。
