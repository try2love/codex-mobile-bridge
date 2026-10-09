# 可选 Android FCM 宿主

- 本目录只包含 Firebase 推送引导和服务；Activity、下载、扫码等公共原生实现继续引用 `../android/src`。
- Gradle 必须同时读取 `../shared/web` 与 `../android/assets`，与普通 APK 资源一致，且资产文件名不能重复。
- 不向普通 APK 引入 Firebase 依赖，不提交项目密钥、google-services 配置或签名文件。
- 其他 Android 安全和验证要求见 `../android/AGENTS.md`。
