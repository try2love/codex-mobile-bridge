# Agent Note: Antigravity 原生 / 指令、Skill 多选与工作模式

Status: implemented

## 问题

Antigravity 原生客户端（Language Server）通过 `GetSlashCommands` 提供内置系统指令（`/goal`、`/compact`、`/schedule`、`/browser`、`/plan`、`/grill-me`、`/teamwork`、`/learn`、`/boost` 等）、个人与工作区工作流以及已启用 Skills，而既有接入层仅开放纯文本 `SendUserCascadeMessage` 且在 `capabilities` 中固定关闭了 `skills`、`plan`、`goal`。用户无法在手机端输入框触发原生 `/` 指令、无法像原生一样多选并发送 Skill 结构化引用，也无法通过共享 `WorkModes` / `ContextUsage` 使用原生的计划模式、目标模式和上下文压缩。

## 决策

1. **后端原生能力发现与结构化发送**：
   - 在 `bridge/clients/antigravity/cli_session.py` 开放只读白名单 RPC `GetSlashCommands`，按 `conversation_id`、`cwd` 和当前所选模型（通过 `defaultOverrideModelConfig` 与已启用 `clientModelConfigs` 动态解析）实时拉取原生指令，按 `info.type` 归一化为 `system` / `skill` / `workflow`。
   - 在 `bridge/clients/antigravity/adapter.py` 中：
     - `catalog` 新增 `section='commands'` 分支返回 `commands` 和 `skills`；
     - `capabilities`（仅桌面进程在线 `live=True` 时）开放 `skills`、`multiSkills`、`slashCommands`、`compact`、`plan`、`goal`；
     - `_build_message_items()` 将消息开头的已知 `/` 指令及多选 `plugins`（最多 8 个、自动按指令名去重）编码为原生 `{'item': {'slashCommand': {'info': ...}}}` 结构化项；未在原生目录中出现的一般 `/path` 保持纯文本发送；
     - 新增 `compact`、`goal`（`operation='set'`）及 `work-mode`（`default` / `plan`，持久化于 session settings 并仅在空闲且无待审批确认时应用；进入计划模式前核验当前模型的原生 `/plan` 能力，缺失或读取失败不修改既有设置，回到普通模式不依赖指令目录）；
     - 忙碌状态下通过 hook 执行 `queue` / `steer` 时，显式拒绝包含 `/` 指令、`plugins` 或 `plan`/`goal` 模式的后续消息，避免在原生会话运行期间旁路丢弃结构化字段。此检查同时读取已保存的计划模式；指令目录不可读时，不能证明未知 `/指令` 是普通文本，因此拒绝排队/插话，正常文字与明确的多级路径仍可排队。
2. **共享前后端与 Relay 协议适配**：
   - 在 `web/features/chat/composer-commands.js` 扩展 `action.native` 行为：支持在菜单显示 `Skill ·` / `工作流 ·` 前缀、选择后自动填充 `/<id> ` 并支持提交时直接透传给后端发送。
   - 在 `web/features/clients/desktop-sessions.js` 加入按当前作用域缓存的 `ensureNativeCommands()`、`nativeCommandActions()`（带 `plan`/`goal`/`compact` 兜底）以及基于 `capabilities.multiSkills === true` 的 Skill 多选门控（保持 Claude CLI 默认单选不变）。
   - 在 `relay/protocol.py` 将 Antigravity 的 `compact`、`goal` POST 动作加入 shared relay 白名单，并在 `web/shared/i18n.js` 补齐对应中英翻译。

## 备选方案

- **仅在前端硬编码 `/teamwork` 等指令清单**：无法覆盖动态加载的工作流与个人/项目 Skills，且缺乏原生所需的 `slashCommand.info` 元数据（如 `type` / `workflowPath`）。改为实时读取 `GetSlashCommands` 并以 `['plan', 'goal', 'compact']` 作为未拉取完成时的菜单兜底。
- **直接放开队列/插话夹带 Skill 或 `/指令`**：现有 Antigravity hook 仅注入文本跟随，无法表达原生结构化 `slashCommand` 项，因此明确拦截并提示等待本轮结束。

## 影响

- 不改变 Codex、`Claude`（含 CLI 单选 Skill）或 `DeepSeek` 现有路径。
- 验证已覆盖：`python3 -B scripts/check-boundaries.py`、Antigravity 与共享调度/Relay 后端测试、`ComposerCommands`/`DesktopSessions`/`i18n` 前端 Node 单测，以及本机真实运行中 Antigravity `GetSlashCommands` 实时目录拉取；完整打包测试（因无本地 `node_modules`）与真实 Playwright 浏览器套件、真机联调（需发往真实对话框的操作）将在 PR 说明中按实际未执行状态如实标出。

本地集成补验：目录读取失败的自定义工作流、已保存计划模式在 queue/steer 各复现一次结构化信息丢失；修复后均在入队前拒绝，普通文本及 `/usr/bin/python` 路径仍可入队。原始作者两条提交保留在本地合并历史中。

计划模式回归：缺少 `/plan` 时原内存设置及设置文件字节保持不变；实际提供能力后可保存，随后目录失效仍可退出计划模式。

指令缓存按网关、接入、会话、项目、账号、模型与模型目录身份隔离；旧模型有效缓存保留，但切换模型不串用。目录和 Skill 请求在写缓存前核对读取代次与捕获作用域，退出登录、账号切换或迟到回执不能恢复已清理内容。9 项新增回归先复现再修复，共享桌面 UI 222 项通过。本机只读核验未发现可用 AGY endpoint，因此没有真实登录后的原生写操作验收，不以合成用例代替该结论。模型分组后的 effort 透传及缓存作用域扩展见[参数契约修复](../bug-fix/2026-11-16-antigravity-effort-through-native-commands.md)。
