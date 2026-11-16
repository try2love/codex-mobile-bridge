# Agent Note: Antigravity 打断终止整棵任务树、清理 Hook 心跳与并发写入可停止

Status: implemented

## 问题

1. `AntigravityCli.stop()` 原仅发送 `CancelCascadeInvocation` 且未传 `kill_background_tasks=True`、也未调用 `ForceStopCascadeTree`，当主调用已空闲而派生子任务或后台命令仍在运行时无法彻底停下。
2. `HookIPCServer.request_stop()` 未立即移除 `_heartbeats` 中的活跃标记，且已在 `wait()` 中被唤醒的 `pre_tool_use` 会在加锁后把同一 `cid` 重新写入 `_heartbeats`，导致状态继续误判为运行中；另外新建会话在 `conversation_summaries.db` 尚未落盘前 `_hook_session()` 无法匹配 `brain/<uuid>`。
3. 前端 `DesktopSessionsView` 将 `stop` 与 `send` 等普通写入共用同一 `this.busy` 与 `freshDetail` 门禁：任一写入触发 `invalidateReads` 且尚在传输期间，`■ 停止` 会立即被禁用并拒发请求。

## 决策

- **原生整树强停**：在 `AntigravityCli._ALLOWED_METHODS` 新增 `ForceStopCascadeTree`；`stop()` 先发 `CancelCascadeInvocation`（携带 `kill_background_tasks=True`，若主调用已空闲抛出 `BridgeUnavailable` 则继续），再强制调用 `ForceStopCascadeTree({'conversationId': conversation_id})` 并失效轨迹缓存。
- **Hook 状态与会话识别收敛**：`request_stop()` 立即清除目标 `conversation_id` 心跳；`_handle_pre_tool_use()` 唤醒后仅在 `cid not in self._stop_requests` 时更新心跳；`_hook_session()` 在摘要库未命中且 `cid` 为合法 UUID 时回退校验非符号链接的 `brain/<cid>/.system_generated/logs/transcript.jsonl`。
- **前后端并发止损**：`DesktopSessionsView` 将 `stopping` 与 `pendingWrites` 拆开，`canMutate(allowInFlightWrite=false)` 允许在仍有非停止写入未返回期间触发 `stop` 请求，而不放宽其余写入互斥与防重放语义。

## 备选方案

曾考虑在本地直接强制杀进程或让 `stop` 直接绕过 `canMutate()`，前者违背仅通过原生 RPC 与受控接口操作客户端边界，后者会在未连接或只读状态下误放行停止。上述方案全部留在原生协议与既有状态守卫内。

## 验证

- 基于 `upstream/main` (`1004e5ae`) 独立分支实测：
  - `python3 -B scripts/check-boundaries.py` 通过；
  - `python3 -B scripts/test-backend.py --source-only -p "test_antigravity*.py"`：`113/113` 通过；
  - `TZ=Asia/Macau node --test tests/desktop-sessions-ui.test.cjs tests/frontend-assets.test.cjs tests/client-navigation.test.cjs`：`276/276` 通过；
  - 修复测试夹具漏注册 `self.addCleanup(adapter.close)` 导致 Windows `gateway.lock` 占用 (`WinError 32`) 的清理问题。

## 影响

不改动其余桌面客户端停止流程与未知写入不重放契约；RPC 白名单保持显式列举，仅新增 `ForceStopCascadeTree`。
