# 手机 App 源码快照

本目录的 Android / iOS 原生容器打开配套独立网关的 HTTPS 入口，聊天页面由 `app/web/` 提供。当前源码版本为 Android 1.0.24（25）、iOS 1.0.29（30）。本次贡献未构建、签名安装或进行真机验证。

## 连接与功能

在电脑助手生成入口后，手机点击主页面的扫码按钮，核对地址并连接，也可手动填写 HTTPS 根入口。登录使用网关账号密码，网页 Cookie 支持“记住登录”。临时入口与固定域名分别保存；临时域名变化后需重新连接。

原生容器包含图片选择、下载列表、下载进度、文件分享、外部链接处理及连接错误提示。网页同时提供项目与聊天管理、模型与技能选择、提问和权限回复。Android 包含任务提醒服务；iOS 使用实验性 TaskMonitor，详见 [后台任务提醒](../mobile/ios/TaskMonitor/README.md)。电脑、Codex 与网关需保持运行。

连接入口必须为 HTTPS 根地址，例如 `https://codex.example.com`；不接受账号密码、子路径或查询参数。此扩展手机端与仓库根目录的 v2 手机端、配对协议和网关分别维护，互通尚未验证。

## Android 本地构建

使用 JDK 17、Android SDK 35、Android Gradle Plugin 8.9.2 和 Gradle 8.11.1；最低 Android 8.0（API 26）。工程没有附带 Gradle Wrapper。

从本扩展目录进入 `mobile/android`，在已安装工具链的环境运行：

```sh
gradle --no-daemon :app:assembleDebug
```

产物为 `mobile/android/app/build/outputs/apk/debug/app-debug.apk`。调试签名由构建环境生成，跨环境构建可能无法覆盖安装。正式分发需自行配置签名，不将签名密钥提交仓库。

## iOS 本地构建

工程为 `mobile/ios/CodexMobile.xcodeproj`，共享 Scheme 为 `CodexMobile`。最低 iOS 16，构建需要 macOS 和 Xcode 16 或更新版本。在本扩展的 `mobile/ios` 目录运行：

```sh
xcodebuild -project CodexMobile.xcodeproj -scheme CodexMobile \
  -configuration Release -sdk iphoneos -destination 'generic/platform=iOS' \
  -derivedDataPath build CODE_SIGNING_ALLOWED=NO CODE_SIGNING_REQUIRED=NO \
  CODE_SIGN_IDENTITY="" build
bash package-trollstore.sh
```

脚本生成 `mobile/ios/package/CodexMobile-unsigned.ipa`。文件名沿用历史命名，当前脚本会执行 ad-hoc 签名并写入 TrollStore 所需的私有 entitlement。它不是普通 Apple 开发者签名或 App Store 安装包，后台辅助进程与通知依赖支持这些权限的环境。后台持续运行、网页恢复和通知展示均需真机核对。

## 工作流模板

[scripts/mobile-packages.workflow.yml](../scripts/mobile-packages.workflow.yml) 保留原有手动构建流程，仅响应 `workflow_dispatch`。它不在仓库根目录的 `.github/workflows/` 中，因此本次 PR 不会启用该流程。

若单独维护此扩展，可把本扩展作为仓库根目录，并将模板复制为该仓库的 `.github/workflows/mobile-packages.yml`。若要接入上游工作流，需另行调整检出目录、工作路径与 artifact 路径。构建成功只说明生成了产物，不代表真机功能通过。

## 后续核对

设备核对重点包括扫码权限、登录持久化、地址切换、文件续传与分享、前后台恢复、网络中断、任务提醒去重及关闭提醒后的停止行为。当前快照未完成这些验收。
