# 桌面版本发布与更新

## 用户操作

从 v0.2.0-beta.5 起，桌面 App 启动后及每 6 小时检查一次 GitHub Release。
在「应用更新」中检查新版、阅读说明，点击「更新并重启」。Beta 用户可接收后续 Beta、RC 和正式版；正式版只接收正式版。
旧版首次升级需要手动安装支持更新的版本。v2.0.0 提供 Mac Apple Silicon（arm64）、Mac Intel（x64）和 Windows x64 安装包。Windows ARM 暂无发布包。Ubuntu x64/ARM64 提供实验性 `.deb` / AppImage，暂不支持应用内更新；升级前停止网关并退出 App，保留数据目录后手动安装。

下载阶段网关保持在线。签名、哈希、解压路径和包版本检查通过后，App 才退出并停止网关。
应用在原位置替换，原本运行的网关随后恢复；原本停止的网关保持停止。
数据目录独立于应用目录，登录、网络、通知和关注聊天配置保持原样。
使用临时 Cloudflare HTTPS 时，重启后可能需要最新地址；固定入口可避免普通重启导致的地址变化，但仍需网络可达。Tailscale 配置见[说明](tailscale.md)。Android/iOS 更新与签名方式见[手机说明](../mobile/README.md)。

在「消息通知 → 网关启动与入口通知」检查入口通知；新配置默认开启，升级保留已有选择。开启并保存后，每次启动网关都会向已启用的 PushPlus、Bark、ntfy 通道汇总发送访问地址，即使地址与上次相同。覆盖已启用的局域网、NAS / 已有反代固定域名、自有服务器（SSH 转发建立后）和临时 HTTPS（隧道就绪后）；稍晚就绪的入口或地址变化会补发更新。关闭的网卡与回环地址不包含在手机通知中。固定域名仍需用户完成部署，局域网地址需在同一网络访问。可设置网关名称，点击「发送当前入口测试通知」并在手机确认接收后再远程更新。仅配置聊天通知不代表已开启入口通知。

入口通知独立于聊天订阅；各通道分别记录结果并退避重试，同一次网关运行内去重，重启后重新发送。重试前重新检查当前入口和开关，只发送当前入口列表，不使用聊天通知的固定跳转地址，也不包含密码或登录令牌。通知已被服务接受但本地记录写入前异常退出时，可能重复投递；已在途的通知无法撤回。电脑断网、网关启动失败或通知服务不可用时，无法保证送达。临时入口仍依赖 Cloudflare 分配地址，此功能不提供固定域名。

安装目录需要当前用户可写，数据目录不能放在应用安装目录内部。
macOS 更新签名与 Apple Developer ID、公证是不同机制；该功能不会让应用获得 Apple 公证或 Windows 证书签名。

## 发布流程

1. 同步 `package.json` / `package-lock.json`、Android/iOS 版本与递增的构建号，更新 `RELEASE_NOTES.md`。保留已有 Android 签名和移动端应用身份，不能临时生成新密钥替代覆盖升级。
2. 仓库 Actions Secret `UPDATE_SIGNING_KEY` 必须对应 `desktop/features/updates/update-public-key.pem` 中的 Ed25519 公钥。私钥不提交、不放入构建产物；不要重新生成公钥覆盖现有更新身份。
3. 运行源码测试及五个原生 Desktop builds 目标：macOS arm64、macOS x64、Windows x64、Ubuntu x64、Ubuntu ARM64。Mac 网关与 Electron 在对应架构 runner 构建；核验安装、架构、签名、启动、更新交接、失败恢复与配置保留。iOS 可复用构建生成未签名 IPA 与源码 ZIP；Android 用原签名在本地生成 APK。
4. 推送匹配版本的 tag，或对已有版本 tag 手动运行 **Prepare signed release draft**。流程验证版本和签名身份，等待五个桌面目标及 iOS 构建，始终先生成草稿，不自动发布正式版。
5. 收齐 13 个安装/源码包：两种 Mac 的 DMG/ZIP、Windows Setup/ZIP、两种 Ubuntu 的 DEB/AppImage、Android APK、iOS IPA 与源码 ZIP，以及 `bridge-update.json`。运行 `node scripts/release-assets.cjs verify <目录> --write-checksums` 验证并生成 `SHA256SUMS.txt`。校验文件覆盖这 14 个文件；更新签名载荷继续认证 macOS arm64/x64 和 Windows x64 的三个 ZIP。
6. 上传缺失的 Android 包和最终校验文件，再下载草稿全部资产，运行 `node scripts/release-assets.cjs verify <目录>` 重新核验。完整性、签名与所有计划资产通过后才将草稿发布为正式版；失败时保留草稿排查。发布后检查实际下载地址、Latest 与 App 检查更新结果。

`bridge-update.json` 是签名信封：`payload` 为 JSON 原始字节的 Base64，`signature` 为其 Ed25519 签名。载荷包含 schema、版本、说明、平台文件名、大小和 SHA-256。App 内公钥验签后才接受文件信息，下载地址固定到本仓库 Release，拒绝降级与平台不匹配。

## 安装事务与恢复

私有 stdio 的 `update-prepare` 在应用所在目录建立 `.cmb-update-*`，校验并展开包、验证 macOS 签名和内置运行时，然后复制旧运行时作为独立更新进程。手机 HTTP 接口不提供更新操作。

`update-apply` 等原 App 退出后替换目录，等待新版 App 确认加载，并在需要时恢复网关。失败时尝试还原 `previous` 并启动原版本。Windows ZIP 覆盖更新保留已有卸载程序与快捷方式，并更新对应的当前用户卸载版本信息。

更新进程的工作目录必须是安装目录的父目录。Windows 会锁定进程的工作目录，即使更新程序本身已复制到临时目录，也无法移动仍被它用作工作目录的旧安装目录。不要将工作目录设为事务目录，避免重启后的 App 继承它，影响下次更新时的清理。

1.1.0 已修复更新器工作目录。此前发布的 beta.7 和 1.0.0 没有设置这一工作目录。从安装目录启动后，应用内更新可能出现 `WinError 32` 并恢复原版本。遇到这种情况，在托盘选择「停止网关并退出」，然后运行目标版本的 Windows `Setup.exe`，安装到原位置；不需要先卸载或删除网关数据。已安装的旧版本负责启动更新器，因此仅发布新版下载包无法修复旧版本本次更新的启动行为。

更新结果位于数据目录的 `desktop-update-result.json`，更新进程日志为 `desktop-update.log`。成功后保留一份旧应用，在下次更新准备时清理。失败的事务目录保留供恢复；异常断电、磁盘损坏或权限变化仍可能需要手动恢复 `previous`。这些情况下不要删除备份。

自动清理仅处理 `plan.json` 中目标安装路径匹配的事务，且只删除明确成功或已恢复的事务。辅助进程已启动但没有最终结果，或目录中仍有未确认恢复的 `previous` / `failed` 时，阻止后续更新并保留整个事务；超时或进程退出不能证明恢复完成。未知归属及其他安装副本的事务不作修改。
