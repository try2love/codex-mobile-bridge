# Agent Note: 云面板复用共享中英文词典

Status: implemented

## Problem

云面板的状态、账号操作和错误提示需要随界面语言切换，同时不能因此重新发起云请求或授权操作。

## Decision

面板复用 `BridgeI18n` 和 `bridge-language`，覆盖静态控件、hosted/selfhost、服务到期、本地校验和已知错误。语言重绘只读显示缓存，不翻译或重建表单值、服务器/账号 ID、公钥或授权内容。等待中的控件保持禁用，日期与数字使用选定 locale，未知错误和诊断码仍可见。认证、origin 和中继契约不因翻译改变。

云设备的离线文案只在云面板按 `BridgeI18n.language()` 选择 `Offline` 或「离线」。共享词典不新增 `离线 → Offline`，避免其反向索引将上游模型目录中的原始 `Offline` 错误改成中文；在线文案继续使用上游已有词条。这一适配遵循[云服务分支范围](../simplification/2026-11-16-cloud-only-fork-scope.md)，不修改上游模型界面或断言。

原始范围也包含云 QR 倒计时与批准/撤销提示；这些业务入口随后由[当前云契约](../simplification/2026-11-04-prerelease-cloud-contract-cleanup.md)取代，本篇继续持有语言重绘不产生操作的边界。

## Alternatives considered

原词典选型未保存独立备选，不补造历史理由。离线状态的备选是继续添加共享词条：它可直接复用现有正反向翻译，但也改变了非云错误的显示。将这一状态文案留在云显示层可以保持原云界面的中英文结果，并恢复上游通用错误的语义。

## Consequences

中英文共享一套业务状态，语言变化不重放写入或授予访问；新增错误仍需同步词典，翻译不能修复底层运行依赖。

## Testing

2026-11-01 的 1,320 项 Node（含词典与 IPC）和 10 项 Edge 宽窄真实 DOM 合成回归通过，使用同源脚本及原 CSP。当时刷新并检查 Windows 面板中文显示，原网关继续运行；未验证 WebKit、真实登录/配对或手机。那次记录中的 `cloud_dependencies_missing` 后由[运行依赖修复](2026-11-01-cloud-runtime-dependencies.md)处理，不能把旧运行状态当作当前结果。
