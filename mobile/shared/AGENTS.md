# 两端共用的 WebView 脚本

- `web/mobile-ui.js` 和 `web/mobile-clipboard.js` 是 Android/iOS 的唯一源，按 UI、剪贴板顺序加载。不要在平台代码生成或复制同一份脚本。
- 脚本只补充手机原生壳的页面适配；共享聊天功能与布局仍在根目录 `web/`。
- 保留 token 占位符、剪贴板真实点击/单次消费与主框架保护。原生权限和 origin 校验留在各平台原生桥，不能靠页面自行声明可信。
- 新增或重命名资源必须同时更新 Android 普通构建、FCM Gradle 和 Xcode 主目标资源清单，并运行 `mobile/tests/resources.test.cjs`。不得把 Android 专有 `mobile-session.js` 纳入 iOS。
