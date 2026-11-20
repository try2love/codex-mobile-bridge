# Agent Note: 保留 Codex reasoning 与 assistant 流式中间输出

Status: implemented

## 问题

Codex 的 reasoning 条目有两个互不相干的字段：`summary` 是给界面显示的摘要，`content` 是原始思考正文。官方后端会按 `reasoning.summary` 参数返回摘要，第三方 Responses 兼容后端则经常只回 `content`，`summary` 始终为空。接入第三方 API 后，手机端仍会渲染“思考摘要”折叠行，展开后却没有任何文字；接入 DSH 时同样界面能显示思考，因为 DSH 适配器把 `thinking` 块直接映射成思考正文，不走 Codex 的 summary 字段。

网关此前只读 `summary`，且 app-server 的 `item/reasoning/textDelta`、`item/reasoning/summaryTextDelta` 通知落在 `_event` 的兜底 `return` 分支，思考既不能实时显示，也没有完整条目到达前的兜底。

Codex 的 `agentMessage` 也可能先通过 delta 发送正文，再由 `item/completed` 或 `turn/completed` 发送快照。如果快照中的正文为空，整体替换会抹掉已到达的文字；共享时间线仍会展示空 assistant 行，让用户看到模型名和操作栏，却看不到内容。

Codex 保存的历史中还会出现 `text` 只有空格的辅助 `agentMessage` / `assistantMessage`，紧挨着真正的最终答复。它不是一次可见回答，却会在旧手机客户端上生成带模型名、复制、引用和时间的空消息行。

## 决策

`normalize_item` 的 reasoning 分支先过滤空白字符串，再按 `summary` 优先、`content` 兜底拼出展示文本，标题保持“思考摘要”。两者都为空时仍保留这一行：它表示本次推理发生过，隐藏会让行序跳变，也会让用户以为客户端漏渲染。

`CliSessions._event` 增加 `item/reasoning/textDelta` 与 `item/reasoning/summaryTextDelta` 处理：按 `turnId` 找轮次，按 `itemId` 找条目，缺失时按 reasoning 形状新建，增量写入 `summary[summaryIndex]` 或 `content[contentIndex]`。索引缺失按 0，只对小于 1024 的非负整数补齐空串，非法或巨大索引不分配数组。

同一 ID 和类型的原生快照替换条目时，只在新快照字段为空的情况下保留已有的 `text` 与 `aggregatedOutput`。reasoning 的 `summary` 与 `content` 仅在新快照两者都为空时一起保留；若新快照提供摘要，则不把旧原始思考正文拼回去。不同 ID 或类型不合并。该规则同时用于 item 与 turn 快照，避免晚到 `item/started`、`item/completed` 或 `turn/completed` 抹掉已收到的中间正文。

`normalize_state` 在时间线投影前省略空白 `agentMessage` 和 `assistantMessage`，但保留有附件的 assistant 行、非空 commentary/最终答复以及空 reasoning 项。原生状态本身不被改写；旧手机客户端也不会再收到这些空回答占位行。若同一个流式条目随后收到正文，它会按原生身份重新进入时间线。

## 备选方案

只改 `model.py` 的兜底可以让历史中确实存在的正文显示出来，但无法恢复 reducer 在完成事件归并时已经丢弃的 delta，因此需要在 `CliSessions._event` 保留事件链中的现有数据。

渲染层隐藏空 assistant 行可以去掉空白，但不会恢复已到达的正文，也会掩盖事件状态被回退的问题，因此在数据归并边界修复。

把标题在兜底时改成“思考过程”能区分来源，但会让同一条目在不同后端下改名，也让既有文案与截图失效；用户明确要求保持“思考摘要”。

两者都为空时丢弃 reasoning 行可以避免空面板，但会改变既有行序与条目数量，且与“思考摘要代表一次推理”的语义冲突，因此保留空 reasoning 行。assistant 行的空白占位项不表示一次可见回答，故仅过滤这一类；在手机 WebView 中藏起正文仍会留下按钮和时间，并且要求旧手机端更新，因此在服务端共享投影边界处理。

## 影响

第三方 Responses 后端接入后展开“思考摘要”能看到思考正文，并在整轮进行中随增量出现。官方后端行为不变：有 `summary` 时仍只显示摘要，不把原始思考暴露到界面。`content` 只在摘要缺失时补位，不改变 Codex 侧的字段语义。assistant 正文与命令输出在空完成快照之后仍保留已收到的内容；原生非空完成内容仍优先。

回归覆盖摘要优先、正文兜底、双空 reasoning 保留、空值与缺失字段容错，以及思考增量先于 `item/completed` 到达、增量早于 `item/started` 时自动建条目两种流式顺序；另覆盖 assistant delta 后的空 item 完成、空 turn 完成、晚到 started 事件、空白 assistant 占位过滤、有附件空白消息保留及占位项收到正文后重新出现。

本地整合 [PR #130](https://github.com/try2love/codex-mobile-bridge/pull/130) 时保留原作者提交，另补以下边界：只接受列表形式的 summary/content，不把错误字符串拆成逐字展示；增量索引必须是非 bool 整数且小于 1024，拒绝巨大稀疏索引而不分配无界数组；晚到的 reasoning started 不抹掉已收到的增量，completed 仍是权威完整替换。53 项 modes 与 64 项 CLI 会话回归通过；其中 39 个临时 Unix socket 夹具初次被沙箱禁止，放行后只补跑这些失败项。没有连接真实模型或修改用户聊天。
