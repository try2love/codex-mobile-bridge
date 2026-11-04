# Agent Note: 新建会话在首次输入前确认模型与思考强度

Status: implemented

## 问题

Issue #131 要求在四种 Agent 的新建会话中选择模型和思考强度。Claude 和 ZCode 的原生创建会直接发送首条消息，创建完成后才切模型会先使用默认接入产生一次推理。只读取账号管理中的全局模型列表也不能证明所选项目的配置、CLI 原进程环境或独立 Codex 配置目录相同。

## 决策

Web 与手机的新建弹窗使用同一 `CreationModelPicker`，复用现有模型目录格式、能力字段和 `ModelStore`。已确认的原生当前模型或唯一 `isDefault` 可以作为初值；没有默认依据时要求用户选择，不按目录顺序选择第一项。思考强度只显示原生提供的档位，保留原生手动输入能力；明确 `supportsEffort: false` 的模型不显示手动强度入口。目录刷新保存到原有同作用域缓存；晚到响应不能覆盖另一项目或环境。

新建请求携带 `model`、`effort` 和 `modelCatalogIdentity`。实际创建前重新核对账号/环境及目录身份，拒绝已失效选择；旧请求没有这些字段时保留原客户端行为。配置或账号变化不会自动改选新的默认模型，需要用户重新选择。

### 各原生创建入口

- Codex App、CLI、SSH 和 WSL 的 `thread/start` 同时传递 `model` 与 `config.model_reasoning_effort`，核验创建回执，不调用 `turn/start`。独立 Codex 的空项目使用当前接入 ID 和自己的数据目录，不路由到默认 Codex 账号。
- Claude Desktop Code/Cowork 的同一次原生 `start` 携带模型、强度及首条消息。本机已安装 App 的 `startSession` 源码确认消费这些字段；不修改安装包。
- Claude CLI、SSH 与 WSL 复用 `creation_launch_options`，通过原生 `--model`、`--effort` 设置本次进程，初始化后只读核验 `get_settings`，然后才写第一条 `user`。设置只作用于本次子进程，不改账号或项目配置。默认强度按原生 `auto` 语义处理。
- ZCode 的同一个 `createSession` 同时设置 `config` 和 `firstInput.modelSelection`，不先按默认模型发送。
- DSH 先创建不含消息的空会话，再通过原生 `selectModel` 回读确认，之后才返回可发送状态。新能力由 `createModels` 明确声明；旧插件拒绝新模型创建参数时返回已知未派发状态，不能静默忽略或留下未知首条发送回执。插件主源与 revision 由本轮整合统一同步。

### 缓存、配置与回执

Codex Desktop 的新建目录按目标 cwd 读取，不借用全局账号目录代替项目配置。共享 `Catalog.model_identity` 纳入 cwd 及父级候选 `.codex/config.toml` 摘要，项目层覆盖改变会使旧目录失效；摘要不包含可公开还原的配置或凭据。CLI/SSH 复用原 daemon 的有效配置、账号和执行环境证明及 `native_model_store`。WSL 复用现有 `WslModelCatalog`；没有线程的新建预览不添加线程巡检定时器。

Claude Desktop 按 Code/Cowork 过滤同账号缓存。Claude CLI 优先复用同账号、主机、目录的模型缓存；没有已接入会话且没有可复用目录时允许明确手填，不为探测目录持有新的 CLI 会话。此时手动刷新不能伪称取得了新目录，保留缓存和时间并说明限制。本机目录选择不会额外访问不相关 SSH 主机。

缓存复用有明确边界：Codex CLI/独立配置/SSH/WSL 在完整身份一致时与已有聊天共用后端条目；Claude CLI 沿同作用域聊天目录。Claude Desktop、DSH、ZCode 的新建复用账号管理目录，其聊天设置目录仍有独立命名空间。Codex Desktop 新建使用项目 cwd 和默认 provider 入口；即使与某条聊天实际同为 OpenAI，`None` 与显式 `openai` 仍可能形成不同键，尚未统一。前端创建与聊天设置也保留不同作用域条目。因此不能将本轮描述为所有页面即时共用一个缓存；刷新更新的是所选作用域缓存。

项目、正文、模型和强度属于创建意图。`modelCatalogIdentity` 与环境版本属于执行前置条件：刷新它们不能将未知创建变成新请求 ID，也不能绕过已有未知回执重发首条消息。明确未派发的阻止结果允许用户修正后显式重试；已有创建回执按原 ID 返回。

## 备选方案

先按默认模型创建并发送，再切换设置，容易复用原按钮，但无法避免第一次收费请求，故不采用。DSH 的空创建没有消息，属于可在首条前完成配置的原生路径。

Claude CLI 在初始化后调用既有 `set_model` 看似可直接复用聊天设置，但本次隔离原生验证未得到成功确认。采用官方原生启动参数并只读核验，既不需要第一条消息建立运行状态，也不会为验证设置而调用模型。

使用全局账号模型缓存可以减少读取，但会绕过项目覆盖和 CLI 原进程环境；每个 Agent 独立建立新缓存则会重复刷新、分叉身份规则。因此复用已有按作用域保存的目录，允许手动刷新，不能证明作用域时不跨账号复用。

## 验证

2026-11-04 的定向验证覆盖创建参数、模型/强度能力校验、项目配置变更失效、同作用域缓存、跨主机隔离、独立 Codex 空项目、配置刷新与未知请求幂等、WSL/SSH 参数转发及只读 HTTP 目录边界。原先目录/资源批次的 5 个错误来自测试替身未接收 `host_id`，1 个失败来自新增远端源未列入测试清单；修正后相关 14 项通过。原生目录抽取后 7 项通过；独立目录测试的符号路径比较修正后单项通过。没有重跑已通过且源未变的真实推理测试。

安装的 Claude CLI 2.1.292 在独立临时配置与 loopback 假 API 中验证：首条发送前读回指定的 `claude-sonnet-4-5-20250929`，回显只收到一次首条请求，模型精确匹配，真实模型请求为零。该模型/版本组合对 `--effort low` 返回 `effort: null`，因此该请求在首条前被阻止；不能将原生不支持的强度宣称为有效。支持强度的参数契约另由受控夹具验证。

生产 Web 资源在 Chromium 390/1280 两个视口均通过四 Agent 新建表单、默认选择、刷新、标签、无横向溢出和创建字段检查。实际检查窄屏 Codex/ZCode、宽屏 Claude PNG；完整证据和 8 张截图在 `.tmp/preview3-creation-browser/`，无真实 Agent 写入。CLI 回显脚本为 `tests/acceptance/claude_create_models_native.py`。定向结果摘要在 `.tmp/preview3-creation-evidence.json`。

## 影响

首条输入与用户选择保持同一次原生创建语义，避免隐式默认模型消费。缓存验证增加配置摘要读取；只读取候选配置文件，不增加全量目录扫描或额外 CLI 会话。需要目录确认或账号发生变化时，用户可能需要刷新并重新选择。

本记录不把原生参数/回显夹具等同于真实付费模型响应。Windows/Linux 使用相同创建与界面逻辑，三端构建和实际平台收尾由本轮整合另行核验；本文不宣称子 Agent 已完成三端真实模型收发。

## 既有记录核对

[原生工作模式与新建聊天](2026-11-01-native-work-modes-and-zcode-create.md) 部分重叠：保留原生 firstInput、租约和旧请求继承默认的依据，本篇补充显式模型选择。[Codex 项目信任](2026-10-29-codex-project-trust.md) 与 [Claude Code 会话](2026-10-14-claude-code-sessions.md) 分别保留目录信任、原账号/cwd 和写回执规则；本次不替换其生命周期决定。其他命中的共享外壳、原生元数据和通知记录为相关独立职责，没有完全吸收或需要删除的提案。
