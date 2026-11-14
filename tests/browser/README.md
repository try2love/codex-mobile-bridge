# 浏览器检查

本目录中的 `*.browser.js` 依赖真实 DOM、页面模块及隔离的网关夹具。它们由浏览器驱动加载并调用文件中声明的检查函数；Markdown 与公式脚本在加载后直接运行。各文件开头说明所需页面与前置状态。

仅使用合成会话和测试工作区。不要在真实用户会话中运行会发送消息、修改文件或操作 Git 的夹具。

会话菜单检查：在只加载生产 `session-menu.js`、`session-menu.css` 和基础样式的隔离页面加载 `session-menu.browser.js`，调用并等待 `runSessionMenuBrowserChecks()`。它以合成回调验证置顶、名称转义、详情复制、原生项目与分区选择、当前归属标记、读取错误与迟到响应隔离、归档确认和取消、恢复、认证变化阻断、长按及滚动取消。分别在宽屏、390 px 与 320 px 检查边界和底部面板；合成检查与真实 Android WebView 验收分别记录。

这些脚本不会由 `npm run test:desktop` 执行。该命令只发现 `tests/` 和 `mobile/tests/` 下的 `*.test.js`、`*.test.cjs`、`*.test.mjs`；浏览器检查应单独记录实际执行与未执行情况。

`npm run test:browser` 自动启动隔离服务，在 Chromium/WebKit 的 1280 px 与 390 px 视口执行 `critical.spec.cjs` 指定的检查。它调用并等待实际检查函数，不把页面加载计作通过。准备方式及失败证据见 [验证入口](../README.md#node-与浏览器)。其他专项夹具仍按下面各节单独执行；这套通过不代表所有浏览器功能均已覆盖。

Markdown 与公式检查也由此入口执行：先加载生产聊天与公式资源，再运行 `markdown.browser.js` 的 15 项检查和 `math.browser.js` 的 13 项检查，覆盖无空行块公式、TeX 原文、代码、链接安全、流式片段及窄屏滚动。可用 `npm run test:browser -- --grep 'Markdown, literal code and math rendering'` 单独运行这四组浏览器与视口组合；不代表手机 WebView 真机验收。

## 启动恢复检查

运行 `python3 -B tests/browser/bootstrap-fixture.py`，在一个测试标签页访问它打印的 loopback 地址。用 `?case=success` 等参数选择场景，完整清单见夹具文件头。夹具提供生产 Web 资源与合成认证，不连接真实网关；每次导航重置故障计数，不能多页同时操作。

等待当前页面的 `window.__startupTest.scenario` 与所选场景一致，并等待启动区域隐藏或进入 `data-state="failed"`。把 `/__checks.js` 作为外部脚本加载后，调用并等待 `runBootstrapBrowserChecks()`；仅加载该脚本不会执行断言。`bootstrap-missing` 场景需等待兜底提示可见；`pair-unknown` 使用 `#pair=synthetic-test-token`，约 30 秒后检查只提交了一次合成配对。正常场景还检查执行顺序、脚本不重复执行和原生 load 时刻的剪贴板定义，`fast` 与 `slow` 分别检查遮罩不闪现及延迟显示。

基础样式回归使用 `shell-once`、`shell-twice` 与 `shell-fatal`：分别让 `/style.css` 首次、前两次、始终返回 503，核验自动恢复与包含初始 link 在内最多三次加载的限制。失败样式可能没有 `sheet`，也可能保留一个读取 `cssRules` 时抛出 `SecurityError` 的对象；Node 用例覆盖后者，浏览器用例验证实际请求链。Chromium 可能对初始 link 发起额外 HTTP 请求，次数上限按实际插入的 link 计数，同时保留服务端请求数；缺少基础 CSS 时的无样式 HTML 兜底不计作样式遮罩闪现。

在浏览器中模拟 `prefers-reduced-motion: reduce`，分别打开 `?case=fast&motion=reduce`、`?case=slow&motion=reduce` 和 `?case=bootstrap-missing&motion=reduce`，运行同一检查函数；查询参数只验证浏览器设置已生效，不代替模拟设置。慢启动与脚本缺失时等待至少 650 ms 再检查，避免只因提示尚未到显示时间而失败。宽屏与 390 px 窄屏均应检查。启动提示只延迟改变可见性，不产生位移、缩放或渐变；减少动态效果不应使提示永久隐藏。

`external-script` 场景模拟 Cloudflare 注入的外部统计脚本，断言 CSP 确实阻止它且工作台仍成功加载。资源下载与执行分开后仍须验证脚本顺序、运行时错误和依赖失败屏障。

`opaque-error` 在 DOMContentLoaded 前注入没有来源的全局错误，覆盖 Safari 截图中 `loading / 0s` 的误中止路径；`external-runtime` 与 `external-rejection` 分别覆盖非启动依赖脚本的异常和未处理 Promise 拒绝。三者都应继续显示登录，不提前进入失败页。它们是合成故障，不证明真实手机上的错误由扩展引起；`runtime` 与 `js-fatal` 仍须中止依赖链，Node 用例还验证来源不明的错误不会延长启动超时，也不会在诊断中暴露异常正文或 URL 参数。

`app-cached-failure` 为原始 `/app.js` 版本 URL 返回可缓存、被 nosniff 拒绝的非脚本响应，模拟失败预加载被后续 script 复用。恢复必须产生带新缓存标识的同路径请求，且真正的 `/app.js` 只能执行一次；仅重复插入相同 URL 时仍失败。此场景验证缓存恢复机制，不代表已经定位或验证真实 Safari 的内容拦截、隐私设置或网络请求。

除 `app-cached-failure` 的故障响应外，夹具资源响应使用 `no-store`；这些故障测试不证明生产缓存命中或真实 BFCache 冻结恢复。需要分别记录浏览器模拟弱网、手机 WebView 实机和生产缓存的验证范围。

[PR #88](https://github.com/try2love/codex-mobile-bridge/pull/88) 作者记录的 2026-10-27 验证：1213 项 Node 测试、8 项 Web 资源测试及 17 个 Chromium 启动场景通过；修复同步至正在运行的 Windows 网关后，用户确认 iPhone Safari 已恢复。此实机反馈确认访问恢复，不据此断言最初故障来自某个扩展或缓存来源。本地集成时的独立复验见[决策记录](../../.agents/notes/implemented/bug-fix/2026-10-27-startup-error-attribution-and-cache-retry.md)，不把作者的实机反馈当作本轮重复完成的验收。

<a id="mermaid-checks"></a>

## Mermaid 定向检查

- `mermaid.browser.js` 在加载 Markdown 与图表卡片模块的页面中直接运行，使用替代渲染器核验围栏识别、原文复制、流式更新、源码选择、缓存复用与清理；通过这些断言不等于真实 Mermaid 图像已生成。
- `mermaid-viewer.browser.js` 需调用并等待 `runMermaidViewerTests()`，以合成 SVG 检查全屏、缩放、拖动、源码切换与关闭。它不能替代真实渲染器或手机输入事件验收。
- 打包验收应从实际安装的网关加载生产资源，确认中文 SVG 的 `naturalWidth > 0`，再检查源码、全屏、流式及缓存。保留完整页面 DOM，避免破坏 App 异步初始化；CDP 驱动需等待主框架导航完成并选用其默认执行上下文。iOS 必须使用带生产导航策略的完整 App，并验证失败基线与修复后的实际图像。
- `tests/test_mermaid_renderer_security.py` 覆盖固定资源、鉴权、CSP 与中继路径；`python3 scripts/test-ios-mermaid-navigation.py` 在有 Xcode/Swift 的 macOS 上编译真实 URL 判断，验证直连及中继精确路径与导航限制。后者只验证导航规则，不代替完整 App 的显示检查。

已完成的本轮检查见 [Mermaid 跨平台验收](../../VERIFICATION.md#mermaid-preview-acceptance)。

## 历史分页

在 `bootstrap-fixture.py` 的 `?case=success` 页面启动完成后，加载
`desktop-history.browser.js` 并调用、等待 `runDesktopHistoryBrowserChecks()`。
它创建独立界面，所有历史与 workspace 请求均为合成只读结果；不访问真实会话。
分别在宽屏与 390 px 下验证首屏、旧页追加、滚动位置、文件引用、当前状态及草稿保留。
断言使用当前消息节点，因为文件引用更新可能合法地替换旧 DOM 节点。


## 新建聊天与项目目录

`project-creation.browser.js` 在已加载生产 Web 脚本的隔离 loopback 页面中运行。
通过浏览器驱动读取并注入脚本后，调用并等待
`runProjectCreationBrowserChecks({language:'zh',provider:'claude',keepOpen:true})`。
`language` 支持 `zh` / `en`，`provider` 支持 `claude` / `deepseek`；
`folder:true` 会在检查结束时停留于共享目录弹窗，便于视觉验收。
`keepOpen:true` 保留预览，结束时调用 `window.__projectCreationPreview.cleanup()`；默认自动清理。
分别在 320 px、390 px 和宽屏下运行中英检查，核验短路径、环境身份、首条消息要求、折叠新建文件夹和横向溢出。
该脚本创建独立 `DesktopSessionsView`，所有请求由闭包合成；新建文件夹仅改变内存中的路径，拒绝项目登记和聊天创建，不访问实际客户端。

Codex 的环境路由、目录登记和创建回执由 `codex-entries.browser.js` 检查，配套服务为
`scripts/smoke-wsl-app-ui.cjs`。运行 `CMB_FIXTURE_ONLY=1 node scripts/smoke-wsl-app-ui.cjs`
只启动合成 API 服务，随后由现有浏览器打开打印的 loopback 地址；不要对真实网关调用这些检查。
页面启动后注入 `codex-entries.browser.js`，调用并等待 `checkCodexEntries()`，使用中文界面和全新的夹具状态。
它覆盖单一 Codex 入口、WSL 扫描启停、列表过滤分页、WSL 新目录、本机及 SSH CLI 无既有项目时的新目录流程、模型标签和草稿隔离。
`checkCodexProjectCreation()` 可在原生 Codex 接入已启用时单独检查本机与 SSH CLI 项目流程。
合成服务核验 `environments`、目录 `host`、WSL `entry`、`kind:'cli'`、目录身份和创建请求；
这些聊天创建只返回内存中的合成会话，不发送消息、不调用模型。修改服务端夹具后应重启服务，重新加载页面不能更新旧进程的路由。


## 图片添加到当前对话

`image-composer.browser.js` 配合 `scripts/smoke-image-composer.cjs`，使用已安装的 Electron 执行隔离检查。
它加载生产 Workbench、ComposerQuotes、ChatAttachments、图片预览器、样式和 `app.js` 中的实际回调，
以合成图片验证 Codex、Claude、DSH 在 390 px 和宽屏下的文件标签、图片预览、“添加到对话”、返回聊天、可见附件和草稿保留。
同一个文件标签在账号身份改变后仍可查看原图片，但不能将旧图片加入新的草稿。
附件上传由内存夹具接收，HTTP 服务拒绝所有非 GET 请求，不调用真实网关或模型。
检查结果和截图保存在 `.tmp/v215-chat-feedback/image-workbench-check/`。

## 输入区引用布局

`composer-quotes.browser.js` 在 `bootstrap-fixture.py` 完整启动生产页面后运行。
调用并等待 `runComposerQuoteLayoutChecks()`，分别在实际 390、844、1440 px 视口执行；调整窗口后先等待 `innerWidth` 匹配目标宽度。
它复制已初始化的完整 Codex / Claude 输入区（包括提醒、权限、上下文和收起按钮），不再手工简化工具栏。核验宽屏选区按钮紧邻上下文用量、窄屏独立紧凑行且原工具栏不换行、引用预览单行省略与展开完整文本，以及选区点击和已有草稿保留；不发送消息。布局依据实际输入区宽度，不能用全窗口宽度代替分屏宽度。

## Codex 多标签页发送协调

在隔离 `bootstrap-fixture.py?case=success` 页面加载 `composer-send.browser.js` 后，调用并等待 `runComposerSendCoordinationChecks()`。它使用两个独立同源文档、实际 IndexedDB 事务与专属合成 localStorage 键，验证同 ID 并发登记、拒绝顺序、未知结果保留和全部明确拒绝后允许改为排队；不请求发送 API。顶层标签页的同源共享行为需由浏览器驱动另行核验。

`persistent-drafts.browser.js` 的 `runPersistentDraftTests()` 使用真实提交处理器和合成网络结果，补充账号切换、页面导航及等待回执期间的新草稿保护。两类检查不得在真实会话页面执行。

## 聊天列表优先启动

`python3 -B tests/browser/list-first-fixture.py` 启动隔离 loopback 服务，使用生产页面、版本化资源和合成账号／列表，拒绝聊天写入。
`?case=first` 获取列表后，打开 `?case=cached`：同一身份缓存应先显示，5 秒后刷新为新标题；`?case=identity` 使用另一缓存身份，旧列表不能提前出现。
`?case=blocked` 对公式、终端和文件预览资源返回 503，列表仍应正常显示且不请求这些组件；实际渲染公式后应保留源码和重试按钮，不进入全屏启动错误。
`?case=slow` 为脚本响应额外增加 300 ms；结合浏览器单页网络限速验证冷启动。`window.__listProbe` 记录首个列表／缓存更新时刻和运行时异常。仅在夹具中使用，禁止对真实网关注入测试数据。

## WSL 弹窗宽度

在隔离生产页面夹具中加载 `wsl-dialog-width.browser.js`，调用并等待 `runWslDialogWidthChecks({kind:"accounts"})` 或 `{kind:"new-chat"}`。使用合成长发行版名称、账号名、地址与导入选项；不调用真实账号接口或发送聊天。分别在 320/393/430/1280 宽度及中英文下检查元素边界、`scrollWidth === clientWidth`、横向滚动位置为零，窄屏原生选择框字号至少 16px。完成后调用 `window.__wslLayoutCleanup()`。

选择框内部的长选项在 WebKit 中可能扩大祖先滚动区域，即使元素边界正常；需要在 WebKit 实际执行以上滚动断言，不能只检查 Chromium 或元素宽度。macOS WKWebView 的通过结果不能代替 iPhone 真机输入与选择验收。

## 共享客户端会话控件

在隔离 `bootstrap-fixture.py` 的成功页面中注入 `desktop-parity.browser.js`，调用并等待 `runDesktopParityChecks({language:'zh'})`。检查三客户端底栏同排、原生上下文占比、共享引用入口，以及长文件名图片预览按钮不换行；所有数据和请求均为合成，不发送模型消息。覆盖 320/390 像素中英文、横屏和宽屏。`keepOpen:true` 保留图片预览供截图；会话菜单 ID 复制另由 `session-menu.browser.js` 验证。

## visualize 回归

`npx playwright test -c tests/browser/playwright.config.cjs visualize.spec.cjs` 在 Chromium 与 WebKit 的宽窄视口运行生产 Markdown、预览壳和运行时。覆盖交互、状态恢复、主题、保持脚本内存的全屏、提问草稿、失败重试及隔离边界。合成 API 与外网拦截保证这些用例只操作夹具。
