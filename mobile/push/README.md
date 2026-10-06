# 原生系统推送：本地开发接入

当前 preview 包没有配置 APNs 或 Firebase 项目，不能用于验证后台任务通知。手机收件箱与本地定时通知测试不等同于系统推送。

本地开发链路为：电脑网关 → APNs / FCM → 手机系统。平台私钥只放在电脑网关的私有数据目录，不能打包进手机 App、源码或公开安装包。公开产品的统一推送中转尚未部署；国内无 Google 服务设备的厂商推送尚未接入。

## 电脑网关

源代码运行环境安装本目录 requirements.txt。桌面打包尚未包含这些可选依赖。

在网关数据目录创建 `mobile-push.json`，权限应仅允许当前用户读取。只添加已配置的平台：

```json
{
  "apns": {
    "teamId": "YOUR_TEAM_ID",
    "keyId": "YOUR_KEY_ID",
    "keyFile": "/private/path/AuthKey.p8",
    "bundleId": "io.github.try2love.codexbridge.preview",
    "sandbox": true
  },
  "fcm": {
    "projectId": "your-firebase-project",
    "credentialsFile": "/private/path/firebase-service-account.json"
  }
}
```

网关必须开启密码保护、手机通知收件箱和相应聊天的提醒。手机经登录会话与 CSRF 校验注册设备；注册从当前事件位置开始，不推送历史。过期/撤销的登录停止推送；错误会重试，失效 Token 会清除。服务商接收请求不等于设备已显示通知。

配置文件中的字段出现仅表示已填写配置，并不证明凭据、依赖或投递成功。默认不连接任何外部推送服务。

## iOS

需要具备 Push Notifications 能力的 Apple 开发者签名。当前个人免费签名预览不能用于该验收。

1. 在 Apple 项目中配置 App ID、Push Notifications 和 APNs Token Key。
2. 为本地推送构建配置 `BridgePreview/Push.entitlements`，重新生成匹配的 provisioning profile。该文件默认使用开发环境；生产签名需要使用匹配的环境。
3. 在该构建的 Info.plist 中增加 Boolean `BridgePushEnabled=true`。默认预览不添加这个开关。
4. 配置电脑端 APNs 参数，在手机设置启用系统后台推送。普通通知注册 Device Token；开启聊天实时活动后单独注册 Activity Token。
5. 只有已开启提醒、且网关正在跟踪的聊天会更新灵动岛：执行状态、等待确认或本轮结束。运行状态采用变化时更新及低频保鲜，App 重启后重新订阅已有活动 Token。平台会控制投递频率，仍需要真机验证，不承诺逐秒实时。

## Android

普通小体积预览保留无 SDK 的构建。`mobile/android-push` 为可选 FCM 构建工程，复用同一套界面与连接代码；没有 Firebase 配置时不启用自动初始化。

1. 为相同 applicationId 创建 Firebase Android App，把平台生成的 `google-services.json` 放入 `mobile/android-push/`。此文件已忽略，不提交。
2. 使用 Gradle 8.9、项目内 JDK17 和 Android SDK35 构建 `assembleDebug`。Firebase 插件和 SDK 会作为项目依赖下载。已使用独立的占位配置完成本地编译验证；该测试包不用于分发或真实推送。实际使用仍需自己的平台配置。
3. 电脑端配置同一项目的服务账号。手机需要可用的 Google Play services 和到 FCM 的网络连接。
4. 手机设置中启用系统后台推送。系统通知权限仍由用户确认。

该工程不代表国内厂商推送已支持。正式分发前需要分别完成 FCM 与目标厂商的真机验收。

## 验收

- 前台、切后台、锁屏、恢复网络分别触发隔离任务事件；检查到达、点击目标、重复投递。
- 验证账号/设备移除、登录撤销后停止通知。
- 清空通知只影响本机当前电脑的收件箱显示；新事件正常出现。
- 灵动岛的完成事件结束活动，过期时保留最后已知状态与时间。

当前已实现网关注册/分发与客户端接入代码；没有平台账号和凭据，尚未完成真实后台推送验收。
