# Agent Note: 当前 Agent 按可信配置管理 CLI 释放

Status: implemented

## 问题

连接管理把不同 Agent、主机与配置的连接和每次历史回执平铺在一起。重复回执会淹没当前连接，旧未知结果的只读检查还可能让最新释放状态看起来倒退。用户需要明确选择一条连接或当前配置全部连接，并在共享服务退出前看到实际影响范围。

## 决策

设置和会话入口共用 `CliConnectionsPanel`，捕获当前 Agent、独立实例及认证身份。列表按本机、SSH、WSL 分类，再按已有可信配置字段分组；会话入口另固定原 session/host。`scopeKey` / `scopeLabel` / `sharedHome` 表示共享原生服务范围，`configurationKey` / `configurationLabel` 仅表示可信的独占进程配置。未知配置逐连接独立展示，不能凭可见 location、路径或家族名推测相同范围。

每条连接只呈现最后一次控制操作的状态。顺序使用后端操作账本的插入顺序，连接 token 分代；`check` 是观察，不成为新的控制操作，也不按 updatedAt 排序。旧操作的晚到检查不能覆盖新控制或新连接，待核验状态保留检查入口。历史回执默认折叠、展开时生成，完整保留服务端返回的旧操作、错误和未知结果。

共享 home 的单条和全部释放均先调用已有 `scopePreview`。确认展示返回的真实 scopeLabel、全部 impact writer，包括未被选择的连接；确认提交原 preview 的具体 keys 与 generation。普通释放使用 `scopeWait`，明确强制使用新的 `scopeForce` 请求 ID。只有后端 canCancel 允许的 scopeWait 才显示取消，取消成功前仍保留原状态。新的明确强制是独立意图，旧未知操作不重发。

独占进程配置的全部释放复用既有 `wait` exactKeys，逐条退出；单条强制复用 `forcePreview` / `force`，不冒充共享 daemon scope。未知行只提供 `check`；已退订行保留真实原生占用维护出口。界面明确说明逐条等待暂不支持取消。关闭和返回始终可用，只影响界面；请求期间禁用重复派发，迟到结果不进入已切换 Agent、认证或关闭后的面板。

## 备选方案

按 location 或配置目录文本分组能覆盖更多旧行，但这些文本没有用户和目录身份保证，会把不同账号或主机合并，因而未知配置保持独立。让所有 Agent 使用 scopeWait 可以统一按钮，却会把 Claude 的独占 stdio 进程误当共享服务，故只复用已有 exactKeys 批量入口。按回执更新时间展示最新状态容易实现，但旧 unknown 的 check 会压过新控制；因此以控制操作插入次序与连接 token 决定当前状态，检查只提供观察。

## 验证

`tests/cli-release-ui.test.cjs` 定向覆盖 Agent/实例/主机/配置隔离、未知配置、折叠全部历史、完整 impact、预览代次与确切 keys、等待取消失败与成功、独占配置批量和单条强制、请求期间返回关闭、身份漂移、旧 check 不覆盖新控制或新 token。`tests/session-management-ui.test.cjs` 的增量用例调用实际 shell 入口，核对当前 Agent、原 session 和 host 的传递。

浏览器夹具使用生产共享面板、真实 DOM 点击及现有协议形状的合成回执，覆盖 320 / 390 / 1280 像素中英文，核对共享范围额外 writer、等待取消、明确强制、pending 关闭及 Claude 精确配置批量。所有请求止于夹具，不运行已安装 CLI、正式 App、固定样本或模型，不将这些结果称为真实原生释放验收。

## 影响

当前 Agent 的本机与远端连接、不同配置和旧回执不再混在同一“全部释放”动作中；用户能看到原生共享范围并明确等待或强制。未知配置仍需逐条处理，逐条普通等待的取消能力受既有后端合同限制。接口与持久业务状态机保持既有实现，面板不增加扫描、账号迁移或旧写入重放。

## 相关记录审计

- [主动释放 CLI 连接](2026-11-01-cli-connection-release.md)：部分重叠，沿用共享入口、精确连接世代和未知不重放，更新展示与确认行为。
- [按本机配置释放原生写入者](2026-11-11-cli-scope-writer-release.md)：部分重叠，消费其既有预览、范围等待/强制/取消合同，不改后端维护账本。
- [Mac Claude 自有 CLI 退出](2026-11-11-mac-claude-owned-cli-release.md)：部分重叠，只消费可信配置展示字段与原逐条控制；配置分组不等同 daemon 控制范围。
- [SSH 与 WSL 生命周期](2026-11-11-codex-remote-cli-lifecycle.md)：部分重叠，展示目标端可信范围，不把本机账号恢复用于远端。
- 既有 TCC 提案、扫描文案、路径示例和手机绑定与本点无关，保持原样；上述后端记录由各自作者维护，本点另记 UI 决定。
