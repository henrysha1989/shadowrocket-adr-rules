# shadowrocket-adr-rules

面向 **AdGuard Home + Shadowrocket** 的个人「拦截 / 直连 / 代理」规则仓库。

上游数据来自**真实流量**（AdGuard Home 查询日志 + Shadowrocket 连接日志），经分类后自动生成三份订阅：**精准拦截**广告联盟 / DSP 投放 / 行为埋点，**精细放行**核心音视频切片与实时通信信令，其余境外服务走代理。规则随使用自生长，订阅端自动更新。

> 当前规则量：拦截 **310** 条 / 直连 **90** 条 / 代理 **16** 条。

---

## 为什么需要它

字节系（抖音、番茄小说）、七猫、拼多多等应用，把「广告」和「核心业务」放在了**同一批域名根**下：

- `pangle` / `pangolin` / `dsp` / `applog` / `analytics` … 是**广告与埋点**，要拦；
- `*.douyinvod.com` / `*.amemv.com` / `*rtc*.com` / 小说正文接口 … 是**音视频与信令**，误拦会直接表现为**直播卡顿、章节空白**。

一刀切地「整域拦截」或「整域直连」都会翻车。本仓库的做法：用**白名单**保护业务、用**黑名单**拦截广告，并拆成不同优先级的规则集下发。

## 三大通道

| 通道 | 含义 | 落地位置 |
| --- | --- | --- |
| **拦截** | 广告 / 追踪 / 隐私域 | AdGuard Home `user_rules` + `adh-custom.txt` →（CI）`reject-custom.list` |
| **直连** | 国内低延迟媒体 / 核心 CDN / 信令 | `direct-custom.list` |
| **代理** | 需经代理的境外服务 | `proxy-custom.list` |

## 工作原理

```text
      ┌───────────────┐        ┌────────────────────────┐
      │ AdGuard Home  │        │ Shadowrocket 连接日志   │
      │   查询日志     │        │  proxy-*.db（偶尔导出） │
      └──────┬────────┘        └───────────┬────────────┘
             │ 直连 / 本机解析流量           │ 代理 / remote-dns 流量
             └───────────────┬─────────────┘
                             ▼
                 [ 分类：拦截 / 直连 / 代理 ]
                             │
        ┌────────────────────┴─────────────────────┐
        ▼                                           ▼
  AdGuard Home user_rules                    GitHub 仓库（本仓库）
  （`||d^` / `@@||d^`，即时生效）               ├─ adh-custom.txt   （拦截源）
                                              ├─ direct-custom.list
                                              ├─ proxy-custom.list
                                              └─ GitHub Actions
                                                   └─ convert.py → reject-custom.list
                                                         │
                                                         ▼  订阅
                                             Shadowrocket（手机）
```

- **双源互补**：ADH 覆盖本机 DNS 能看到的一切；Shadowrocket 日志补上 ADH **看不到**的代理 / remote-dns 流量。
- **全自动**：无需逐条人工维护，规则随流量自增长；两端（AdGuard Home 与 GitHub）自动同步。

## 仓库文件

| 文件 | 作用 | 谁维护 |
| --- | --- | --- |
| `adh-custom.txt` | **拦截规则源**（AdGuard 语法 `\|\|domain^`） | 手工区（标记上方）人工；自动区（标记下方）脚本 |
| `convert.py` | AdGuard → Shadowrocket 语法转译引擎 | 核心脚本 |
| `reject-custom.list` | Shadowrocket **拦截**集（生成物） | `convert.py` 自动生成，**勿手改** |
| `direct-custom.list` | Shadowrocket **直连**集 | 脚本 / 人工 |
| `proxy-custom.list` | Shadowrocket **代理**集 | 按需维护 |
| `update_readme_counts.py` | 刷新本 README 的「当前规则量」行 | CI 调用 |
| `.github/workflows/convert.yml` | CI：规则变更即转译 + 刷计数 | 自动化 |

## 规则语法

| AdGuard Home（源） | Shadowrocket（生成） | 含义 |
| --- | --- | --- |
| `\|\|example.com^` | `DOMAIN-SUFFIX,example.com,REJECT` | 拦截该域及其子域 |
| `@@\|\|example.com^` | `DOMAIN-SUFFIX,example.com,DIRECT` | 白名单放行 |
| `\|\|keyword*^` | `DOMAIN-KEYWORD,keyword,REJECT` | 按关键字拦截 |
| `@@\|\|keyword*^` | `DOMAIN-KEYWORD,keyword,DIRECT` | 按关键字放行 |
| `! 注释` | `# 注释` | 注释透传 |

> `convert.py` 会自动跳过小火箭不支持的 `IP-CIDR,` 等语法，并对 `! updated:` 头部做去重。

## 使用方式

在 Shadowrocket（或兼容客户端）中添加以下订阅：

```text
拦截   https://raw.githubusercontent.com/henrysha1989/shadowrocket-adr-rules/main/reject-custom.list
直连   https://raw.githubusercontent.com/henrysha1989/shadowrocket-adr-rules/main/direct-custom.list
代理   https://raw.githubusercontent.com/henrysha1989/shadowrocket-adr-rules/main/proxy-custom.list
```

**规则顺序建议**：`拦截` → `直连` → `代理` → `GEOIP` / `FINAL`。子域规则优先于父域，确保宽泛直连不会「吞掉」精确拦截。

> 国内网络可搭配任意 GitHub Raw 加速前缀以提升拉取成功率。
> 规则更新后，需在手机上**刷新订阅**才会生效。

## 维护说明

- **修改拦截规则**：编辑 `adh-custom.txt`。手工规则**必须写在 `! ===== 自动收集（以下内容由脚本管理，勿手改）=====` 标记之上** —— 脚本的自动收敛只清理标记下方。
- **自动区**：由上游脚本按真实流量增删；每轮结果会**同时写回 AdGuard Home 与 GitHub**，两边自动同步。
- **本地转译**：`python3 convert.py`（读 `adh-custom.txt` → 写 `reject-custom.list`）。
- **直连 / 白名单**：在 `direct-custom.list` 中以 `DOMAIN-SUFFIX,domain,DIRECT` 形式维护。
- **Shadowrocket 连接日志（数据源）**：把手机导出的 `proxy-*.db` 放入指定目录，脚本检测到新文件后会**用与 ADH 相同的分类逻辑**并入规则，并顺带报告「手机仍在拦截、但已被放行」的疑似未刷新 / 误杀域名。

## ⚠️ 注意事项

- `reject-custom.list` 是**生成物**，请勿手改（下次 CI 运行会覆盖）。
- 拦截桶由参考清单 + 本地观测共同判定；已用白名单尽量保护核心业务。若发现误拦，请提 issue。

## 免责声明

本项目仅用于个人网络环境的广告治理与流量优化，规则来自公开清单与本地观测，请自行评估使用风险。
