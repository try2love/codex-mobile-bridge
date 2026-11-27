# Agent Note: 已回答卡片与用户补充任务的展示边界

Status: implemented

## 问题

长任务中的用户补充与问题卡片回复都可能是原生 user 消息。按回合位置或 requestId 推断会误折叠用户新任务；直接更改 role 又会影响历史和提交判断。

## 决策

保留 role、kind 和 requestId。仅在既有原生问题回复包装解析成功、每项具有 questionItemId 和字符串 answer，且 steering 消息已 accepted 时，增加纯展示字段 `interaction: response`。待接受或被拒绝的补充消息不标已处理。当前请求与审批仍来自独立的实时 requests，不从历史恢复可执行状态。

## 备选方案

- 所有同回合 user 都折叠：会隐藏临时补充任务，拒绝。
- 根据“允许”“继续”等文本归类：无法区分新任务与授权，拒绝。
- 更改原生 role：会改变提交及用户轮次语义，采用附加展示字段。

## 测试

回归先复现缺失标记，再覆盖普通 user 回复、accepted/pending/rejected steering、普通补充与不完整包装，检查 timeline 保留标记且原角色不变。

## 影响

该字段仅供共享聊天展示；其他接入须有协议证据才可标记。原生未提供已处理审批历史时不虚构记录。共用折叠规则见[活动分组](../feature/2026-11-16-shared-activity-groups.md)。
