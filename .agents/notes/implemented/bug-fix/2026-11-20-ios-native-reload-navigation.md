# Agent Note: iOS 原生刷新按已登记的导航类型放行

Status: implemented

## 问题

iOS Test 的原生“刷新页面”菜单可以打开和点击，但 WKWebView 没有发出新的页面请求。真实 iOS 18.2 模拟器仪表确认：`web.reload()` 的主目标导航使用 `.reload`，而 `sourceFrame.isMainFrame` 为 false。已有原生导航票据虽然正确绑定同一 WebView、代际和 URL，其守卫却只接受原生 `load()` 使用的 `.other`，导致刷新被本机拒绝。

## 决策

`nativeMainNavigation` 同时保存该次原生操作预期的 `WKNavigationType`。`loadNative()` 登记 `.other`，原生刷新登记 `.reload`；非主来源的导航只在类型与票据相同、WebView 相同、代际相同、URL 完全相同且仍属于当前网关时放行。票据保持一次性消费，未登记的非主来源导航仍然拒绝，隔离预览壳与设备路径边界不变。

本记录与[手机信道 Cookie 位置](2026-11-11-mobile-channel-cookie-slot.md)在原生窗口和来源约束上部分重叠；该记录的绑定、Cookie 与回滚规则继续适用。本修复只处理宿主发起的刷新导航，不改变凭据。检索未发现同一刷新原因的活跃提案，没有归档已有记录。

## 备选方案

所有 `.reload` 均放行改动最少，却会把未登记或不同来源的刷新一起授权，因此不采用。仅把允许类型改为 `.other` 或 `.reload` 能修复当前症状，但会允许一个 load 票据被 reload 消费；保存并匹配具体预期类型才能保留原操作边界。

## 验证

- 新增 `scripts/test-ios-native-navigation.py` 原样提取生产票据声明和导航守卫，修改前在合法原生刷新用例失败；修改后十三项通过，覆盖 load/reload、交叉类型、页面链接、无票据、不同 WebView、旧代际、不同 URL、网关及中继设备路径、缺失目标帧。
- iOS 官方 XCUITest 在现有独立模拟器中点击原生刷新。修改前已记录进入 refresh、导航 `.reload` 且来源非主帧，但服务器没有新 GET；修改后相同原生点击触发新页面和脚本 GET，证明刷新实际生效。
- 导航与资源 Node 十三项、既有隔离预览壳 Swift 回归通过。实际 Test App 的软件键盘、草稿输入、原生发送方式选择和中间横滑也完成观察，未向真实网关发送消息。

## 影响

原生刷新能够重新读取当前页面。增加的导航类型只保存在内存，不改变请求、存储或授权格式。验证范围为本机 iOS 模拟器和合成回环网关；没有覆盖实体 iPhone 或真实账号。
