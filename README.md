# shadowrocket-adr-rules —— 小火箭侧（独立项目）

> 2026-10-01 与 ADH 侧**彻底拆成两个仓库**：
> ADH（DNS 层、`adh-custom.txt`）在 [`henrysha1989/adh-rules`](https://github.com/henrysha1989/adh-rules)；
> 本仓库只服务手机 **Shadowrocket**（客户端分流）。两边**互不引用、不共享状态**。

> 当前规则量：拦截 **28** 条 / 直连 **116** 条 / 代理 **57** 条。

## 三张表（订阅给 Shadowrocket）

| 文件 | 动作 | 放在配置的哪一段 |
|---|---|---|
| `reject-custom.list` | `REJECT-DROP`（丢包） | **拦截段最前**（集合动作必须是 `REJECT-DROP`，写成 `REJECT` 会引发 SDK 秒级重连 → 重试风暴，实测 29,700 次/分） |
| `direct-custom.list` | `DIRECT` | **拦截段之前**（自建放行要压过订阅广告表） |
| `proxy-custom.list` | `PROXY` | 代理段（兜底 `FINAL,PROXY` 其实已覆盖，留着便于显式控制） |

每张表都分两段，以 `# ===== 自动收集（以下内容由脚本管理，勿手改）=====` 为界：

- **手工区**（标记之上）：**owner 维护**，脚本只读不写。公司自建系统、Tesla、图片 CDN 这类"必须直连"的就写在这里。
- **自动区**（标记之下）：由 `sr_analyze.py` 从手机 `proxy-*.db` 的证据生成。

订阅地址（前加速站前缀即可直接给手机）：

```
https://git.521989.xyz/https://raw.githubusercontent.com/henrysha1989/shadowrocket-adr-rules/main/reject-custom.list
https://git.521989.xyz/https://raw.githubusercontent.com/henrysha1989/shadowrocket-adr-rules/main/direct-custom.list
https://git.521989.xyz/https://raw.githubusercontent.com/henrysha1989/shadowrocket-adr-rules/main/proxy-custom.list
```

## 模块专区（路径级去广告）

`module/` 放自建 `.module` —— 清单管**域名级**，模块管**路径级**：内容和广告同域时（字节的
`pstatp` / `byteimg` / `snssdk` 就是这种），域名级一刀切会连内容一起拦，只能靠路径分。
**前提是配置里 `[MITM] enable = true` + 手机安装并信任证书。**

| 模块 | 动作 | 作用 |
|---|---|---|
| `module/bytedance-ad.module` | 15 条 URL Rewrite | 字节系公共广告链路：穿山甲接口 / 上报、广告素材与安装包、广告图（`reject-img`）、广告视频 |

不含 `[Rule]`、不含 IP、不含关键字、不跑第三方 JS —— 域名级拦截仍然只在 `bytedance-ad.list`。

安装地址（带自建加速前缀）：

```
https://git.521989.xyz/https://raw.githubusercontent.com/henrysha1989/shadowrocket-adr-rules/main/module/bytedance-ad.module
```

代价、回滚、可疑规则与校验脚本见 [`module/README.md`](module/README.md)。

## 脚本

`sr_analyze.py`（纯标准库，独立运行）—— 读手机导出的 `proxy-*.db`，出体检报告 + 更新三张表的自动区：

```sh
python3 sr_analyze.py                          # 只看报告（不写任何东西）
python3 sr_analyze.py --write                  # 写 direct/proxy 自动区
python3 sr_analyze.py --write --write-reject    # 连拦截表也写（带冲突护栏）
```

报告三件事：

1. **漏网之鱼**：判广告、但手机实际没拦的主机（带次数与次/分）
2. **直连域名滑落到代理**：① 已在直连表却走了代理（规则没生效）② 判直连但不在表里（需新增）
3. **冲突护栏**：要写进拦截表的域先跟直连表（含手工区）对撞，冲突的**一律不写**并列出 ——
   绝不让"拦广告"把正常上网的直连域给拦了

判定口径、状态文件、与 ADH 的边界：见仓库里的 `sr_analyze.说明.md`。

## 规则顺序（配置里必须遵守）

```
[Rule]
DOMAIN-SUFFIX,521989.xyz,DIRECT
RULE-SET,…,direct-custom.list,DIRECT        ← 自建直连表放在**拦截段之前**
RULE-SET,…,reject-custom.list,REJECT-DROP   ← 拦截段最前
RULE-SET,…,AdvertisingLite.list,REJECT
RULE-SET,…,Privacy.list,REJECT-DROP
RULE-SET,…,GlobalMedia.list,PROXY
RULE-SET,…,ChinaMedia/Download/China_Domain,DIRECT
GEOIP,CN,DIRECT
FINAL,PROXY
```

## 维护

- 自动区：跑 `sr_analyze.py`（脚本按手机证据收敛，TTL 90 天）。
- 手工区：直接改文件（owner 说了算，脚本永不删）。
- README 里的规则量：`.github/workflows` 之外由 `update_readme_counts.py` 刷新。
