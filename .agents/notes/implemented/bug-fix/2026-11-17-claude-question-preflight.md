# Agent Note: Claude 损坏提问不能变为空回答批准

Status: implemented

## 问题

原生 AskUserQuestion 的 input、questions 或问题项损坏时，归空或跳过会把批准映射为空 answers，丢失用户应确认的问题。

## 决策

共享 respond_answers 在批准前要求非空问题列表，每项带非空问题正文；非法输入在任何原生写入前明确阻断。Desktop、CLI 与 SSH 复用同一校验；用户仍能拒绝损坏请求。正常多选与手填回答保持原映射。ResumePreflightBlocked 只表示未发送；发送后回执丢失保留 unknown，不能重放。

## 备选方案

把非法问题归空可以避免解析异常，但会产生未确认的空批准；跳过损坏项同样可能批准不完整问题，因此明确阻断批准并保留拒绝入口。

## 影响

隔离 manager→Claude 生产派发测试覆盖损坏结构、无写入的 blocked、正常多选/手填、拒绝以及写入后 unknown 去重；未连接真实 Claude 或调用模型。模型目录缓存、手填模型与会话归属不变。
