# Agent Note: 共用原生工作模式与 ZCode 新建聊天

Status: implemented

## 问题

非 Codex 聊天未使用已有的工作模式和目标状态组件。Claude 的计划入口分散在权限选择中，DSH 和 ZCode 原生目标接口没有接入。ZCode 原型只绑定一条聊天，新建入口被禁用。

## 决策

DesktopSessionsView 复用 WorkModes，适配器提供原生计划、目标和目标操作能力。Claude 通过既有 setPermissionMode 进入计划模式；DSH 使用已注册的原生 plan 命令和 goals 服务；ZCode 使用原 App V4 命令及结构化 goalSession。未确认 Claude 原生目标接口，因此不声明目标能力，不用提示词或 Bridge 自动续跑替代。

DSH 的 planMode 位于隔离的 planning 配置组，Bridge 不能直接读取全局属性。通过 commands.list 核验原生定义身份，再用 commands.execute 执行 /plan 或 /plan off，不向模型发送命令文本。该接口返回 commandId/result 包装，读取 result.kind 才能判断原生接纳。冷会话先展示历史，由“继续会话”显式调用 sessionController.resolveAgent 恢复原生 owner，并核对返回的会话 ID 后发现计划能力。create 只能复用空聊天，已有内容时可能新建另一条聊天，不能用于恢复。

共享发送请求携带 workMode，并沿用现有持久写回执。模式切换未确认时不发送正文；未知结果不重放。目标操作携带当前目标身份，DSH 额外核验 revision，ZCode 使用 V4 revision/logEpoch；清除目标使用原生状态版本，不能把 V4 revision 当作旧协议 stateRevision。

ZCode 在已接入的原生项目中创建聊天，复用共享项目选择器及 firstMessage 字段，不另设 message 字段。创建使用原 App 的 createSession/firstInput，首条消息和会话一次接纳，继承已核验会话的模型及思考深度。空 V4 草稿尚未落盘；实测可读但不在原生清单，提前改名及设定目标会失败。不能将取得草稿 ID 当作创建完成。聊天名称由原生首条输入生成，隐藏的名称字段不校验、不提交。

显式的新建模型选择见[共享创建模型预选](2026-11-04-shared-creation-model-selection.md)；上述继承行为保留为未传选择的旧请求兼容，不先发送默认消息再切换模型。

同项目多会话复用 App 已有 SessionDataLayer 的独立引用计数租约，不另建执行器。被动读取只读历史和已存在投影；明确继续会话才 acquire。退出和账号切换检查覆盖全部持有租约，包括尚未出现在持久清单的草稿；清单不完整、陌生会话无实时证据、runtime 变化时仍拒绝退出。

### 相关记录核对

- [原生会话原型](2026-10-31-zcode-native-session-prototype.md)和[共享能力](2026-10-31-zcode-shared-capabilities.md)：部分取代固定单会话范围，保留原 owner、账号、进程和未知回执约束。
- [手机原型](2026-10-31-zcode-mobile-test.md)：复用原页面和接入，不重做移动壳。
- [CLI 权限切换](2026-10-14-cli-permission-switching.md)：沿用 Claude 的原生权限接口，计划切换不提升其他权限。
- 原生归档、显示分类、通知及附件笔记属于独立功能，本次不改变其决定。

## 验证

ZCode 3.17.1 原生计划切换及回读通过，模型保持 Flash/low。createSession/firstInput 原生新建及标记回复通过，原生清单包含新聊天。原生目标设置、暂停、恢复、自然完成和清除通过。空草稿的改名失败、未落盘目标外键失败记录在本地临时证据中，未将其计为成功。

DSH 原生计划进出、目标设置、暂停、恢复、单轮自然完成与清除通过；冷恢复精确会话 ID 和无额外会话验证通过；未修改原 App 安装包。Claude 测试聊天的原生计划进入及恢复 default 权限通过，回读与持久回执一致。相关后端 71 项、共享聊天界面 167 项、补充表单及冷会话 2 项、ZCode 协议 16 项及 DSH 协议回归通过；firstMessage 修正后的 Python ZCode 8 项及共享请求至原生传输检查通过。

最终 Mac Test 为 2.1.7-test.20261101.zcode9，编译、签名、冻结资源及保留安装检查通过；584 个运行时源文件与构建快照一致，375 个 Web 文件与安装包一致。正式版 8787、原客户端进程及网关网络配置保留。HTTPS 页面实际创建原生聊天，收到 ZCODE_CREATE_HTTPS_20261101_B，局域网页面读取同一回复；普通、计划、目标三个选项均可用。测试推理前即时核验 Flash/low，未重复发送已通过消息。zcode7 的 HTTPS 表单测试暴露 firstMessage 契约不一致，失败发生在原生派发前；旧未知回执保留，新验收使用独立请求，不自动重放。

## 备选方案

- 继续为不同客户端维护独立模式面板，接入速度快，但重复目标状态、错误和幂等操作，违背共性功能上提约束。
- 用提示词模拟计划或目标，可覆盖没有接口的客户端，但无法保证原生状态、暂停和自动续跑，本次不采用。
- 空会话创建后再设目标或改名，便于沿用既有表单，但原生草稿没有完整持久化身份，真实操作失败，因此采用原生首条输入创建路径。

## 影响

目标能力与实际原生接口对应，手机和 Web 共用同一行为。新增 DSH 插件协议动作需要更新本地接入插件；旧 revision 9 仍可阅读和收发，不虚报新能力。ZCode 仍限 Mac Test 已绑定项目及已核验 App 版本；任意项目与跨平台 ZCode 接入不在本次验证范围。真实测试每次发起 ZCode 推理前核验 GLM-5.3-Flash/low，既有未变测试不重复执行。
