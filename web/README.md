# Web 与手机共享界面

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

## 工具执行截图

Codex 的 MCP、动态工具及保存历史中的工具输出，共用 `bridge/features/sessions/tool_images.py` 提取图片。支持 MCP 图片块、Codex 的 `input_image` / `inputImage` 内容项、内联图片 data URL，以及 computer use 返回的嵌套 JSON 文本。“显示设置 → 对话内容 → 工具截图”默认开启，偏好保存在当前浏览器或手机 WebView。关闭时不创建工具图片预览，也不请求图片字节；开启后截图显示在折叠的工具文字详情下方，点击使用现有图片放大查看器。切换立即生效并保留工具详情展开状态；关闭“执行过程”时仍隐藏整条工具记录。用户上传的图片不受此开关影响。

时间线、会话状态（含轮询与 SSE）、子代理日志和侧边聊天只传图片标识，Base64 仅保留在网关内的会话索引；子代理与侧边聊天暂不提供截图预览。图片字节通过原有、需登录且限定聊天的 `desktop-images` 接口按需读取；不访问工具指定的外部 URL 或本地路径。仅接受文件头与 MIME 匹配的 PNG/JPEG/GIF/WebP，单张最多 8 MiB，每次工具结果最多 20 张、合计 16 MiB，并限制嵌套解析深度和工作量；超出深度、节点数或 JSON 解析预算而未扫描的内容，每次工具结果合计保留至多 16384 个 JSON 字符，其余替换为 `{"type":"omitted","reason":"toolOutputBudget"}` 占位（列表剩余元素合并为一个占位），原始会话记录不变。对应检查为 `tests/test_tool_images.py`、`tests/test_uploads_activity.py` 和实际浏览器运行的 `tests/browser/tool-images.browser.js`。

工具图片预览按当前会话视图建立索引，同一会话版本内逐张加载图片不再重复规范化完整历史。工具输出按内容摘要复用解析结果，普通文字更新不重复解码旧截图。网关进程共用有容量限制的 LRU 缓存：解析结果按保守载荷估算最多 64 MiB，已验证图片及其 Base64 来源最多 40 MiB，两类缓存各最多 256 项；超过容量按需重算。缓存不授予会话访问权，预览仍核对当前会话中的图片及原始数据；缓存未命中时在会话锁外解码，返回前重新核验图片归属，并合并并发请求的解码工作。

## 文件阅读模块

- `source-view.js` 用本地 Prism 词元生成只读源码节点，行号独立，复制取原文；软换行与原始行宽可切换。超过 65,536 字符或 4,000 行不高亮，超过 10,000 行省略行号，仍遵守 512 KiB 文本预览上限。见[源码约定](../.agents/notes/implemented/feature/2026-10-13-source-code-view.md)。
- `pdf-preview.js` 使用随网关分发的 PDF.js legacy 模块、worker、CMap 和字体。沿用鉴权下载与 Range，并加 `preview=1`：服务端逐次检查实际大小不超过 `min(20 MiB, clickDownloadMiB)`，不能与显式下载或目录归档混用。可见页按需绘制，预算内保留至多两张邻近完成画布；缩放保留旧画布，单个离屏绘制完成后替换，文本层独立更新。已显示画布最多约 12 MP，替换缓冲另限约 4 MP；这是单个阅读器的画布预算，不是所有标签的总内存预算。
- `html-preview.js` 只把文本交给已鉴权的 `/html-preview-shell`。父页核验消息来源与壳实例；壳的 CSP 与 iframe sandbox 隔离内联脚本，不授同源、网络、表单、弹窗或导航权限。Android/iOS 原生壳仅放行预期的预览子页面，拒绝其余高权限操作。关闭或切换到源码时销毁 iframe，标签返回重新握手。见[PDF/HTML 约定](../.agents/notes/implemented/feature/2026-10-13-pdf-html-preview-and-source-mode.md)。
- `workbench.js` 持有目录返回、阅读位置和全屏状态。Back 先退出预览全屏、关闭浮层，再处理当前文件目录；文件子目录先回父级，根目录后回聊天。全屏沿用同一预览实例，不重新创建 PDF 或 HTML。

Claude 账号面板的定时刷新只读已保存额度；只有显式 `details(refresh=True)` 查询。成功快照的日期时间与面板刷新时间分开，失败不能覆盖原成功值。见[手动额度约定](../.agents/notes/implemented/bug-fix/2026-10-12-keychain-access-and-manual-quota.md)。

## CLI 能力展示

列表与会话快照的 `access` 字段控制 CLI 历史阅读、显式连接和允许的操作。界面不自动接入 CLI；服务端独立校验相同能力，隐藏按钮不能替代权限检查。连接失败保留原因，连接成功后清除；诊断下载使用既有登录会话。无 `access` 的旧网关维持原桌面行为。
