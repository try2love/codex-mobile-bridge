# Agent Note: 网关显示可读设备与接入方式

Status: implemented

## 问题

网关直接展示原始UA，手机只有BridgeMobile协议标记，桌面浏览器则含多段兼容标记。用户难以识别设备型号、App版本和真实浏览器。

## 决策

新手机WebView在原BridgeMobile标记后携带机型、系统版本、App构建号；标记中的版本来自安装包元数据。Android使用厂商与MODEL，iOS使用uname的machine型号标识。只包含型号，不采集序列号等唯一硬件标识。

认证完成后的会话保存clientInfo展示数据。旧手机后台仍可能发送原UA，因此同平台后台请求保留已知的详细型号和版本；详情变化及时持久化。旧会话在管理快照中按已有UA整理，不要求重新绑定。旧0.1是协议标记，不能当成安装包版本。

浏览器按Edge、Opera、Firefox、Chrome、Safari顺序识别，避免Edge被Chrome兼容段误判。桌面浏览器只显示可判断的平台，不把冻结的macOS或Windows UA版本当作实际系统版本。UI使用textContent显示型号与版本。

设备重命名仅经原有本机管理入口写入对应登录记录，最多80个可见字符；名称为空恢复IP。标题显示“名称（IP）”，IP变化仍保留该登录名称，同IP其他登录不共用别名。编辑框沿用卡片内输入与保存/取消操作，后台快照刷新不销毁正在输入的名称。名称不改变认证、绑定、有效期或访问规则。

## 备选方案

仅压缩原UA无法补回手机壳未上报的版本和型号。采集硬件唯一ID不是判断型号所必需。依赖第三方UA库增加依赖且仍不能获知被浏览器隐去的真实型号，因此保留小范围规则与原生型号上报。

## 影响

客户端提供的信息用于辨认设备，不是身份或授权证明；认证、令牌、过期及解除绑定机制保持原样。未升级手机不会凭空出现型号和真实App版本，连接新版壳后补齐。

回归覆盖Edge识别、原生版本与旧协议区分、后台旧UA和重启后的信息保留、旧会话兼容及控制字符。平台依据为[Android Build](https://developer.android.com/reference/android/os/Build)、[PackageInfo](https://developer.android.com/reference/android/content/pm/PackageInfo)和[Apple uname](https://developer.apple.com/library/archive/documentation/System/Conceptual/ManPages_iPhoneOS/man3/uname.3.html)。

原生通道识别按版本无关语法兼容新旧UA；此前离线注入脚本遗漏该契约的回归与修正见[Android离线桥](2026-11-23-versioned-android-offline-bridge.md)。
