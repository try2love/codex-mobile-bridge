# Agent Note: 源码网关显式 IPv6 监听

Status: implemented

## 问题

Issue #208 的 IPv6 Origin 已可规范化，但 HTTPServer 仍只有 IPv4，无法绑定 ::1 或 ::。

## 决策

GatewayServer 按地址选择 AF_INET6，设置 IPV6_V6ONLY，与现有 IPv4 listener 共存并共享认证及并发限额。源码入口增加可重复 --listen-ipv6，规范化去重并拒绝 zone。回环添加精确带方括号 HTTP Origin；非回环仍须密码与明确 HTTPS Origin，关闭 localAccess 同时拒绝 IPv6 回环。内部健康检查保留原私有 IPv4 通路。

## 备选方案

只在反代终止 IPv6 不改网关代码，但无法覆盖用户明确提出的原生监听需求。

依赖操作系统双栈 socket 可减少监听对象，但系统默认行为不同并可能与 IPv4 冲突，采用明确 V6ONLY。

## 影响

源码用户可选择 IPv6 上游；增加的入口仍需自行配置防火墙和 HTTPS。桌面页面、自动网卡枚举和手机地址发现未扩展，不把监听成功宣称为公网连接成功。

## 验证

5 项用例覆盖真实 ::1 HTTP、V6ONLY、Host/Origin 拒绝、localAccess、默认 IPv4、重复地址、多 listener 及无认证拒绝。公网和其他操作系统仍待部署验收。
