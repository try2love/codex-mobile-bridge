# Web 与手机共享界面

会话菜单在手机端以 200 ms 滑入、180 ms 滑出，电脑端以 120 ms 淡入淡出；动画仅改变位移和透明度。减少动态效果模式直接切换。手机系统栏按遮罩透明度同步暗化，动画期间约每秒更新 30 次，结束后停止逐帧采样。

`index.html` 是浏览器入口，手机外壳也加载同一页面。脚本仍按 HTML 中的顺序加载，共享现有全局对象。

| 目录 | 职责与入口 |
| --- | --- |
| `shell/` | `app.js` 组织页面启动、网关请求与模块交互；`style.css` 提供基础布局。 |
| `features/chat/` | 会话列表、消息时间线、输入与附件、权限和工作模式。 |
| `features/accounts/` | 官方账号、API 接入、额度及客户端账号管理。 |
| `features/clients/` | 应用切换、启用管理与 Claude/DeepSeek 会话界面。 |
| `features/workspace/` | 文件与下载、源码高亮、PDF/HTML 预览、Git、终端、侧边聊天、子任务和分屏工作台。 |
| `features/settings/` | `presentation.js` 管理本地外观偏好。 |
| `shared/` | 翻译、Markdown、图片查看器与共享视觉样式。 |
| `hosts/` | 浏览器、iOS/Android App 的身份和原生布局适配；不参与宽窄判断。 |
| `layouts/` | 紧凑、中等、宽屏的页面结构与工作台容器分屏判断。 |
| `client-icons/`、`vendor/` | 客户端原始图标和有许可证记录的第三方浏览器资源。 |

## 静态地址契约

`assets.json` 将现有公开地址映射到磁盘文件，例如 `/app.js` 对应 `shell/app.js`。后端仅发布 `bridge/api/assets.py` 固定白名单允许的入口，以及限定路径的字体与 PDF.js CMap 资源；清单本身不会扩大公开范围。CSS 清单可以是有序数组，后端按原顺序在内存中组合并缓存；每个公开 URL 仍只需一次请求。搬移源文件时更新清单；HTML 中的公开地址、查询版本和脚本顺序保持稳定。

桌面网关通过本地文件引用 `shared/i18n.js` 和 `features/accounts/`；这些路径还需要出现在根 `package.json` 的打包清单中。图片地址相对于页面，CSS 地址相对于公开样式 URL；不要根据脚本所在目录改写浏览器地址。

从仓库根目录运行 `npm run test:desktop`。该命令递归发现 `tests/` 和 `mobile/tests/` 下的 `*.test.js`、`*.test.cjs`、`*.test.mjs`，无需手动添加测试清单。资源映射与桌面本地引用由 `tests/frontend-assets.test.cjs` 验证；相关功能测试按实际磁盘路径读取源码。

`tests/browser/*.browser.js` 是依赖真实页面和隔离网关夹具的浏览器检查，不属于 Node 测试；需要通过对应浏览器驱动单独执行，不能将仅加载脚本视作检查通过。

## 启动错误归属

`shared/bootstrap.js` 只把可归属到同源必需启动脚本的运行时错误或 Promise 拒绝作为即时失败。Safari 等浏览器中没有来源的全局错误，以及扩展、注入脚本或按需组件的错误，不直接取消启动；必需资源的有限重试、启动超时和配对写入不重放规则不变。无法归属的错误仅在最终失败详情中保留错误类型与事件类型，不展示异常正文、完整 URL 或堆栈。真实脚本错误仍中止后续依赖执行，不能用此规则掩盖页面自身的失败。

首次资源加载保留内容版本 URL 与预加载缓存。只有 script/link 明确触发加载失败，后续尝试才在同源、相同路径及版本上添加每页独立的 `bridge_retry` 缓存标识，避免三次插入都复用同一个失败响应；总尝试次数仍最多三次。挂起或仅超时的脚本不重复插入，JavaScript 语法／运行时错误不进入资源重试；不使用 fetch 后 eval、内联脚本或替代域名绕过 CSP。

## Web 复制

会话列表右键/长按、Codex 与 Claude/DSH 聊天标题共用 `features/chat/session-menu.js`；`shell/app.js` 绑定身份、能力与现有操作接口。菜单按可用宽度采用就地弹窗或底部面板。改名与归档持有打开菜单时的会话身份及认证代际；复制沿用共享剪贴板，归档使用单次操作 ID。项目、分区通过会话的 `organization` 接口读取原生选项与当前归属，移动前验证原归属与执行目录，移动后回读确认；列表按 Codex 保存的项目归属分组，归属迁移期间沿用旧项目记录并统一项目 ID，其余会话按主机汇入“未归类”。工作目录仅作详情展示。WSL 操作连接原有运行时。分支从当前已读取历史中最近一个可分支消息发起，沿用原生确认流程和工作目录。

`features/chat/message-actions.js` 定义共享 `BridgeClipboard.copy(getText, button)`：已就绪的文本同步调用 Clipboard API；异步完整正文优先在点击内创建带 Promise 的 `ClipboardItem`，保留 WebKit 的用户手势。局域网 HTTP 或现代 API 拒绝时尝试临时只读文本框的兼容复制，并恢复原焦点和选择；弹窗内复制使用同一顶层 dialog。全部失败才展示可重试、可选中文本的兜底窗口，不读取系统剪贴板、不修改草稿。原生 App 继续由 `mobile/shared/web/mobile-clipboard.js` 接管，保留可信点击与桥接令牌检查。

`getText` 可返回字符串或 Promise。普通消息、已读取源码应直接返回字符串；截断正文保留分页、版本与会话校验，不能把预览当成完整内容复制。Node 回归见 `tests/web-clipboard.test.cjs`；真实复制验收须用浏览器点击与系统粘贴，不能把假 Clipboard 对象的调用当作写入成功。

## 工具执行截图

Codex 的 MCP、动态工具及保存历史中的工具输出，共用 `bridge/features/sessions/tool_images.py` 提取图片。支持 MCP 图片块、Codex 的 `input_image` / `inputImage` 内容项、内联图片 data URL，以及 computer use 返回的嵌套 JSON 文本。“显示设置 → 对话内容 → 工具截图”默认开启，偏好保存在当前浏览器或手机 WebView。关闭时不创建工具图片预览，也不请求图片字节；开启后截图显示在折叠的工具文字详情下方，点击使用现有图片放大查看器。切换立即生效并保留工具详情展开状态；关闭“执行过程”时仍隐藏整条工具记录。用户上传的图片不受此开关影响。

上述已识别的图片在时间线、会话状态（含轮询与 SSE）、子代理日志和侧边聊天中以图片标识替换内联数据，Base64 留在网关内的会话索引；未识别的工具输出结构仍按普通文本展示，本模块不承诺清除任意字段里的编码内容。子代理与侧边聊天暂不提供截图预览。图片字节通过原有、需登录且限定聊天的 `desktop-images` 接口按需读取；不访问工具指定的外部 URL 或本地路径。仅接受文件头与 MIME 匹配的 PNG/JPEG/GIF/WebP，单张最多 8 MiB，每次工具结果最多 20 张、合计 16 MiB，并限制嵌套解析深度和工作量；超出深度、节点数或 JSON 解析预算而未扫描的内容，每次工具结果合计保留至多 16384 个 JSON 字符，其余替换为 `{"type":"omitted","reason":"toolOutputBudget"}` 占位（列表剩余元素合并为一个占位），原始会话记录不变。对应检查为 `tests/test_tool_images.py`、`tests/test_uploads_activity.py` 和实际浏览器运行的 `tests/browser/tool-images.browser.js`。

工具图片预览按当前会话视图建立索引，同一会话版本内逐张加载图片不再重复规范化完整历史。工具输出按内容摘要复用解析结果，普通文字更新不重复解码旧截图。网关进程共用有容量限制的 LRU 缓存：解析结果按保守载荷估算最多 64 MiB，已验证图片及其 Base64 来源最多 40 MiB，两类缓存各最多 256 项；超过容量按需重算。缓存不授予会话访问权，预览仍核对当前会话中的图片及原始数据；缓存未命中时在会话锁外解码，返回前重新核验图片归属，并合并并发请求的解码工作。

## 文件阅读模块

- `source-view.js` 用本地 Prism 词元生成只读源码节点，行号独立，复制取原文；软换行与原始行宽可切换。超过 65,536 字符或 4,000 行不高亮，超过 10,000 行省略行号，仍遵守 512 KiB 文本预览上限。见[源码约定](../.agents/notes/implemented/feature/2026-10-13-source-code-view.md)。
- `shared/code-syntax.js` 和共享词元样式用于聊天代码块与文件预览；Prism 通过 `syntax` 组按需加载。代码块左侧显示明确标注的语言，未知类型为纯文本，复制仍取原文。见[共享高亮约定](../.agents/notes/implemented/feature/2026-10-30-shared-code-block-syntax.md)。
- `pdf-preview.js` 使用随网关分发的 PDF.js legacy 模块、worker、CMap 和字体。沿用鉴权下载与 Range，并加 `preview=1`：服务端逐次检查实际大小不超过 `min(20 MiB, clickDownloadMiB)`，不能与显式下载或目录归档混用。可见页按需绘制，预算内保留至多两张邻近完成画布；缩放保留旧画布，单个离屏绘制完成后替换，文本层独立更新。已显示画布最多约 12 MP，替换缓冲另限约 4 MP；这是单个阅读器的画布预算，不是所有标签的总内存预算。
- `html-preview.js` 只把文本交给已鉴权的 `/html-preview-shell`。父页核验消息来源与壳实例；壳的 CSP 与 iframe sandbox 隔离内联脚本，不授同源、网络、表单、弹窗或导航权限。Android/iOS 原生壳仅放行预期的预览子页面，拒绝其余高权限操作。关闭或切换到源码时销毁 iframe，标签返回重新握手。见[PDF/HTML 约定](../.agents/notes/implemented/feature/2026-10-13-pdf-html-preview-and-source-mode.md)。
- `shared/visualize.js` 在 Codex 聊天正文识别完整的 visualize 引用，并按可见区域加载已鉴权的文件。独立 `/visualize-shell` 提供主题、基础控件、图表 CDN、`widgetState`、`setWidgetState` 和 `sendFollowUpMessage`；后者经聊天外层按钮放入输入框。全屏沿用原 iframe，已保存状态按网关、聊天、消息、文件版本隔离。文件读取覆盖本机与 SSH，中继沿用设备作用域。协议、边界和验证见 [visualize 约定](../.agents/notes/implemented/feature/2026-11-13-inline-visualize.md)。
- `workbench.js` 持有目录返回、阅读位置和全屏状态。聊天左上角返回直接进入聊天列表，保留文件标签、目录与阅读位置。文件面板的上一级按钮负责目录导航；手机系统返回先退出预览全屏、关闭浮层，再处理当前文件目录，文件子目录先回父级，根目录后回聊天。全屏沿用同一预览实例，不重新创建 PDF 或 HTML。聊天文件链接的“定位并打开所在文件夹”会在文件列表中定位文件或文件夹；离屏目标约 300 毫秒滚到中央，已可见时保持原位，并高亮约 3 秒；减少动态效果时立即定位并保持高亮 3 秒。

Claude 账号面板的定时刷新只读已保存额度；只有显式 `details(refresh=True)` 查询。成功快照的日期时间与面板刷新时间分开，失败不能覆盖原成功值。见[手动额度约定](../.agents/notes/implemented/bug-fix/2026-10-12-keychain-access-and-manual-quota.md)。

<a id="mermaid-preview"></a>

## Mermaid 图表阅读

- `shared/markdown.js` 仅把已闭合的 `mermaid` 围栏交给 `mermaid-cards.js`；未完成的流式内容和普通代码块仍按源码显示。Codex 时间线、Claude/DSH 聊天和工作台 `.md` 文件预览共用该入口。图表为只读，源码复制沿用原始内容读取入口。
- `mermaid-cards.js` 管理图表/源码选择、并发渲染合并及有界内存缓存。键包含引擎版本、主题与源码，作用域由时间线、客户端会话或工作台管理；重置、切换身份或销毁时由调用者清理。每个作用域最多 24 项、data URL 字符数合计不超过 `6 * 1024 * 1024`；这不是跨设备或跨页面重启的持久缓存。卡片源码超过 30,000 字符时保留源码入口。
- `mermaid-renderer.js` 按需创建 `/mermaid-render-shell` 隔离子页面，通过核验来源、实例和请求编号的消息传递交给本地 Mermaid 10.9.8 渲染；返回 SVG 经白名单过滤后以图片显示，不向主页面插入可执行图表内容。自定义初始化配置、外部资源、交互链接及不安全输出不参与预览，失败保留源码。
- `mermaid-viewer.js` 提供全屏、双指/滚轮缩放、放大后拖动、双击和适应屏幕，缩放范围为适应尺寸的 1–8 倍；源码面板独立滚动。无关流式更新不会关闭已打开的全屏；源码与图表切换保留缩放。作用域被清理时关闭对应全屏。
- `bridge/api/mermaid_render.py` 固定渲染壳与 CSP；渲染壳仍要求登录，主页面 CSP 不放宽。opaque-origin 子页面仅允许加载两个固定脚本资源，禁止网络请求、表单和嵌套页面。网关与中继均按内容版本提供引擎资源；图表源码不写入资源 URL，不依赖外部 CDN。
- iOS 原生导航仅放行当前网关下精确的 HTML/Mermaid 预览子页面 URL，两者作为主页面导航仍拒绝。iOS 验收必须包含完整 App 的导航代理，不能只用不带代理的 WKWebView 替代。

用户操作见[图表阅读说明](../README.md#mermaid-preview)，定向检查见[浏览器检查](../tests/browser/README.md#mermaid-checks)，本轮跨平台结果见[验收记录](../VERIFICATION.md#mermaid-preview-acceptance)。

## CLI 能力展示

列表与会话快照的 `access` 字段控制 CLI 历史阅读、显式连接和允许的操作。界面不自动接入 CLI；服务端独立校验相同能力，隐藏按钮不能替代权限检查。连接失败保留原因，连接成功后清除；诊断下载使用既有登录会话。无 `access` 的旧网关维持原桌面行为。

## 回复交互

审查卡片、后续操作、审查链接和选中片段引用见 [使用说明](../docs/output-interactions.md)。`shared/output-directives.js` 仅解析完整标记，文件入口仍依赖服务器授权的 artifact；`features/chat/composer-quotes.js` 观察原生选区并复用 `ComposerDrafts`，引用和操作按钮不自动发送。
