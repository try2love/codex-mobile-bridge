# Agent Note: 桌面单独删除已配对的云设备

Status: implemented

## Problem

不用的手机仍出现在「网络访问 → 云设备」。关闭开关仅暂停连接；要永久移除旧授权，原先须在批准另一次配对时替换它。

## Decision

已配对的手机无论在线、离线还是已暂停，都能在这一行选择「删除」。确认框显示设备名与短码；删除只撤销此手机与本电脑的绑定，不撤销账号、手机登记或其他电脑的绑定。已删除的绑定不能恢复；重连需要重新配对。待扫码的申请仍按原流程到期。

本机 IPC 要求准确的服务器源、账号、网关与绑定 ID。网关先推进本地连接栅栏、关闭目标通道并停止发送该绑定的本地待发通知，再用网关凭据核验服务器上的绑定归属。只有核验通过，才用同账号凭据调用既有 `DELETE /v1/bindings/{id}`，无需更改服务端或手机协议。服务器确认撤销后，桌面清除该绑定的可信密钥和许可记录；界面只移除匹配回执的行，并忽略在途的旧目录响应。未知结果保留本地拒绝，由后续只读目录核验，不自动重放删除。

## 记录归属

[桌面二维码配对](2026-11-19-desktop-qr-pairing.md)继续持有配对证明和批准时原子替换的约束；[同账号目录](2026-11-03-account-device-auto-discovery.md)继续持有可逆开关和本地栅栏。[旧流程收敛](../simplification/2026-11-04-prerelease-cloud-contract-cleanup.md)与[已否的兼容提案](../../rejected/simplification/2026-11-04-retire-manual-cloud-pairing.md)仍适用，未恢复旧 `revoke` IPC。网页访问、直连和通知通道各自独立。未发现同义活跃提案。

## Alternatives considered

- 只隐藏该行可避免网络写入，但不会撤销手机已有的连接权限，也无法阻止它再次出现在目录中。
- 从云账号删除整台手机也能清理旧设备，但会撤销它对其他电脑的连接；桌面列表操作的范围应止于本电脑。

## Testing

- `.venv/bin/python -B scripts/test-backend.py --source-only -p 'test_cloud_*.py'` 通过 128 项，覆盖指定绑定、先封锁后联网、作用域校验、未知回执及迟到开启。`npm run test:boundaries` 通过。
- `node --test tests/cloud-desktop.test.cjs tests/cloud-i18n.test.cjs tests/cloud-panel-ui.test.cjs` 通过 55 项；`npx --no-install playwright test -c tests/browser/cloud-panel.config.cjs -g 'cloud device deletion'` 通过 Chromium、WebKit 的宽窄屏四项。使用合成目录覆盖取消、同名短码、局部更新、语言切换和迟到目录响应。
- 桌面全量 1,775 项通过、1 项跳过、1 项失败；隔离副本证明现有「离线」译文足以触发账号页面的 `Offline` 英文断言。本轮不修改该用例。笔记格式和归档封印检查通过；链接检查报出旧媒体播放笔记中代码示例里的「本机路径」。未打包安装、操作真实账号或设备。

同日本机部署沿用[独立 Local 签名](../process/2026-11-14-local-macos-signing.md)，覆盖安装 macOS arm64 `2.2.2-preview.1`，版本号不变。包签名、冻结网关的本地信任根、打包后的删除界面与 IPC、隔离启动均通过。切换前核验网关子进程和工作状态，正常退出旧网关与界面，不退出原生 Codex 守护进程，切换前后该进程身份一致。新网关回环健康检查返回 200，云中继恢复连接，临时隧道就绪，只读目录返回两条原有配对。八个账号、授权、配对和配置文件摘要一致。未在真实账号上执行删除，也未部署服务端或手机端。

本机证据在 `.tmp/cloud-device-delete-deploy-20261120/`，回退应用包在 `.local/client-backups/20261120-before-cloud-device-delete/`；回退只替换应用包，不覆盖资料目录。

## Consequences

无需新配对即可清理同名或退役手机，「允许连接」仍可用来暂时停用。永久删除需要云端确认；云端不可达时本机先阻止连接，但不能承诺远端已经撤销或从清单中移除。
