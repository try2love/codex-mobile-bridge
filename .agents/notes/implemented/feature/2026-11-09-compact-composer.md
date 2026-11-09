# 紧凑聊天输入区

Status: implemented; native builds and device acceptance pending

## 决策与范围

只改共享输入区及其原生附件入口。按本次委托移除发送方式下拉和收起输入区功能，不沿用 Issue #147 评论中保留这两项的建议。不处理 Issue #144 的执行过程折叠，不改时间线结构、通知默认值、provider 或运行中的网关。

`CompactComposer` 负责加号菜单、`$` Skill 入口、`/plan` 与 `/goal` 候选、可移除的模式标签。实际 Skill 选择仍由原有对话框负责，Plan/Goal 仍由 `WorkModes` 和客户端能力控制。权限、模型及思考强度、上下文圆环留在工具栏。

发送空闲时为 `send`；运行中且 queue 与 sendModes.queue 未被禁用时为 `queue`，否则为 `send`，仍检查该模式的能力；排队请求不携带仅即时发送可用的 workMode。不自动选择 steer。隐藏传输字段保存当前自动结果，不再恢复旧草稿中显式的发送方式。原有正文、引用、附件、工作模式、持久发送 ID 及未知结果核对逻辑继续使用；状态变化导致待确认请求正文不匹配时保持阻塞，不重放旧写入。

## 原生附件

相机、照片、文件使用独立 input，保留调用时的会话身份。取消不修改草稿；旧会话或发送锁下返回的选择不加入附件。

Android 13+ 使用系统 Photo Picker，旧版使用原生 image GET_CONTENT；文件继续使用 OPEN_DOCUMENT。相机使用 ACTION_IMAGE_CAPTURE 和私有 CameraProvider 的临时 JPEG URI；仅本轮精确 URI 允许自有 provider 返回，其他自有 provider 仍拒绝。保留 origin、WebView 和 generation 校验；取消时清理文件并撤销授予。

iOS 18.4+ 使用已有 WKOpenPanel 接口分发到图片限定的 PHPicker、UIImagePickerController 相机或 UIDocumentPicker；旧版继续由 WebKit 根据 input 的 accept/capture 调起系统控件。相机 JPEG 编码、写入在后台队列，返回前复核 ticket、generation、origin。没有增加图库读取授权。

## 验证与性能

- Node 回归保护草稿、附件、自动队列、未知写入与能力检查；原生源码合约另见 `mobile/tests/upload-pickers.test.cjs`。
- `tests/browser/compact-composer.spec.cjs` 使用生产页面和合成会话检查 Codex/Claude、375×812 和 768×1024、明暗主题、真实 filechooser 事件、快捷键及能力边界。
- 资源合并进已有 attachments.js 与 style.css，不增加公开请求；候选列表仅含当前入口，不加载模型/Skill 目录。浏览器断言 `/` 操作产生零 API 请求；Skill 复用原有目录缓存。没有新增轮询或输入区 MutationObserver。
- Android 构建入口在本机报缺少 `.tmp/mobile-tools/jdk`；本机为 Windows，无 Xcode。原生源码检查不代表 APK/iOS 编译、相机权限或真机交互已通过。按仓库 CI 的移动构建和 `tests/chat/compact-composer.md` 完成后才可验收原生部分。

使用记录与当前检查结果见提交说明及本地 `.tmp/ui-alignment-20261109/`，截图不提交。
