# Agent Note: 工作台聊天页直接右侧分屏与分屏缩略图可见性

Status: implemented

## 问题

在桌面端工作台停留在 `聊天`（或 `child:` 侧边聊天）标签页时，右上角「在右侧分屏」按钮（`.wb-split-button`）虽然可见，却在 `paint()` 中被直接设为 `disabled = true`，点击无反应；同时 `refreshThumbnails()` 在分屏开启且 `files` 处于右侧面板时因只检查 `session?.active !== 'files'` 而在部分切换路径漏刷图片缩略图。

## 决策

- **直接从聊天触发右侧分屏**：取消 `paint()` 对 `chat` / `child:` 标签页禁用 `.wb-split-button` 的限制，改为仅受 `splitEligible`（容器宽度 `>= 720px`）控制。点击 `.wb-split-button` 调用 `toggleSplit()`：
  - 当前已在工具/文件标签页时，直接将该标签页移入右侧分屏；
  - 当前在 `聊天` / `child:` 标签页且存在已开启的非聊天标签页时，优先恢复上次使用的 `lastToolTab`（由 `select()` / `split()` / `close()` 维护，否则回退 `files` 或首个非聊天标签页）；
  - 尚无开启的非聊天标签页时，直接调用 `files(true)` 在右侧分屏开启「文件」列表。
- **缩略图可见性判定统一**：`refreshThumbnails()` 复用 `this.isVisible(session, {id: 'files'})`，兼容主视图与右侧分屏。

## 备选方案

曾考虑在 `聊天` 页隐藏分屏按钮，但右上角功能入口消失会让用户误以为不支持分屏，且仍迫使用户先切换离开聊天再分屏；直接支持在聊天页开启或恢复右侧分屏更符合操作直觉，同时不改变 `unsplit()` 保留当前工具标签与拖拽分屏的既有契约。

## 验证

- 基于 `upstream/main` (`1004e5ae`) 独立分支实测：
  - `python3 -B scripts/check-boundaries.py` 通过；
  - `TZ=Asia/Macau node --test tests/desktop-sessions-ui.test.cjs tests/frontend-assets.test.cjs tests/layouts.test.cjs tests/workbench-*.test.cjs tests/file-actions-path.test.cjs tests/bootstrap.test.cjs`：`252/252` 通过（含新增分屏按钮直接从聊天开启、恢复 `lastToolTab` 与右侧分屏缩略图测试）；
  - 无头 Chromium + 独立 `GatewayServer` 端到端点击验证通过。

## 影响

仅调整工作台桌面宽屏右上角分屏按钮的启用条件与目标解析，窄屏（`< 720px`）行为、拖拽分屏、工作台退出分屏行为与路由契约不变。
