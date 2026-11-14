# Codex 聊天内 visualize

Codex 聊天正文中的完整 `visualize` 内容引用在原位置显示交互内容；共享 Web 同时覆盖浏览器与手机 App。解析支持绝对 HTML 路径、`title` 和 `mode: "wide"`，代码块、行内代码与用户消息保持文本。消息分页及正文展开同时提供文件描述符。

## 文件与宿主边界

- `GET /api/sessions/{thread}/visualizations/{id}` 返回 UTF-8 源码及内容摘要。ID 来源于该聊天助手消息中的完整引用；请求时重新读取完整会话视图核验，授权与时间线展示窗口独立。保存历史前缀沿用现有合并规则，完整快照删除的引用同步失效。文件限定在执行主机的项目目录或 Codex visualizations 目录，沿用 Workspace 的防符号链接读取与 1 MiB 上限。
- 引用解析统一接收助手消息文本；原生历史与规范化会话分别提供文本，无须互相转换数据结构。被动读取使用原会话及执行主机；SSH 请求由 `visualize_payload.read_request` 构造，模块源码集中在 `worker_source` 按 Python 语法解析依赖导入，复用本机工作区读取规则。资源清单测试覆盖载荷源文件，会话测试隔离执行实际生成的读取请求。中继仅转发允许的 GET，并在设备作用域提供固定预览壳。
- `/visualize-shell` 单独鉴权、采用 opaque-origin sandbox；只放行内联脚本与固定 CDN 的静态脚本/样式/字体，API 网络、子帧、表单和原生权限仍受隔离。普通 HTML 预览沿用原策略。Android/iOS 仅增加这一精确预览 URL；手机 App 需包含本轮原生导航改动。

## 交互与生命周期

- 运行时提供主题变量、基础控件样式、标签切换、提示与图标加载，兼容 `window.openai.widgetState`、`setWidgetState`、`theme`、`notifyIntrinsicHeight` 和 `sendFollowUpMessage`。
- 图表按可见区域加载并报告高度。全屏在原 dialog 容器进入 top layer，iframe 始终保留原父节点。内联 dialog 保持 closed 状态，由样式显示，手机返回处理只看到真正进入全屏的 open dialog。
- `setWidgetState` 在宿主保存成功后确认，单项上限 16 KiB，最多保留 100 项；sessionStorage 按网关、聊天、消息、文件 ID 与内容摘要隔离。消息重绘与页面刷新可恢复已保存状态。任意脚本内存与普通控件状态随全屏保留；跨消息重绘的恢复通过 widgetState 接口完成。
- `sendFollowUpMessage` 只展示提问内容及“放入输入框”按钮，宿主按钮复用 `bridge-compose-prompt` 和现有草稿/发送流程。切换聊天、销毁节点时取消请求、移除 iframe 与监听器。

## 验证

Python 回归覆盖引用解析、路径/大小/编码/符号链接边界、被动会话与保存历史核验、展示窗口与授权独立、独立 SSH 源码执行，以及网关/中继鉴权和 CSP。浏览器回归在 Chromium 与 WebKit 的宽窄视口执行生产渲染和运行时，检查交互、主题、全屏脚本内存、保存状态恢复、跨消息状态隔离、提问草稿、重试和网络隔离。

Android 已由用户真机实测通过；iOS 已完成源码审阅，真机交互由对应平台单独验收。
