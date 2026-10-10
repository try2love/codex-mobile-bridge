# Tailscale 固定 HTTPS 入口

无需自建服务器或邀请码。网关复用电脑上已安装的官方 Tailscale 客户端，为现有工作台提供固定地址。电脑、网关与所用 Agent 客户端仍需保持运行；Tailscale 不会唤醒休眠的电脑。

## 首次配置

1. 安装 [Tailscale 官方客户端](https://tailscale.com/download)，登录自己的账号并开启连接。macOS 首次启动需在系统设置允许网络扩展；Windows/Linux 需要官方服务处于运行状态。需要 1.52 或更高版本。
2. 在网关 **网络访问** 添加 **固定 HTTPS · Tailscale**。选择 **公网访问 · Funnel**，保存配置。
3. 点击 **检测 Tailscale 并读取地址**，再保存自动填入的地址。无需手填域名或密码；Tailscale 登录凭据由官方客户端管理。
4. 启动网关。如果提示授权，点击 **继续 Tailscale 授权**，在 Tailscale 官方页面启用 HTTPS / Funnel。需要相应网络管理权限；没有权限时联系自己的 Tailscale 网络管理员。
5. 点击 **检测固定入口**，再从概览生成手机配对码。普通手机浏览器和手机 App 不必安装 Tailscale；沿用网关原有登录与扫码配对。
6. 手机关闭 Wi-Fi，验证首次打开、聊天读写、文件下载和重新连接。电脑端检测通过不等于蜂窝网络可达。

macOS 建议使用 Tailscale 官网的独立安装包（Standalone）；Funnel 的平台要求见[官方说明](https://tailscale.com/docs/features/tailscale-funnel)。

Funnel 会把入口提供给公网，因此要求启用网关登录验证。不要关闭验证来解决连接问题。

## 私有访问

选择 **私有组网 · Serve** 时，只有已连接同一 Tailscale 私有网络且获访问授权的设备可以进入。浏览器无需插件，但所在手机/电脑必须具备 Tailscale 网络连接。本版没有在手机 App 内嵌 Tailscale。

## 地址和启停

- 地址为 `https://设备名.网络名.ts.net`，HTTPS 证书由 Tailscale 管理。保留设备身份和名称时，正常重启不会重新分配临时地址。
- 删除设备、切换账号、改名后需要停止网关，重新检测并保存。原手机地址可能需要更新。
- 入口随网关启动和停止；只退出控制面板并保留网关时，入口也保留。
- 网关不退出 Tailscale，不执行 `serve reset`、`tailscale down` 或 `logout`。本项目创建的前台会话在连接进程退出时由 Tailscale 清理。
- 默认使用 HTTPS 443。被其他 Tailscale 服务占用时不会覆盖，可以改为 8443 或 10000，再检测保存；这时访问地址包含端口。
- 重启电脑后，Tailscale 需要恢复连接，网关也需要启动。网关的自动启动选项不会替代操作系统或 Tailscale 的登录设置。

## 网络限制与排查

Funnel 使用 Tailscale 公网服务，存在带宽限制，不保证国内蜂窝网络速度。固定域名解决地址变化问题，不能代替线路实测。Funnel 的内建 HTTPS 入口限定为 `.ts.net`，不直接支持自定义域名。

| 提示 | 处理 |
| --- | --- |
| 未找到客户端 | 安装官方 Tailscale，重新检测；macOS 支持应用目录和 Homebrew，Windows 支持标准安装目录，Linux 支持 PATH/标准目录 |
| 等待登录或网络权限 | 打开 Tailscale，完成登录和系统网络授权 |
| 等待 HTTPS/Funnel 授权 | 点击继续授权，完成官方页面操作 |
| 首次授权后公网找不到域名 | 公网 DNS 记录最多可能需要 10 分钟传播；电脑上的 MagicDNS 可先解析到私有地址，仍需用手机蜂窝网络核验公网访问 |
| 端口被其他服务使用 | 选择其他支持的 HTTPS 端口，不需要重置已有 Tailscale 配置 |
| 账号或设备地址变化 | 停止网关，检测当前账号，再保存 |
| 入口启动但检测失败 | 检查 DNS、证书、Funnel 授权和公网线路；不要跳过 TLS 或关闭网关验证 |

[Serve 官方说明](https://tailscale.com/docs/features/tailscale-serve) · [Funnel 官方说明](https://tailscale.com/docs/features/tailscale-funnel)
