# Agent Note: 原生回合完成状态与耗时

Status: implemented

## 问题

平铺聊天需要明确一轮任务何时结束。页面计时、消息首末时间和当前分页长度都不能还原原生任务耗时，刷新和补载历史也可能改变结果。

## 决策

完成提示是平铺消息的同级文本行，与[逐条过程展示](../../implemented/simplification/2026-11-19-unfold-native-chat-process.md)共同保留，不建立分组或恢复折叠。提示按原生会话内回合身份关联，只有明确 completed、interrupted、failed 才显示对应终态；空闲本身不是结束证据。

成功回合同时有合法原生开始和结束时间时，显示两者差值，精确到整秒；缺失、无效或逆序时只显示完成。这个值是总经过时间，包含等待，不混用原生活动工时。中断与失败显示真实结果，不标为成功。历史刷新复用已有提示节点，滚动锚点继续绑定原消息。

Codex 复用标准化模型的 startedAt/completedAt；DSH 复用原生 turn/start 和 turn/end 事件时间。ZCode 从官方 V4 turnHeader 的 startedAt/endedAt 透传，用显式 productTurnId（旧协议回退到 turnId）关联；不从消息 createdAt 或 activeMs 推导。Antigravity 没有可靠边界的分页不生成耗时。

## 备选方案

DSH 安卓项目的通知计时以事件接收时的本地时钟计算，实现简单且适合当次通知，但进程重启和漏收事件会使历史失真，因此采用它所收录上游聊天界面的原生事件起止方式。用当前页首末消息时间可覆盖更多客户端，却会漏掉跨页起点与工具执行时间，因此不采用。

## 影响

同一原生回合在刷新、历史补载和中英文切换后保持同一个耗时；缺少原生证据的记录仍能阅读，不强行补齐数值。独立完成行占用少量垂直空间，消息顺序、复制、引用、权限和发送路径保持原有语义。定向测试覆盖成功、终态区分、缺失/逆序时间、跨原生补充消息、重复刷新、逐条详情与语言切换。

## 来源

- [DSH 聊天原生起止计算](https://github.com/kelai141/dsh-mobile-apk/blob/9671321e4433dadb186ddb7275465256ed14c327/scripts/patches/tests/fixtures/dsh-client-ui-chat-0.2.2-rc.3/lib/client.js#L19)
- [ZCode V4 原生回合定义](https://github.com/zai-org/ZCode/blob/a9fe401eede6e6e5e01be9048679c206fe6f02d9/packages/shared/src/zcode-protocol-v4/rows.ts#L69)
