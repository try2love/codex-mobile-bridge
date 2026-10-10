# 页面宿主适配

此目录处理页面运行在普通浏览器、iOS App 或 Android App 时的差异。窗口尺寸与分屏由 `../layouts/` 管理；同一个宿主可以处于窄屏、横屏或宽屏。

## 页面入口

`environment.js` 通过固定公开入口 `/host.js` 在功能脚本前加载，提供 `BridgeHost`：

| 方法 | 用途 |
| --- | --- |
| `kind(userAgent?)` | 返回 `browser`、`ios` 或 `android`，沿用现有 `BridgeMobile` 标识语法 |
| `isNativeApp(userAgent?)` | 登录界面和附件下载选择原生壳已有行为 |
| `hasNativeLayout()` | 查询原生脚本是否已注入布局；供返回电脑列表、终端输入行为使用 |

身份与原生布局准备状态是两件事。Android/iOS 注入时机不同，`hasNativeLayout()` 每次读取当前标记，不在页面加载时永久缓存。普通 Safari/Chrome 即使在手机上运行，也属于浏览器宿主。

这些信息只用于呈现和行为选择，不能授予权限。任何页面都能伪造 UA/class；剪贴板、下载、导航等操作仍由原生桥核验当前连接、token、主框架和真实用户操作。

## 原生实现与共享脚本

| 修改目标 | 所在位置 |
| --- | --- |
| 两端共同的顶部菜单让位、返回入口和连接名称 | `mobile/shared/web/mobile-ui.js` |
| 两端共同的原生剪贴板脚本 | `mobile/shared/web/mobile-clipboard.js` |
| Android 生命周期通知与会话续期提示桥 | `mobile/android/assets/mobile-session.js` 及 Android 原生实现 |
| Android JavaScript 资源读取 | `mobile/android/.../MobileWebScripts.java` |
| iOS 资源读取、viewport 注入 | `mobile/ios/BridgePreview/WebScripts.swift` |
| 原生导航、token/origin/frame 验证 | 各平台的 `MainActivity.java` / `App.swift` |

宿主 CSS 按原样式表中的优先级位置拆出，由 `web/assets.json` 的有序清单组合；不要通过插入更多覆盖规则来绕开已有布局。共享脚本随原生壳打包，网关不公开提供它们，也不通过网络动态加载原生桥脚本。

## 验证

`tests/host-environment.test.cjs` 检查宿主识别、晚注入和与尺寸独立的行为。`mobile/tests/resources.test.cjs` 检查两端实际资源引用；移动端现有剪贴板、名称、返回与会话测试仍从共同源码运行。

浏览器中设置 UA/class 只模拟页面选择分支，不能代表 Android WebView、iOS WKWebView 或原生系统权限已验证。
