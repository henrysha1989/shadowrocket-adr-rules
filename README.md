# shadowrocket-adr-rules

面向 **AdGuard Home + Shadowrocket** 的个人 DNS / 代理规则仓库，配套一个**规则 review（审核 / 归类）脚本**。

脚本从**真实流量**中筛出候选域名，自动归类为 **拦截 / 直连 / 代理**，再生成订阅下发到客户端；规则随使用自生长，AdGuard Home 与 GitHub 两边自动同步。

> 当前规则量：拦截 **323** 条 / 直连 **92** 条 / 代理 **21** 条。

---

## 设计目标

把流量按用途分成三档，各走各路：

- **拦截**：广告 / 追踪 / 隐私域；
- **直连**：国内低延迟服务、核心 CDN、实时通信信令；
- **代理**：需要经代理访问的境外服务。

难点在于：**「广告」与「核心业务」常常挂在同一批域名根下**。整域一刀切地拦或放，都会误伤业务（典型表现是卡顿、加载失败、请求重试）。所以采用 **白名单保护业务 + 黑名单拦截广告**，并拆成不同优先级的规则集下发。

## 三大通道

| 通道 | 含义 | 落地位置 |
| --- | --- | --- |
| **拦截** | 广告 / 追踪 / 隐私域 | AdGuard Home `user_rules` + `adh-custom.txt` →（CI）`reject-custom.list` |
| **直连** | 国内低延迟服务 / 核心 CDN / 信令 | `direct-custom.list` |
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
                 [ 规则 review 脚本：分类 ]
                 （拦截 / 直连 / 代理，交叉参考公开清单）
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
                                                    客户端（Shadowrocket）
```

- **双源互补**：ADH 覆盖本机 DNS 能看到的一切；Shadowrocket 连接日志补上 ADH **看不到**的代理 / remote-dns 流量。
- **全自动**：无需逐条人工维护，规则随流量自增长；AdGuard Home 与 GitHub 两边自动同步。

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

> `convert.py` 会自动跳过客户端不支持的 `IP-CIDR,` 等语法，并对 `! updated:` 头部做去重。

## 使用方式

在 Shadowrocket（或兼容客户端）中添加以下订阅：

```text
拦截   https://raw.githubusercontent.com/henrysha1989/shadowrocket-adr-rules/main/reject-custom.list
直连   https://raw.githubusercontent.com/henrysha1989/shadowrocket-adr-rules/main/direct-custom.list
代理   https://raw.githubusercontent.com/henrysha1989/shadowrocket-adr-rules/main/proxy-custom.list
```

**规则顺序建议**：`拦截` → `直连` → `代理` → `GEOIP` / `FINAL`。子域规则优先于父域，确保宽泛直连不会「吞掉」精确拦截。

> 国内网络可搭配任意 GitHub Raw 加速前缀以提升拉取成功率。
> 规则更新后，需在客户端**刷新订阅**才会生效。

## 维护说明

- **修改拦截规则**：编辑 `adh-custom.txt`。手工规则**必须写在 `! ===== 自动收集（以下内容由脚本管理，勿手改）=====` 标记之上** —— 脚本的自动收敛只清理标记下方。
- **自动区**：由 review 脚本按真实流量增删；每轮结果会**同时写回 AdGuard Home 与 GitHub**，两边自动同步。
- **本地转译**：`python3 convert.py`（读 `adh-custom.txt` → 写 `reject-custom.list`）。
- **直连 / 白名单**：在 `direct-custom.list` 中以 `DOMAIN-SUFFIX,domain,DIRECT` 形式维护。
- **Shadowrocket 连接日志（数据源）**：把客户端导出的 `proxy-*.db` 放入指定目录，脚本检测到新文件后会**用与 ADH 相同的分类逻辑**并入规则，并顺带报告「客户端仍在拦截、但已被放行」的疑似未刷新 / 误杀域名。

## ⚠️ 注意事项

- `reject-custom.list` 是**生成物**，请勿手改（下次 CI 运行会覆盖）。
- 拦截结果由参考清单 + 本地观测共同判定；已用白名单尽量保护业务。若发现误拦，请提 issue。

## 免责声明

本项目仅用于个人网络环境的广告治理与流量优化，规则来自公开清单与本地观测，请自行评估使用风险。
