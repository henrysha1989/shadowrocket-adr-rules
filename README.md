# shadowrocket-adr-rules

基于 **AdGuard Home** 与 **Shadowrocket** 构建的自动化「拦截 / 直连 / 代理」DNS 与代理规则系统。

面向字节跳动系（抖音、番茄小说）、七猫小说、拼多多等高频商业化应用：通过**白名单**精细保护核心音视频切片与实时通信信令，同时**精准阻断**穿山甲广告联盟、DSP 投放及行为埋点，实现「抓取 → 转译 → 多端分发」的全自动闭环。

---

## ✨ 核心特性

- **双通道自净化闭环**：NAS 侧脚本定期轮询 AdGuard Home 的放行记录，自动区分「广告特征」与「国内低延迟直连特征」，规则随使用自增长。
- **核心业务免打扰**：对 WebRTC / ByteRTC 实时信令、媒体分发 CDN、小说正文接口实施白名单放行，避免误杀造成的**切流卡顿**、直播间握手失败或章节翻页空白。
- **自动语法转译**：GitHub Actions 监听规则变更，自动将 AdGuard 过滤语法（`||domain^`）编译为 Shadowrocket 标准规则集。
- **多端免运维同步**：规则以纯文本订阅分发，移动端小火箭定时静默更新即可生效。

---

## 🏗️ 工作原理

```text
[手机 / 家庭网络设备]
          │
          ▼ 发起 DNS 请求
[AdGuard Home 本地 DNS 过滤]
          │
          ▼ 每 6 小时轮询查询日志
[NAS 自动化脚本 adh_gist_sync.py]
          │
          ├── 命中直连特征 ──► 追加至 direct-custom.list（直连源）
          │
          └── 命中广告特征 ──► 写入 adh-custom.txt（拦截源）
                                       │
                                       ▼ 触发 GitHub Actions
                              [convert.py 转译引擎]
                                       │ 生成标准规则
                                       ▼
                              reject-custom.list
                                       │
          ┌────────────────────────────┴───────────────────────────┐
          ▼                                                         ▼
[Shadowrocket: reject-custom.list]                      [Shadowrocket: direct-custom.list]
```

---

## 📁 目录结构

| 文件 | 作用 | 维护方式 |
| --- | --- | --- |
| `adh-custom.txt` | **拦截规则源**（AdGuard 语法 `||domain^`） | 手工区（标记上方）由人工维护；自动区（标记下方）由脚本管理 |
| `reject-custom.list` | Shadowrocket **拦截**规则集 | 由 `convert.py` 自动生成，**请勿手改** |
| `direct-custom.list` | Shadowrocket **直连**规则集 | 脚本 / 人工维护 |
| `proxy-custom.list` | Shadowrocket **代理**规则集 | 按需维护 |
| `convert.py` | AdGuard → Shadowrocket 语法转译引擎 | 核心脚本 |
| `.github/workflows/convert.yml` | CI：监听 `adh-custom.txt` 变更并自动重生成拦截规则集 | 自动化 |

> 当前规则量：拦截 **291** 条 / 直连 **89** 条 / 代理 **15** 条。

---

## 🔁 语法转译对照

| AdGuard Home（源） | Shadowrocket（生成） | 含义 |
| --- | --- | --- |
| `\|\|example.com^` | `DOMAIN-SUFFIX,example.com,REJECT` | 拦截域名及其子域 |
| `@@\|\|example.com^` | `DOMAIN-SUFFIX,example.com,DIRECT` | 白名单放行 |
| `\|\|keyword*^` | `DOMAIN-KEYWORD,keyword,REJECT` | 按关键字拦截 |
| `@@\|\|keyword*^` | `DOMAIN-KEYWORD,keyword,DIRECT` | 按关键字放行 |
| `! 注释` | `# 注释` | 注释透传 |

`convert.py` 会自动跳过 `IP-CIDR,` 等小火箭不支持的语法，并对 `! updated:` 头部去重。

---

## 🚀 使用方式

在 Shadowrocket（或任意支持的客户端）中添加以下订阅：

```text
# 拦截
https://raw.githubusercontent.com/henrysha1989/shadowrocket-adr-rules/main/reject-custom.list
# 直连
https://raw.githubusercontent.com/henrysha1989/shadowrocket-adr-rules/main/direct-custom.list
# 代理
https://raw.githubusercontent.com/henrysha1989/shadowrocket-adr-rules/main/proxy-custom.list
```

> 国内网络可搭配任意 GitHub Raw 加速前缀（如 `https://<加速域名>/https://raw.githubusercontent.com/...`）以提升拉取成功率。

**规则顺序建议**：拦截 → 直连 → 代理 → `GEOIP`/`FINAL`。子域规则的优先级高于父域，确保宽泛直连不会"吞掉"精确拦截。

---

## 🛠️ 维护说明

- **修改拦截规则**：编辑 `adh-custom.txt`，**手工规则必须写在 `! ===== 自动收集 … =====` 标记之上**（脚本的自动收敛只清理标记下方内容）；提交后 Actions 会自动重生成 `reject-custom.list`。
- **本地转译**：`python3 convert.py`（读取 `adh-custom.txt`，输出 `reject-custom.list`）。
- **直连 / 白名单**：在 `direct-custom.list` 中以 `DOMAIN-SUFFIX,domain,DIRECT` 形式维护。

---

## ⚠️ 免责声明

本项目仅用于个人网络环境的广告治理与流量优化，规则来自公开清单与本地观测，请自行评估使用风险。
