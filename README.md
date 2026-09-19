# shadowrocket-adr-rules

基于 **AdGuard Home** 与 **Shadowrocket** 构建的自动化去广告与防卡顿补强规则集。

针对字节跳动系（抖音、番茄小说）、七猫小说等高频商业化 App，通过白名单精细保护核心音视频与正文加载通道，同时拦截穿山甲联盟、拼多多 DSP 及行为反作弊埋点，实现多端全自动同步与静默更新。

---

## 架构特性

* **动态自净化闭环**：NAS 端自动化脚本定期抓取 AdGuard Home 放行记录，基于行为特征提取漏网推广域名，自动同步至云端仓库。
* **业务白名单防护**：内置音视频切片分发、CDN 测速探针及正文接口放行规则，避免误杀导致短视频预加载缓冲、切视频卡顿或章节加载失败。
* **全自动语法转译**：利用 GitHub Actions 监听规则更新，自动将 AdGuard Home 过滤语法（`||domain^`）转译为 Shadowrocket 标准分流规则（`DOMAIN-SUFFIX` / `DOMAIN-KEYWORD`）。
* **多端免运维同步**：通过自建反代加速通道，移动端小火箭开箱即用，定时静默更新生效。

---

## 自动化流水线流程

```text
[手机终端 / 家庭网络设备]
          │
          ▼ 触发网络请求
[AdGuard Home (本地 DNS 过滤)]
          │
          ▼ 定时轮询查询日志 (每 6 小时)
[NAS 自动化收集脚本 (adh_gist_sync.py)]
          │ 提取未拦截的广告与追踪特征，并剔除白名单
          ▼ 自动推送
[GitHub: adh-custom.txt]
          │
          ▼ 触发 GitHub Actions
[转换引擎 (convert.py)]
          │ 自动编译并生成规则集
          ▼
[GitHub: shadowrocket-custom.list]
          │
          ▼ 通过自建 Worker 节点加速拉取
[Shadowrocket 客户端 (RULE-SET)]
