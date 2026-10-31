# Agent Note: ZCode 共享会话手机原型

Status: implemented

## 问题

[手动协议原型](2026-10-31-zcode-native-session-prototype.md) 证明了原 App 会话的核心控制，但直接订阅同一 renderer connection 会与桌面视图竞争。用户要求在 Mac 上接入现有手机页面进行实测，不能复制聊天业务或另起会话执行器。

## 决策

只在私有配置显式给出 `zcodePrototype` 且宿主为 macOS 时注册适配器。固定已核验的 ZCode 3.17.1、本机安装和端口进程、原生测试 session/cwd；默认网关不探测、不启用 ZCode。本轮仅开放绑定的原生测试聊天，不把未验证的任意会话、新建、模型、账号、后台通知能力暴露为已实现。

通过原 renderer 的 React context 找到持有该 session 的 `SessionDataLayer`，显式 attach 取得其引用计数租约。后续读取只查看原 `ConversationProjectionStore.getState()`，复用原生 snapshot/delta、订阅和流式更新，不再自行 subscribe/resync。桌面与手机共用同一原生 store；没有第二套 reducer。被动请求仍检查 original connection/runtime identity，不会 acquire、启动或切换会话。进程或 owner 变化要求用户明确重试；重试也仅采用已打开的原生会话，不自动启动新执行器。

共享 HTTP 鉴权、CSRF、上传账本、Workspace 文件边界、DesktopSessions 读合并/锁/持久化写回执保持原路径。发送时引用宿主文件重新校验 cwd/路径并安全读取，通过原生 attachment API 上传字节生成原生引用，正文不拼文件提示词。手机上传能力显式标记 `uploadFiles`；既有 Claude/DSH 附件规则不扩大。

手机使用 `desktop-sessions.js`，不建立 ZCode 聊天页。共享停止按钮按可选 `stopTarget` 传递用户看到的执行标识；原生适配器发送前再核验。共享审批卡支持可选 `decisions`，展示原生的一次允许和拒绝选项；不开放永久授权。普通提问映射到已有 questions/answers 格式。思考、工具、附件、Markdown 与代码高亮均沿用已有组件。合入已有共享阅读位置修复 `69e2f017`（本分支为 `a783051`），避免 ZCode 重现 DSH 的展开内容滚动重置。

ZCode logo 沿用原 App 资源，网关和远程管理页显示同一后端描述及模型/账号管理说明。底部应用栏按实际容器宽度横向滚动，按钮保留最小宽度，状态刷新保留 scrollLeft；不按 iOS/Android 或固定客户端数量另建布局。

真实测试遵守用户的免费额度约束，每次发送前读取原生执行器的 `snapshot.config`，只使用 `GLM-5.3-Flash / low`。原 App 下拉框改为 Flash/低时，已有执行器仍可能保留旧配置；测试不能仅检查下拉框。同步配置使用原生 `switchModelConfig` 的 `baseRevision` 乐观锁，并在接受后再次读取执行器配置。此规则属于测试流程，不强制改变用户日常会话的模型，也不重跑源码未变且已通过的模型测试。

## 备选方案

继续直接订阅 renderer 会抢占原生视图，已放弃。另建 host 连接仍需要稳定入口，是后续正式适配的候选，不是本机手机验证的前置。复制原生流式 reducer 会扩大跨版本维护面，故复用原 App store。另起 CLI 或修改安装包不符合原 owner 与最小改动约束。

## 验证证据

2026-10-31，原 App 打开同一测试聊天时，反复读取后 SessionDataLayer 仍只有一个 session entry。通过共享 DesktopSessions 读取列表约 126 ms、详情约 88 ms、真实发送确认约 202 ms；原生 AskUserQuestion 经共享 requests/answers 格式回答 accepted。读取不再强制全量 resync。性能数据是单次本机证据，不代表 HTTPS 或所有模型耗时。

协议回归覆盖被动读取不 acquire、共享租约复用/释放、owner 变化、过期停止/审批、端点身份和未知写入不重放。手机契约回归覆盖 opt-in 注册、默认关闭、共享账户/停止投影、原生文件字节、权限选项/问题、上传能力与会话隔离。共享 UI 与上传 HTTP 回归、模块边界检查同步验证；未扩大 22 项既有依赖例外。

一次 HTTPS 附件发送已到达原 App，桌面可见 attachment.md 原生附件，但该轮模型因服务端 429/3009 并发限制失败。适配器现在投影原生 lastError 到共享错误消息和失败状态；下一条明确发送是否可用取原生 inputRouting.mode，不使用 sessionEnded 推断。不会自动重发失败请求或切换模型。

最终安装版本 `2.1.7-test.20261031.zcode3`：包内 374 个 Web 资源与源码匹配，正式网关及四个原客户端进程未变，Test 认证、绑定、网络配置和草稿保留。核验原生执行器 Flash/low 后仅再发一条 HTTPS 附件测试，回执约 1097 ms；模型返回附件内标记 `ZCODE_NATIVE_ATTACHMENT_20261031` 和 `ZCODE_PHONE_FLASH_FINAL_OK`，原 App 同时显示原生附件和回复。局域网重新打开同一聊天，显示已连接并读到相同最新结果；没有为局域网重复发送模型请求。这不是对每条网络路径延迟的普遍保证。

浏览器 320、390、844、1440 CSS 像素宽度验证没有整页横向溢出；应用栏需要时滚动，刷新保留位置，四个 logo 均加载。网关客户端页实际显示 ZCode logo 和描述。此项是浏览器窄屏模拟及 Mac 原生窗口验证，手机真机实操仍由用户验收。末次代码改动对应的协议 Node 11 项、手机契约 Python 6 项通过；既有共享 UI、上传、模块边界检查按修改范围复用结果。

## 影响

这是 Mac Test 原型，不是第四客户端正式发布。需要本机 ZCode 临时 loopback 调试入口和原生测试聊天；不修改 ZCode app.asar。历史范围受原 App 已加载窗口限制；更早内容在原 App 加载。尚未覆盖 Windows/Linux、SSH、多窗口、多工作区、任意冷会话、App 更新迁移、原生附件内置侧栏预览。原 App 被退出后不会由网关自动重启。

连接中断时写回执保持 unknown，不自动重放；手动重建连接只更新 owner 绑定，不改写或重发旧命令。账户身份未核验时沿用共享临时草稿策略，不伪造持久账户归属。网页在前台使用既有自适应刷新，后台隐藏后停止；不新增 ZCode 定时器。

## 相关记录审计

手动协议原型部分重叠并互链，保留其初始证据与被替换的订阅理由；[共享阅读位置](../bug-fix/2026-10-30-shared-chat-reading-state.md) 和 [共享任务状态](2026-10-29-shared-chat-progress.md) 直接复用，决定不变；[原生项目附件](../bug-fix/2026-10-29-native-project-attachments.md) 保留文件可读、原生引用可见与侧栏预览的不同验收标准。没有需要归档或拒绝的其他提案。
