# Agent Note: 已批准原生手机的独立业务路由策略

Status: implemented

本篇接口清单决定已被[所有链路取消接口策略](2026-11-10-http-forwarding-without-route-policy.md)取代；以下保留当时的取舍。账号 ID 与管理版本的决定继续有效，当前源码不再包含 `mobile_policy.py`。

## Problem

原生 App 全量移植 Web 时，旧共享中继清单缺少 ZCode、独立实例管理、新建项目与会话管理。直接修改旧清单会同时扩大服务器可读中继和账号 Web 的访问面。

## Decision

已获本地可信密钥许可的 Noise 手机请求使用 mobile_policy 的精确方法/路径清单，P2P 复用相同策略。仍转发私有 HTTP listener，原有认证、CSRF、账号、实例与文件权限继续由 HTTP 业务处理器执行；请求头只允许 Range/If-Range，单次正文限制保持 20 MiB。

云注册、兑换、本机安装和任意 IPC 不在新清单中。WSL 账号沿用 Web 已开放接口及其能力限制，其他工具仅开放已有管理接口；共享中继与账号 Web 继续使用原策略。

Codex 账号列表以 `managementRevision` 公开现有 `AccountInfo.source_stamp()`；原生手机复用该版本、已有账号 ID 与实例作新鲜性校验，不增加配置扫描或独立账号指纹机制。其他客户端继续使用现有 `modelCatalogIdentity`，WSL 使用已有 `identity`。

## 记录审计

[持久中继](2026-11-05-persistent-relay-v2.md)部分重叠，继续持有配对、流控和未知写入边界。[独立客户端](2026-11-05-independent-client-instances.md)部分重叠，family + entry 校验仍在原 HTTP 层。[云账号 Web](../feature/2026-11-07-cloud-web-proxy.md)属于另一授权主体，保持其独立策略。检索到的 ZCode、项目、通知笔记继续持有业务约束，没有完全吸收或废弃的记录。

## Alternatives considered

扩展 relay.protocol 最少代码，但会扩大旧共享中继权限，因此新建手机策略。允许所有 /api 路径易于同步，但会暴露未来接口及本机管理入口，因此使用闭合清单。

## Consequences

手机与桌面 Web 可以覆盖相同已开放业务，同时保留授权主体差异。新增 HTTP 业务时需要同步手机清单与拒绝回归。

## Testing

test_mobile_business_policy 验证新增业务、实例参数、旧中继权限不扩大及 URL/路径/方法/正文边界；完整执行结果随本轮交付记录。
