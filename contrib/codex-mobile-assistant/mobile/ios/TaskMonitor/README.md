# 后台任务提醒（实验）

IPA 内包含独立 TaskMonitor 可执行文件。主 App 登录并取得通知权限后，通过 TrollStore 的 persona 能力启动辅助进程；它每 5 秒读取助手自身的 `/api/task-status?switcher=true`，网络失败后间隔 15 秒重试，并以固定的 `io.github.abyxs.codexmobile` 身份提交本地系统通知。前后台共用去重记录，首次快照不补发历史完成通知，待处理的批准/提问仍会提醒。

这不是 APNs 推送，也不使用 Bark、后台音频、定位或 launchd 服务。系统是否允许进程持续运行和通知提交，仍需在对应 iOS/TrollStore 版本实测。设置页会显示最近一次成功检查时间；“正在监听”仅表示接口读取成功，不证明系统一定显示了横幅。

## 凭据与停止

仅复制当前 HTTPS 入口的 `codex_mobile_session`，存放在 App 自身容器的 0700 目录、0600 文件中，使用首次解锁后可访问的文件保护；不记录密码和其他 Cookie。拒绝 HTTP 重定向。登录到期、退出登录、关闭开关、切换入口都会停止旧配置；没有过期时间的 Cookie 最多使用 24 小时，打开 App 可续期。

前后台以文件锁和有界事件记录去重。重启手机后需要打开一次助手；进程被系统回收后，需要再次打开助手恢复。系统设置中关闭通知后不会绕过系统限制。轮询会增加耗电，主 App 未运行时不保证重启或恢复。

## 构建

使用原有 Xcode scheme 构建，随后运行 `bash package-trollstore.sh`。脚本先为辅助进程和 App 注入各自 entitlement 并进行 ad-hoc 签名，再校验并生成 IPA，不需要开发者证书。历史文件名 `CodexMobile-unsigned.ipa` 保留，但其中包含 ad-hoc 签名。普通 Apple 签名安装不支持这些私有权限。

主 App 恢复 1.0.25 中可以启动的 platform-application、no-sandbox、persona 和容器访问组合，并增加仅涉及 IOSurface/AGX/IOGPU 的 IOKit 客户端白名单。分别声明 com.apple.security.iokit-user-client-class 和 com.apple.security.exception.iokit-user-client-class，以覆盖公开实现使用的两类图形访问检查。打包脚本校验这些数组和其他预期权限均写入实际签名。独立辅助进程的权限保持原配置，通知身份固定为本 App。

此次调整针对 1.0.25 的“原生连接页正常、网页可点击但没有画面”；不再沿用 1.0.26 移除平台标记的方案。现有两份 1.0.25 日志均为 LIBXPC / EXC_GUARD 故障记录，没有标明被拒绝的图形服务，不能据此认定白屏根因。本改动仍需真机验证。

## 真机验收待做

安装后允许通知、登录并确认设置页最近检查时间更新；分别锁屏 1、5、15、30 分钟触发任务完成和待批准状态，确认通知中心、声音、点击跳转和前后台去重。再检查退出登录、切换入口、关闭提醒、网络中断恢复和手机重启。未通过这些检查前，不宣称后台通知已可靠实现。

## 方案参考

- [TrollStore root helpers](https://github.com/opa334/TrollStore#root-helpers)
- [Dopamine 图形访问声明](https://github.com/opa334/Dopamine/blob/1a54e76d515ff5916b64e44d6afbb57d2bc89ee9/Application/Dopamine/Dopamine.entitlements)：使用 exception.iokit-user-client-class 声明 IOSurface/AGX 客户端。
- [Zebra 图形访问声明](https://github.com/zbrateam/Zebra/blob/62bc2b8ba33cd6045d3a007163bfdda63b201913/Zebra/Supporting%20Files/iOS.entitlements)：使用 iokit-user-client-class 声明 IOSurface/AGX 客户端。
- [TrollSpeed](https://github.com/Lessica/TrollSpeed)：独立 persona 进程。
- [TrollVNC BulletinManager](https://github.com/owngoal-dev/TrollVNC/blob/HEAD/src/BulletinManager.mm)：进程内指定 App 标识的通知中心。

上述机制参考公开实现，本目录代码独立实现；这些机制的组合仍属实验方案。
