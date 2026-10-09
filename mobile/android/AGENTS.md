# Android 原生壳

- `src/io/github/try2love/codexbridge/MainActivity.java` 保留 Activity 生命周期、WebView 导航和原生提示桥的校验。`MobileWebScripts.java` 只按既有顺序读取 assets，不负责授权。
- 两端共用页面脚本放 `../shared/web/`；`assets/mobile-session.js` 是 Android Cookie 落盘与后台同步通知脚本，不能挪入共用目录并注入 iOS。
- 普通 APK 由根目录 `scripts/build-mobile-android.py` 打包；可选 FCM 使用 `../android-push/build.gradle`。调整共享资源须同步两种输入，不能在两处保留同名 JS；缺资源继续显式失败，不使用空字符串。
- 保留 token、当前 WebView、URL origin、窗口焦点、大小限制以及导航主框架/用户手势检查。共享 JS 不能替代原生端的校验，不能新增不受限的 JavascriptInterface。
- `GatewayURL`、下载、通知和文件选择保留原来的 URL、Cookie、已保存电脑与回调代际边界。普通 APK 不包含 Firebase SDK；推送特有依赖只在 `android-push`。
- Java 包名与清单入口保持一致；新源文件由既有递归源码构建发现。不要因目录整理改 applicationId、版本或签名。
- 源码回归：`node --test mobile/tests/*.test.cjs`。`scripts/test-mobile-*.py` 会运行 JDK/Swift 等工具；构建、安装及真机验证必须符合本次任务范围。
