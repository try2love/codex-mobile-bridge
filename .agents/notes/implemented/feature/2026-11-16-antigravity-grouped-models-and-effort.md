# Agent Note: Antigravity 模型列表按基座聚合与思考强度选择

Status: implemented

## 问题

Antigravity 原生 `GetUserStatus` 的 `clientModelConfigs` 会把同一个基座模型的多种思考强度拆成扁平条目（例如 `(Low)`、`(Medium)`、`(High)`、`(XHigh)` 共十余项），直接平铺会让模型弹窗与配额列表冗长重复，且原生面板原有 `effort: False` 限制让用户无法复用共享的「思考强度」分段控件。

## 决策

- **基座模型与变体聚合**：在 `AntigravityCli._build_catalog_from_status()` 通过 `_split_model_variant()` 识别后缀为 `(None|Minimal|Low|Medium|High|XHigh|Max|Ultra)` 的启用条目；当基座 ID 不与独立模型碰撞时聚合成单一基座模型条，生成有序 `efforts`、`defaultEffort` 与 `variants` 映射，并将 `usageSummary.items` 与配额文本按基座去重保留一条代表配额。
- **强度校验与原生枚举还原**：新增 `canonicalize_model_selection()` 与 `_resolve_from_catalog()`，将 `(model, effort)` 还原为原生 `MODEL_*` 枚举供 `StartCascade` 与 `SendUserCascadeMessage` 使用；对需要强度但缺省、强度不被该模型支持、或手动填写 `MODEL_*` 同时传 `effort` 的请求显式报错。
- **向后兼容与动态能力暴露**：`resolve_model_display()` 与 `_save_session_model()` 将历史已保存的扁平变体 ID（如 `gemini-4-hydrogen-xhigh`）自动投影为 `(base_id, effort, modelDisplayName)`；`Antigravity` 与 `DesktopSessions` 仅在目录实际包含带 `efforts` 的分组模型时开放 `capabilities.effort`（保持 `manualEffort: False`），新建弹窗同步启用 `fallbackCapabilities.effort: true`。

## 备选方案

曾考虑在前端单方面用正则折叠展示而不改后端契约，但这会导致配额重复、各端（桌面/Web/管理页/新建会话）显示与发送逻辑分裂且无法持久化标准 `(model, effort)`；集中在 Antigravity adapter/cli 层统一聚合与映射可让共享模型弹窗直接生效并保持旧配置兼容。

## 验证

- 基于 `upstream/main` (`1004e5ae`) 独立分支实测：
  - `python3 -B scripts/check-boundaries.py` 通过；
  - `python3 -B scripts/test-backend.py --source-only -p "test_antigravity*.py"`：`113/113` 通过；
  - `TZ=Asia/Macau node --test tests/desktop-sessions-ui.test.cjs tests/frontend-assets.test.cjs tests/client-accounts-ui.test.cjs tests/accounts-ui.test.cjs`：`330/330` 通过；
  - 无头 Chromium + 独立 `GatewayServer` 实测 17 个变体聚合为 6 个基座模型、旧扁平 ID 自动投影及设置切换往返正常。

## 影响

完全保留旧扁平 `modelId` 与手动 `MODEL_*` 枚举兼容；不改变其他客户端（Codex / Claude / DeepSeek / ZCode）模型逻辑与模块边界。
