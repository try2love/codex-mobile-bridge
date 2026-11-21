# HarmonyOS 实验客户端

这是独立安装身份的单入口网页壳：扫码或输入完整网关地址，在 WebView 中登录并使用网关工作台。地址列表只保存规范地址，一次性配对片段不写入地址列表；登录 Cookie 由系统 WebView 管理。返回电脑列表保留现有 WebView，打开另一地址或重新配对前提示确认。

公网地址仅接受 HTTPS；HTTP 限本机回环、localhost 和私有 IPv4。支持网关根地址及精确 `/d/<32 位设备 ID>` 前缀，页面导航限制在所选 origin 与前缀内。证书错误取消加载。列表移除仅删除地址记录，不注销网关设备或清除 WebView Cookie。

当前不提供自动换线、跨地址绑定迁移、后台通知、原生文件上传下载、外部通知跳转或 Bridge 原生 prompt 接口。手机网页现有能力取决于系统 WebView；这不是 Android/iOS 能力的完整移植。

## 构建

使用支持项目配置 `6.0.1(21)` SDK 的 DevEco Studio 打开本目录，等待工程同步，选择 `entry/default`，在 IDE 中配置自己的设备签名后构建。仓库不含签名证书、密码或本机路径。未在本轮安装或运行 DevEco，不提供已构建 HAP。

实验 bundle 为 `io.github.try2love.codexbridge.harmony.experimental`，版本为 `0.1.0-experimental`，不会覆盖其他平台或旧贡献版应用。安装与真实设备扫码、Cookie 持久化、系统返回、键盘、TLS、页面重载行为均待真机验收。

Node 测试执行生产 `GatewayUrl.ets` 的地址规则，使用标准 URL 替身；它不验证 ArkTS 编译、ArkUI 布局或 ArkWeb 的运行行为。

## 来源

工程骨架与最初客户端贡献来自 [Aplier 的 PR #248](https://github.com/try2love/codex-mobile-bridge/pull/248)，源提交 `bda256624c9e2eabc7ba171e57b64aa10efce5ff`。本地适配将其收敛为上述实验范围，使用共享网关网页；保留原作者归属，整体遵循仓库 AGPL-3.0-only 许可及第三方各自许可。
