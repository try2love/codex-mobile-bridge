# Agent Note: 云面板复用共享中英文词典

Status: implemented

## Problem

云面板的状态、账号操作和错误提示需要随界面语言切换，同时不能因此重新发起云请求或授权操作。

## Decision

面板复用 `BridgeI18n` 和 `bridge-language`，覆盖静态控件、hosted/selfhost、服务到期、本地校验和已知错误。语言重绘只读显示缓存，不翻译或重建表单值、服务器/账号 ID、公钥或授权内容。等待中的控件保持禁用，日期与数字使用选定 locale，未知错误和诊断码仍可见。认证、origin 和中继契约不因翻译改变。

原始范围也包含云 QR 倒计时与批准/撤销提示；这些业务入口随后由[当前云契约](../simplification/2026-11-04-prerelease-cloud-contract-cleanup.md)取代，本篇继续持有语言重绘不产生操作的边界。

## Alternatives considered

无；原记录未保存独立备选，不补造选型理由。

## Consequences

中英文共享一套业务状态，语言变化不重放写入或授予访问；新增错误仍需同步词典，翻译不能修复底层运行依赖。

## Testing

2026-11-01 的 1,320 项 Node（含词典与 IPC）和 10 项 Edge 宽窄真实 DOM 合成回归通过，使用同源脚本及原 CSP。当时刷新并检查 Windows 面板中文显示，原网关继续运行；未验证 WebKit、真实登录/配对或手机。那次记录中的 `cloud_dependencies_missing` 后由[运行依赖修复](2026-11-01-cloud-runtime-dependencies.md)处理，不能把旧运行状态当作当前结果。
