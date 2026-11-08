# Agent Note: 原生会话改名与 DSH 项目成员边界

Status: implemented

后续范围：[原生会话重命名](2026-11-04-native-session-renames.md) 增加已持有 CLI 的官方控制通路、DSH 已加载 Session store 与 ZCode 持久任务服务，并取消改名的别名回退；本文原生非激活、身份和项目边界继续适用。

## 问题

共享菜单需要补齐 Claude、DSH 的改名和分类入口，但方法存在并不代表调用没有副作用。DSH 的 `sessionController.rename` 会解析或恢复冷会话；部分第三方 Harness 又改变了项目成员的目录限制。直接通用转发会破坏仅修改显示元数据、不启动会话和保留原工作目录的约束。

## 决策

Claude Code Desktop 与 Cowork 仅在连接器同时发现 `getSession` 和 `updateSession` 时提供 `mobileRename`。调用使用 `updateSession(sessionId, {title, titleSource: 'user'})`，先核对目标 ID，再从原生 `getSession` 读回标题、ID、cwd 和 Cowork 文件夹。方法只传标题，不传模型、账号、目录或项目字段。CLI 与 WSL 不借用 Desktop 方法，不为改名恢复 CLI，也不直接改写其 JSONL。

DSH 只对 `ctx.agents.get(id)` 已存在的原生会话提供原生改名能力，直接调用同一原生 `sessionTitle.rename(agent.session, title)` 服务。服务核对会话仍在原生 live store 中，再写入用户来源的标题事件。Bridge 不调用会隐式恢复的 `sessionController.rename`。冷会话或已卸载会话返回明确的 `notSent`；成功写入必须由 `sessionController.list` 读回确认。菜单中的 Bridge 别名回退由共享元数据层负责，不在此适配器中伪装原生标题。

DSH 原生项目操作使用 `workspaceRegistry.list/get` 与工作区的 `attachSession/detachSession`，按 `sessionIds` 读取成员，不根据 cwd 猜测所属项目。可选目标必须经过 `realpath` 确认与原 cwd 相同；提交前再次检查，写后核对成员和 cwd。即使第三方实现放宽约束，Bridge 也不允许借项目操作改变原工作目录。原生分区没有已确认接口，因此不提供原生分区能力。

原生修改返回 `status: confirmed` 仅代表读回确认。写入后失败或不匹配保持结果未知，不重放；确定未派发的冷会话、目录不匹配或目标消失返回 `notSent: true`。共享管理器负责跨请求和重启的持久化回执。Claude 文件桥接把 `mobileRename` 列入写操作集合，升级连接器 revision 13；DSH host 升级 revision 9。DSH 保留从本轮基线核验的 revision 8 LF/CRLF 源码摘要，只将原有受控连接识别为待显式升级，不在扫描中改写源码、凭据或配置。新网关不能把仍在运行的旧连接器当作新接口。

## 原生证据与验证

- 本机 Claude 3.6556.2 的 `app.asar` 中，Code 的 `LocalSessions.updateSession` 和 Cowork 的 `LocalAgentModeSessions.updateSession` 都接收 `(sessionId, options)`，底层保存元数据并发送更新事件。Code 自己负责向既有 CLI 标题同步；Bridge 不另行操作 CLI 文件或调用启动接口。
- 本机官方 DeepSeek Harness 内核包 0.2.1-rc.3 的 `dsh-api-session-controller` 明确由 `rename` 调用 `resolveAgent`；其解析器可恢复会话。`dsh-session-title.rename` 的公开服务实现只接受原生 live session，追加 `session/title` 事件并使用 `source.kind: user`。
- 本地 DSH Tauri 0.25.0 的内核工作区实现带有 `dsh-tauri-worktree` 补丁，删除了官方 `attachSession` 中的同 cwd 比较。Bridge 的独立目录检查用于守住原约束。项目 API 的语义同时与[官方工作区文档](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/subsystems/workspace.md)对应。
- `claude-session-rename.test.cjs` 在 CDP、签名文件两条合成传输中验证 Code/Cowork 改名、控制字符与长度拒绝、原生 ID 核对、未知结果不重试和不启动会话。
- `deepseek-session-actions.test.mjs` 验证冷会话不激活、live 标题服务读回、项目加入与移出、第三方宽松实现仍不能跨 cwd、相同成员不重复写入和未知结果不重试。
- `test_claude_session_rename.py` 验证适配器原生方法选择、网关 ID 转换、缺失能力拒绝和未知结果。相关已有 Claude 连接器/生命周期/owner/适配器及 DSH 桌面/插件/手动绑定回归通过。

这些证据来自已安装源码及隔离合成测试；没有改写真实用户聊天，也没有在本轮专项中安装或升级共享连接器。它们不等同于升级后的三系统和手机实机验收。

## 备选方案

直接复用 DSH 既有 `sessionController.rename` 最简洁，也能覆盖冷会话，但它会隐式恢复会话，因此只复用其底层已加载标题服务。把冷会话直接追加或重写存储日志也能改变历史标题，但绕过原生存储所有权和活跃写入检查，故不采用。

完全信任第三方 `attachSession` 可支持更宽松的工作区组织，但不同内核补丁的目录语义不一致；本轮保留同 cwd 边界，并由共享层提供明确的 Bridge 分类，不扩张原生迁移能力。

## 历史笔记审计

按改名、原生 API、会话菜单和分类检索活跃记录。[共享会话菜单](2026-10-28-shared-session-menu.md)部分重叠，本篇补充原生方法核验与冷会话约束，不替代菜单和确认行为；[DSH 通用插件](2026-10-28-dsh-generic-plugin-binding.md)继续持有实例绑定和手动安装契约。本轮修改不改变[停止释放再归档](2026-10-27-confirmed-disconnect-archive.md)及[本地优先置顶](2026-10-26-local-first-session-pins.md)的决定。未发现同义的待实施或已否决改名提案。

## 影响

原生改名的支持范围按实际能力和会话加载状态显示。Claude CLI、WSL 与 DSH 冷会话不承诺原生改名；DSH 原生项目候选可能只有当前目录对应的一项，也可能没有候选。连接器升级需要走既有身份和在途写入检查，不能仅因代码已更新就视为运行版本已生效。
