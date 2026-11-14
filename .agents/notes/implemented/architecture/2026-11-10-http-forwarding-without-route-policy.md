# Agent Note: 各传输统一交由 HTTP 处理器判断业务权限

Status: implemented

共享中继的转发规则由[上游共享中继与云 HTTP 分别校验](2026-11-14-shared-relay-cloud-framing.md)持有；本篇对 Noise/P2P 与账号 Web 的通用转发决定继续有效。以下保留取消各链路操作表时的理由与取舍。

## Problem

手机 Noise/P2P、旧共享中继、账号 Web 各自维护接口清单，新增业务必须重复修改，遗漏时请求在到达现有 HTTP 处理器前被拒绝。用户明确要求在所有链路删除逐接口白名单、黑名单，并允许云服务上线前的协议调整。

## Decision

删除 `mobile_policy.py`、`relay.protocol` 的路径/动作表以及账号 Web 的创建接口例外。所有转发共用相对 URL、方法 token、正文长度等报文校验，不按接口名称、客户端 family 或 GET/POST 清单筛选。App Rust 和 Go Web 代理同步取消方法清单，POST/PUT/PATCH/DELETE 正文原样转发，GET/HEAD 不携带正文。

业务是否存在、执行能力、实例归属、账号管理版本、权限确认与文件访问由原 HTTP 处理器判断。认证、Origin/CSRF、设备密钥批准、账号/电脑归属、撤销、IP 规则、禁止重定向、凭据隔离、Range 边界及流量上限保持各自职责。固定静态资源映射与原生 IPC 的动作分派不是本次删除对象。注册与兑换的 App 页面仍不提供。

未知 API 返回处理器的 404，方法不受支持时由处理器返回 405/501。拒绝写入的 HTTP 响应关闭连接，防止未读取正文被连接池解析为下一条请求。HEAD 保留描述性 Content-Length，但不要求响应正文；云网页不对空 HEAD 正文进行资源改写。请求结果未知时保留原操作记录，不重放业务写入。

## 记录审计

- [原手机业务策略](2026-11-10-native-mobile-business-policy.md)：接口清单决定被本篇取代，账号 ID 和管理版本决定继续有效；保留旧取舍以解释此次翻转。
- [云网页透传](../feature/2026-11-07-cloud-web-proxy.md)：部分取代逐接口策略，保留账号信任、资源适配、会话撤销和安装经验。
- [持久中继](2026-11-05-persistent-relay-v2.md)：部分重叠，原历史验证中的接口名单描述不再是新增路由要求，配对、流控和未知写入约束继续有效。
- [创建模型](../feature/2026-11-04-shared-creation-model-selection.md)及[手机更新](../feature/2026-10-12-gateway-assisted-android-update.md)：仅更新接口转发归属。搜索到的 IP、静态资源、通知模板与原生 IPC 清单具有其他用途，未删除；没有完全吸收或失效的提案。

## Alternatives considered

- 继续补全各清单可以保留不同传输的额外限制，但维护重复且已造成真实功能缺失，用户明确取消该策略。
- 改为禁止少数管理接口的黑名单能减少维护，却仍保留同一套逐接口功能开关，因此不采用。

## Testing

`test_http_forwarding` 检查通用报文边界；实际回环 HTTP 的 Noise/P2P 公用转发、云网页和旧中继测试覆盖未来接口、写方法/正文、HEAD、未知接口返回及连接复用。既有认证/CSRF/撤销/跨设备/未知写入等 147 项回归通过；HTTP/认证/Harness 补充 45 项通过。Go 侧隔离 PostgreSQL 与真实 HTTP/WebSocket 测试及 `go vet` 通过；Rust 侧 81 项通过、1 项专用 P2P 夹具跳过，含真实 Noise/WebSocket 和直连 HTTP 的方法/正文测试，Clippy 通过。安装证据与完整业务验收分别记录，合成客户端测试不代替真机组合。

桌面安装包保留在忽略的 `.local/deliveries/http-forwarding-desktop-2026-11-10/`，SHA-256 为 `ff5fbb2943fd8aeb28ff1157bc339b185a9ca2ede81011a7f4b403c95cee491c`。按用户已有授权覆盖安装并重启成功；主程序、app.asar、网关程序与构建文件哈希一致。安装前备份并比较 36 个配置/运行文件，差异仅为停启会改变或删除的 `cloud-status.json`、`network-status.json`、`gateway-control.json`；账号和连接配置保持一致。实际 App DLL 的 FFI 8 项及使用本桌面工作树的真实 HTTP 契约 1 项再次通过。

用户授权后，配套云服务于 2026-11-10 部署 `http-forwarding-20261110-d35a7a5`，核对运行二进制、健康、公网 TLS、认证拒绝及持久记录摘要。桌面云连接恢复为 `connected`，同签名更新的 Lenovo 手机保留登录态并重新读取 Codex 聊天列表。远端只切换 server 容器，数据库、HTTPS 和 P2P 服务保持运行。

## Consequences

新增业务接口无需再同步各通道名单，直连和转发统一由网关控制。相应代价是 HTTP 处理器必须完整承担当次权限校验，未来接口不能依赖传输层的隐含拒绝。配套桌面、测试手机和云服务均已更新；合成夹具与真实读取验证不代替全量真实客户端或 iOS 验收。
