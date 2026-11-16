# Agent Note: Antigravity 打断终止整棵任务树、清理 Hook 心跳与并发写入可停止

Status: implemented

## 问题

1. `AntigravityCli.stop()` 原仅发送 `CancelCascadeInvocation` 且未传 `kill_background_tasks=True`、也未调用 `ForceStopCascadeTree`，当主调用已空闲而派生子任务或后台命令仍在运行时无法彻底停下。
2. `HookIPCServer.request_stop()` 未立即移除 `_heartbeats` 中的活跃标记，且已在 `wait()` 中被唤醒的 `pre_tool_use` 会在加锁后把同一 `cid` 重新写入 `_heartbeats`，导致状态继续误判为运行中；另外新建会话在 `conversation_summaries.db` 尚未落盘前 `_hook_session()` 无法匹配 `brain/<uuid>`。
3. 前端 `DesktopSessionsView` 将 `stop` 与 `send` 等普通写入共用同一 `this.busy` 与 `freshDetail` 门禁：任一写入触发 `invalidateReads` 且尚在传输期间，`■ 停止` 会立即被禁用并拒发请求。

## 决策

- **原生整树强停**：在 `AntigravityCli._ALLOWED_METHODS` 新增 `ForceStopCascadeTree`；`stop()` 先发 `CancelCascadeInvocation`（携带 `kill_background_tasks=True`，若主调用已空闲抛出 `BridgeUnavailable` 则继续），再强制调用 `ForceStopCascadeTree({'conversationId': conversation_id})` 并失效轨迹缓存。
- **Hook 状态与会话识别收敛**：`request_stop()` 立即清除目标 `conversation_id` 心跳；`_handle_pre_tool_use()` 唤醒后仅在 `cid not in self._stop_requests` 时更新心跳；`_hook_session()` 在摘要库未命中且 `cid` 为合法 UUID 时回退校验非符号链接的 `brain/<cid>/.system_generated/logs/transcript.jsonl`。
- **前后端并发止损**：`DesktopSessionsView` 将 `stopping` 与 `pendingWrites` 拆开，在途写入记录绑定 `provider / sid / gateway / account / generation`。只有与当前作用域完全相同的在途写入，才允许停止操作临时绕过该写入造成的 `freshDetail=false`；连接、只读、恢复状态及停止能力检查继续生效。已 active 的任务可在同作用域普通写入期间停止；首次发送尚未返回且状态仍 idle 时，只在同作用域确有 `send` 在途并且 `capabilities.stop===true` 时显示停止。settings/access 等其他写入不作为发送证据，显式 `stop=false` 始终拒绝停止。停止请求在途期间，重复停止与所有普通写入均不发送；普通写入仍保留原有总量互斥，未知结果不自动重放。

## 备选方案

全局写入计数能保留原有 busy 互斥，但无法区分切换会话、账号或网关后的旧请求，因此仅凭计数不足以授权停止。改为保存带作用域的在途记录，在原请求 finally 中删除自身；不因界面切换而把旧操作误判为已结束。仅复用发送预览也能改善首次发送按钮，却不能证明真实 send 请求仍在途，更不能覆盖 active 状态下设置写入与停止的并发。

曾考虑在本地直接强制杀进程或让 `stop` 直接绕过 `canMutate()`，前者违背仅通过原生 RPC 与受控接口操作客户端边界，后者会在未连接或只读状态下误放行停止。上述方案全部留在原生协议与既有状态守卫内。

## 验证

- 基于 `upstream/main` (`1004e5ae`) 独立分支实测：
  - `python3 -B scripts/check-boundaries.py` 通过；
  - `python3 -B scripts/test-backend.py --source-only -p "test_antigravity*.py"`：`113/113` 通过；
  - `TZ=Asia/Macau node --test tests/desktop-sessions-ui.test.cjs tests/frontend-assets.test.cjs tests/client-navigation.test.cjs`：`276/276` 通过；
  - 修复测试夹具漏注册 `self.addCleanup(adapter.close)` 导致 Windows `gateway.lock` 占用 (`WinError 32`) 的清理问题。

- 本地集成补充验证：`node --test tests/desktop-sessions-ui.test.cjs`：`238/238` 通过，其中新增十项前端回归覆盖五维作用域隔离、首次发送停止、非发送写入不显示停止、显式不支持，以及停止在途时重复停止与普通写入互斥。日志位于 `.tmp/pr178-validation/desktop-sessions-stop-scope.log`。此处为合成 UI 验证，不代表真实原生任务已停止。

## 影响

共享 UI 保留一个短生命周期的在途记录集合，直到对应请求返回；切换界面不会提前释放原写入互斥，也不能借用旧作用域授权。其余桌面客户端继续使用既有停止能力声明与未知写入不重放契约；RPC 白名单保持显式列举，仅新增 `ForceStopCascadeTree`。
