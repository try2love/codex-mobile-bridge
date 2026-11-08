# Agent Note: 第三方 Responses 只回思考正文时补全思考摘要

Status: implemented

## 问题

Codex 的 reasoning 条目有两个互不相干的字段：`summary` 是给界面显示的摘要，`content` 是原始思考正文。官方后端会按 `reasoning.summary` 参数返回摘要，第三方 Responses 兼容后端则经常只回 `content`，`summary` 始终为空。接入第三方 API 后，手机端仍会渲染“思考摘要”折叠行，展开后却没有任何文字；接入 DSH 时同样界面能显示思考，因为 DSH 适配器把 `thinking` 块直接映射成思考正文，不走 Codex 的 summary 字段。

网关此前只读 `summary`，且 app-server 的 `item/reasoning/textDelta`、`item/reasoning/summaryTextDelta` 通知落在 `_event` 的兜底 `return` 分支，思考既不能实时显示，也没有完整条目到达前的兜底。

## 决策

`normalize_item` 的 reasoning 分支先过滤空白字符串，再按 `summary` 优先、`content` 兜底拼出展示文本，标题保持“思考摘要”。两者都为空时仍保留这一行：它表示本次推理发生过，隐藏会让行序跳变，也会让用户以为客户端漏渲染。

`CliSessions._event` 增加 `item/reasoning/textDelta` 与 `item/reasoning/summaryTextDelta` 处理：按 `turnId` 找轮次，按 `itemId` 找条目，缺失时按 reasoning 形状新建，增量写入 `summary[summaryIndex]` 或 `content[contentIndex]`。索引缺失按 0，只对小于 1024 的非负整数补齐空串，非法或巨大索引不分配数组。晚到的 `item/started` 不抹掉已收到的 reasoning 增量；`item/completed` 到达时仍按 id 整体替换，增量只影响完成前的中间态。

## 备选方案

只改 `model.py` 的兜底即可让历史与完成后的条目有文字，但整轮期间折叠行一直是空的，与官方后端的实时体验不一致，因此同时补流式分支。

把标题在兜底时改成“思考过程”能区分来源，但会让同一条目在不同后端下改名，也让既有文案与截图失效；用户明确要求保持“思考摘要”。

两者都为空时丢弃该行可以避免空面板，但会改变既有行序与条目数量，且与“思考摘要代表一次推理”的语义冲突，因此保留空行。

## 影响

第三方 Responses 后端接入后展开“思考摘要”能看到思考正文，并在整轮进行中随增量出现。官方后端行为不变：有 `summary` 时仍只显示摘要，不把原始思考暴露到界面。`content` 只在摘要缺失时补位，不改变 Codex 侧的字段语义。

回归覆盖摘要优先、正文兜底、双空保留、空值与缺失字段容错，以及思考增量先于 `item/completed` 到达、增量早于 `item/started` 时自动建条目两种流式顺序。

本地整合 [PR #130](https://github.com/try2love/codex-mobile-bridge/pull/130) 时保留原作者提交，另补以下边界：只接受列表形式的 summary/content，不把错误字符串拆成逐字展示；增量索引必须是非 bool 整数且小于 1024，拒绝巨大稀疏索引而不分配无界数组；晚到的 reasoning started 不抹掉已收到的增量，completed 仍是权威完整替换。53 项 modes 与 64 项 CLI 会话回归通过；其中 39 个临时 Unix socket 夹具初次被沙箱禁止，放行后只补跑这些失败项。没有连接真实模型或修改用户聊天。
