# Agent Note: Claude 工具回执与最终回答展示归类

Status: implemented

## 问题

真实 Claude 历史中工具回执可带附加 text，原投影把 text 显示为普通 user，导致回执打断过程分组。助手原生 stop_reason 也被连接器过滤，中间工具说明与最终回答无法区分。

## 决策

连接器仅增加 sourceToolAssistantUUID 和已知 stop_reason 的安全白名单。原生 user 事件同时具有工具来源与 tool_result 块时，其 text 标记 interaction=response，仍保留原 role。普通补充不按正文推断。助手 end_turn 文本为最终回答，tool_use 文本为中间说明；其他结束原因不猜测。

## 备选方案

- 所有带 tool_result 的事件都视为互动回复：来源不足，保留工具来源字段作为附加证据。
- 根据“批准”等字词折叠：会误伤用户补充，拒绝。

## 影响

改动只作用于共享展示元数据，待审批请求和原生响应接口不变。不扩展凭据或其他原始事件字段的导出范围。

## 验证

真实本地事件结构只读核验；回归覆盖混合工具回执、普通补充、stop_reason 以及连接器白名单。当前 Test 中 Claude 接入关闭，不能据此宣称已完成新任务的实时交互验收。
