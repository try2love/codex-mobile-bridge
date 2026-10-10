# 手机宿主与宽窄布局分层验证

日期：2026-10-10。基线为本地 `b70ea57d17e2a714fdf38e0d9cc35d80e776fcf5`，分支为 `refactor/platform-feature-layout`。本轮仅整理源码，不改变版本、不推送、不构建或安装应用。

## 修改范围

- `mobile/ios/`、`mobile/android/` 各自维护原生导航、通知、下载和安全回调；新增各平台开发约束及脚本资源加载入口。
- `mobile/shared/web/` 保存两端共用的 UI、剪贴板注入脚本。与原 Android 文件及原 Swift 内嵌脚本内容保持一致；Android 专用会话脚本仍留在 Android。普通 Android、FCM 和 Xcode 的资源清单已同步。
- `web/hosts/` 集中浏览器/iOS/Android 识别及原生布局适配；这些提示不授予原生权限。保留 token、来源、主框架与真实点击校验。
- `web/layouts/` 提供窄屏、中等宽度、宽屏的页面级样式入口和尺寸查询。分屏继续使用聊天容器实际宽度，共享业务、草稿及标签状态仍由原功能模块管理。
- 原有 46 个静态 URL 和 MIME 保持一致，明确新增 `/host.js`、`/layout.js`。CSS 由固定白名单入口按有序清单组合，按源文件修改时间和大小失效缓存。网关与中继使用同一读取逻辑。

## 源码验证

| 检查 | 结果 |
| --- | --- |
| Node 前端与移动资源回归 | 372 项：371 通过，1 个 Windows 检查跳过 |
| Python 资源、HTTP、relay 回归 | 107 项：105 通过，2 项跳过，无失败/错误 |
| Xcode 项目清单 | `plutil -lint` 通过；没有调用 Xcode 构建或 Swift 编译/解释 |

Python 范围为 `test_resource_layout`、`test_web_assets`、`test_bridge`、`test_shared_relay`。新增检查验证组合字节与顺序、重复访问复用内容、修改时间/大小变化后重读，以及清单额外条目不能开放任意文件。HTTP、中继使用合成数据和本机隔离监听，不连接真实客户端。

Python 跳过的 2 项都是 Windows 路径语法检查；Node 跳过的 1 项是 Windows 文件占用检查。

宿主测试覆盖 UA 语法、晚到的原生注入、尺寸独立性；布局测试覆盖 720px 边界、容器与窗口尺寸差异以及 CSS 优先级位置。移动资源检查覆盖两端唯一源码和资源引用。此前整仓 Python 回归的数量见上一轮记录，未算作本轮重跑。

本机测试日志位于忽略的 `.tmp/architecture-refactor/surface-node-final.log`、`surface-backend-final.log`、`surface-backend-result.json`。

## 浏览器对照

使用已有浏览器与隔离合成会话，覆盖普通浏览器、iOS UA + 原生布局标记、Android UA + 原生布局标记，以及 390×844、844×390、1440×1000 三种视口。改前和改后各 9 组、90 个断言通过；15 类元素的计算样式与几何位置对照没有差异，截图已检查。

另执行现有工作台分屏夹具，14 项通过：拖拽至右屏、分隔条键盘操作和最小宽度、工具切换只替换右侧标签、两侧草稿保留、原生布局标记变化后分屏选择保留。本轮改后共 104 个浏览器断言通过，不包含改前对照断言。

5 套样式表共 944 条解析后的规则、媒体条件与顺序相同。其中 4 套组合字节与基线完全一致；客户端导航样式只把一个混合宿主/尺寸的媒体块拆成相邻、条件相同的块，规则顺序不变。5 个原有 CSS URL 对应 27 个有序源码片段，不增加样式请求。

对照数据与截图保存在 `.tmp/surface-layout/`，包括 `before-matrix.json`、`after-matrix.json`、`layout-comparison.json`、`css-equivalence.json`、`split-interactions.json`。图片预览独立烟测的样式读取也同步到有序资源清单，避免只加载拆分后的首片；本轮未重跑完整图片交互烟测。隔离服务与浏览器测试空间已关闭。

## 验证边界

浏览器里的宿主模拟只能检查页面分支与排布，不能代替 Android WebView 或 iOS WKWebView。原生控制器仍保留既有职责，本次并未将所有原生功能继续拆成独立类。

没有运行 Android/iOS 模拟器或真机，没有执行原生编译、打包、签名、安装或更新。没有执行 Windows/Linux 实机回归、真实客户端启停、账号切换或模型发送。资源清单和源码检查通过，不等于打包产物已验证。
