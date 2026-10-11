# mihomo / Clash 规则集（`clash-ios` 分支）

给 iOS Clash 客户端（Hako-mihomo 内核）用的 **classical 文本** 规则集，由 **main 分支的同名
Shadowrocket 规则表**自动同步而来：只去掉第 3 列的策略、丢掉注释行，**类型与值逐字不动**。

| 文件 | 条数 | 来源（main） |
|---|---|---|
| `bytedance-ad.list` | 261 | `bytedance-ad.list` |
| `soul-ad.list` | 17 | `soul-ad.list` |
| `reject-custom.list` | 10 | `reject-custom.list` |

转换规则：

```
DOMAIN-SUFFIX,x,REJECT-DROP        →  DOMAIN-SUFFIX,x
DOMAIN,x,REJECT-DROP               →  DOMAIN,x
DOMAIN-KEYWORD,x,REJECT-DROP       →  DOMAIN-KEYWORD,x
IP-CIDR,x,REJECT-DROP,no-resolve   →  IP-CIDR,x,no-resolve
```

**动作不写在文件里**，由引用方决定（配置里写 `RULE-SET,ByteDanceAd,REJECT`）。
放这里是因为 mihomo 的 rule-provider 文本不接受第 3 列策略。

## 同步策略

本分支**长期独立、不合并回 main**，靠自动同步跟随：

- **触发**：main 上 `bytedance-ad.list` / `soul-ad.list` / `reject-custom.list` 任一被 push
  （宿主 cron 每 30 分钟跑 `sr_analyze.py` 写这三张表的自动区）→ GitHub Action
  [`.github/workflows/sync-clash-ios.yml`](../../actions/workflows/sync-clash-ios.yml) 起一轮。
- **Action 只做两件事**：从本分支取 `mihomo/sync.mjs`，然后执行它。同步逻辑与数据放在同一个
  分支里，main 那边只有一个 workflow 文件。
- 也可手动跑：Actions → `sync clash-ios rule sets` → Run workflow。
- **每日巡检**：每天 11:17（北京）`schedule` 跑一次 `--check`。发现分支落后就让这次运行失败，
  并自动开一个标题为 `clash-ios 同步失败` 的 issue 通知；同步恢复正常后自动把该 issue 收掉
  （同一问题只续评论，不刷屏）。想手动验一遍告警链路：Run workflow 时把 `mode` 选 `check`。

`sync.mjs` 用法：

```sh
node mihomo/sync.mjs           # 转换 + 推送（有变化才提交，三个文件合成一次提交）
node mihomo/sync.mjs --check   # 只报告是否落后（退出码 0=一致，2=落后，给 CI 判定用）
node mihomo/sync.mjs --dry     # 转换比对，但不推
```

**护栏**：任一表转换后为空、或条数比线上腰斩以上，立刻中止并保留线上旧版 —— 一次坏数据不会
把规则集打空。提交信息带 main 的 sha（`chore(mihomo): sync from main@xxxxxxx`），便于回溯。

## 为什么不是 MRS

iOS 内核的 MRS 校验（`bind/clash/mrs_validation.go`）只支持 `domain` 与 `ipcidr` 两种
behavior，`classical` 会直接报 `MRS format is unsupported for provider behavior classical`；
而这三张表是混合类型（DOMAIN-SUFFIX / DOMAIN / DOMAIN-KEYWORD / IP-CIDR），装不进 `domain` 集。
总共 288 条，也用不上 MRS 的性能收益（那是给几万条的大表准备的）。
所以这里用 `behavior: classical` + `format: text`。

## 引用方式（Clash 配置侧）

```yaml
rule-providers:
  ByteDanceAd:
    type: http
    behavior: classical
    format: text
    interval: 86400
    url: "https://raw.githubusercontent.com/henrysha1989/shadowrocket-adr-rules/clash-ios/mihomo/bytedance-ad.list"
    path: ./rules/bytedance-ad.list
```

> 本分支长期独立，配置 URL 里的 `clash-ios/` 会一直需要；**不要把它合并回 main**。
