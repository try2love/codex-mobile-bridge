# IPv6 源码网关监听

源码入口可用 `--listen-ipv6 ADDRESS` 增加 IPv6 监听。可重复指定，地址规范化后去重；原 IPv4 回环监听继续保留，IPv6 socket 只处理 IPv6。桌面配置页目前没有 IPv6 开关，地址不会自动加入手机信道发现。

本机回环示例：

```sh
python3 -B run.py --listen-ipv6 ::1
```

网关会允许 `http://[::1]:8787` 的精确 Host/Origin；替换端口时使用 `--port`。访问控制中的“允许本机访问”关闭后，IPv6 回环也被拒绝。

需要 IPv6 上游的自管 HTTPS 反代可使用：

```sh
python3 -B run.py --listen-ipv6 :: --origin https://bridge.example.com
```

必须保持网关密码验证，并将示例域名替换为自己的 HTTPS 入口。该参数只创建 HTTP 监听，不申请证书、不配置防火墙或反代。反代需保留入口 Host 与 Origin；不要把 `http://[::]` 作为手机地址。仅本机反代可优先绑定 `::1`，需要指定接口时使用其明确 IPv6 地址。

不接受带 `%en0` 等区域标识的链路本地地址。非回环 IPv6 配合 `--no-auth` 会在启动前拒绝。默认不自动添加公网 IPv6 信任地址，也不放宽 TLS、Cookie、CSRF 或既有健康检查规则。

本地测试覆盖真实 `::1` HTTP 请求、地址族、Host/Origin、关闭本机访问，以及双栈启动参数；公网 IPv6、目标系统防火墙和实际反代链路仍需在部署环境验收。
