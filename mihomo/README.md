# mihomo / Clash 规则集（`clash-ios` 分支）

给 iOS Clash 客户端（Hako-mihomo 内核）用的 **classical 文本** 规则集，由 **main 分支的同名
Shadowrocket 规则表**转换而来：只去掉第 3 列的策略、丢掉注释行，**类型与值逐字不动**。

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
放在这里是因为 mihomo 的 rule-provider 文本不接受第 3 列策略。

## 为什么不是 MRS

iOS 内核的 MRS 校验（`bind/clash/mrs_validation.go`）只支持 `domain` 与 `ipcidr` 两种
behavior，`classical` 会直接报 `MRS format is unsupported for provider behavior classical`；
而这三张表是混合类型（DOMAIN-SUFFIX / DOMAIN / DOMAIN-KEYWORD / IP-CIDR），装不进 `domain` 集。
总共 288 条，也用不上 MRS 的性能收益（那是给几万条的大表准备的）。
所以这里用 `behavior: classical` + `format: text`。

## main 是唯一事实来源

这份目录是**派生产物**，不要手改。main 上那三张表更新后（`sr_analyze.py` 会写自动区），
在仓库根目录重跑：

```sh
sh mihomo/convert.sh          # 需要 curl
```

脚本会重新拉 main 的三张表、去策略列、覆盖本目录的同名文件。

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

> 若本分支合并回 main，引用 URL 里的 `clash-ios/` 需要去掉。
