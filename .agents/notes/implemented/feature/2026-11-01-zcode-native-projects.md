# Agent Note: ZCode 多项目原生会话绑定

Status: implemented

## 问题

手机只列出原型锚点的工作区，不能登记新项目。把新目录提交给原会话的 SessionDataLayer 会把订阅仍发往旧工作区；仅补一个目录按钮不能解决归属。

## 决策

目录选择、创建、身份复检和账号范围继续使用共享项目服务。项目目录由 ZCode 自己的 projectService 登记，创建前 prepareSession 固化 cwd 与原生 projectId。列表和会话目标从原 App 项目树读取，不能相信手机提交的 cwd。原生首次消息创建仍使用同一个 renderer clientId 和原 App 的命令接口。

显式接入与创建按工作区租用原 App 的 workspaceConnectionRegistry，同一个工作区复用同一个 SessionDataLayer。已安装的 3.17.1 没有导出该注册表，原型通过 CDP 检查原生函数模块作用域取得现存函数；只在显式操作时解析，不复制实现、不修改安装包，不能确认唯一匹配时拒绝。每个会话保留自己的工作区和运行世代，漂移只能显式重新绑定。

## 相关决定审计

[原生工作模式和新建聊天](../../implemented/feature/2026-11-01-native-work-modes-and-zcode-create.md)部分重叠，扩展同项目创建为多项目；原 command ID、首条消息与未知回执不重放约束继续适用。[原生手机原型](../../implemented/feature/2026-10-31-zcode-mobile-test.md)部分重叠，共享原生订阅租约的决定不变。不吸收其真机和通知验证事实。

## 备选方案

另建 transport 和投影可以少做运行时解析，但会与桌面在同一个 connection/topic 上互相替换订阅，所以不采用。切换桌面项目能让原 App 创建数据层，但会打断桌面当前查看位置，也不能保证跨项目并存，因此直接租用原注册表。

## 验证

20 项原生协议 Node 检查、32 项适配器/共享项目 Python 检查和模块边界检查通过。新增目录在原生项目树可见；共享管理器首条消息创建回执 1059 ms，GLM-5.3-Flash/low 返回短标记，新 cwd 与所选目录一致，旧会话仍可读。新 Bridge 实例先显示 detached 历史，显式 resume 后恢复 idle，目录未变；此重连不发送新消息。目录替换、跨项目附件取错根、未绑定写入、注册表非唯一匹配均有拒绝检查。共享项目弹窗在 390/1280 像素各通过 13 项浏览器检查，未向真实网关发送。最终 Mac Test zcode11 已安装，375 个 Web 文件、冻结模块和原生传输资源与本轮源码匹配；正式网关、原生客户端进程、账号、网络设置和草稿保留。局域网页面显示原生登记的新项目及共享更换目录入口；HTTPS 显式继续后恢复同一目录的原生会话。手机 Test build 42 已编译，iPhone 未连接，不把浏览器检查算作手机实测。

已安装版本要求 reuseExistingRoot 只带 roots/hostId，不得同时带 primaryRoot；prepareSession 的 projectId 与 draftId 互斥。两次参数拒绝发生在 firstInput 派发前，原未知请求 ID 保留不重放，修正后才创建一次模型消息。

## 影响

模块作用域是版本相关内部入口，仅限当前 macOS Test 原型。原函数或唯一服务无法确认时需要明确报不可用，不能静默退回旧目录或替代 CLI。跨工作区运行时增加后，原有账号切换安全检查可能保持保守拒绝，不能为补功能而放宽活动任务门禁。

共享项目 HTTP 路由也包含 ZCode，目录浏览、登记与信任继续使用原认证及 CSRF 校验。实际 HTTP 夹具确认 ZCode 请求能到达共用处理器；不能只用 manager 内部调用代替路由验证。
