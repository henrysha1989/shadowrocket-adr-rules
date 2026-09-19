# shadowrocket-adr-rules

基于 **AdGuard Home** 与 **Shadowrocket** 构建的自动化“拦截 / 直连 / 代理”自净化网络规则系统。

针对字节跳动系（抖音、番茄小说）、七猫小说等高频商业化应用，通过白名单精细保护核心音视频切片与信令通道，阻断穿山甲联盟、拼多多 DSP 及行为反作弊埋点，实现全自动抓取、语法转译与多端静默分发。

---

## 架构特性

* **双通道自净化闭环**：NAS 端自动化脚本定期轮询 AdGuard Home 放行记录，区分提取广告特征与国内低延迟音视频直连特征，实现规则自增长。
* **核心业务免打扰**：针对实时通信信令（WebRTC / ByteRTC）、媒体分发 CDN 及小说正文接口实施白名单放行，避免误杀导致的切视频卡顿、直播间握手失败或章节翻页空白。
* **自动语法转译**：利用 GitHub Actions 监听规则更新，自动将 AdGuard Home 过滤语法（`||domain^`）转译为 Shadowrocket 标准 `reject-custom.list`（`DOMAIN-SUFFIX,domain,REJECT`）[cite: 10, 11]。
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
          │ 
          ├── 命中直连特征 ──► 追加至 direct-custom.list (直连源)
          │ 
          └── 命中广告特征 ──► 写入 adh-custom.txt (拦截源)
                                       │
                                       ▼ 触发 GitHub Actions
                              [转换引擎 (convert.py)]
                                       │ 编译生成标准规则
                                       ▼
                              [GitHub: reject-custom.list]
                                       │
          ┌────────────────────────────┴───────────────────────────┐
          ▼ (自建 Worker 加速拉取)                                   ▼ (自建 Worker 加速拉取)
[Shadowrocket: reject-custom.list]                      [Shadowrocket: direct-custom.list]
