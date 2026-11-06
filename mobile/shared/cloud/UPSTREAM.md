# 云协议来源

端到端加密、登录、设备证明和多路复用来自 [Bridge Mobile v0.2.0](https://github.com/mc373906408/codex-mobile-bridge-app/tree/e9d29730a67045fb210533184c80a4ee36b4c05f/rust/src)，遵循同目录的 MIT 许可。保留了其控制协议和证书验证，不使用 Flutter、上游聊天 UI 或上游聊天适配器。电脑目录与消息仍由本网关和现有 Web 工作台提供。

## 原型构建

使用项目现有的 Android SDK 或 Xcode，再在构建机准备 Rust 编译目标。App 内使用编译后的库，无需在手机安装 Rust 或 Flutter。云配置按服务器的规范 HTTPS 源地址和服务器认证的账号 ID 登记。设备别名只属于该配置。退出保留安装密钥和别名；删除清除本机的整份登记。

- Android ARM64 用 `scripts/build-mobile-cloud-prototype.py android --ndk <NDK> --verifier-aar <AAR> --kotlin-jar <JAR>`。JNI 使用 Android 系统信任管理器。AAR 来自 [rustls 的固定 Maven 快照](https://github.com/rustls/rustls-platform-verifier/tree/1aa691352a5e0c215210dfc9a3c2f2078639dc91/android-release-support/maven/org/rustls/rustls-platform-verifier/0.2.0)。测试组合使用 NDK r27c 与 Kotlin stdlib 1.9.23。
- 云 Test APK 使用 release Rust 库与 R8；保留 JNI 入口和系统证书验证器，只裁剪未引用的字节码。
- iOS 用同一脚本的 `ios-sim` 或 `ios` 目标构建对应的静态库。在主 App 目标加入 Swift 编译条件 `CMB_CLOUD_PROTOTYPE`、链接 `libcmb_cloud.a`、Security、SystemConfiguration、CoreFoundation、libc++ 和 libresolv。使用独立 Test 身份及自身的钥匙串访问组。模拟器的 application-identifier 通过 Mach-O 的 `__TEXT,__entitlements` 传入；真机使用 Apple 签名与描述文件。
- 与桌面相同的公开 Web 资源清单在编译期组装。已配对且获准、用户选择电脑后才开启私有回环页面。该页面的 API 请求需要当前页面的随机凭据，并继续经过桌面中继的逐次授权检查。退出或离开云工作台关闭本机入口。
- 已验证的设备再次打开会重新建立加密连接并检查授权。云工作台和直连共用账号页面、会话控件及桌面处理函数。每次请求仍须匹配已允许的设备、当前启用的 Agent 和所属配置。账号切换、API 接入、SSH 重连由对应配置原有的入口执行；免密网关原有的写操作限制同样适用。
- 四个平台按各自的原生能力提供接续、发送、停止和审批。云原型尚未验收终端 WebSocket、全部附件类型及推送服务。
