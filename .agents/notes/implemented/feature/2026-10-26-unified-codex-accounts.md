# Agent Note: 统一本地 Codex 账号

Status: implemented

## 问题

Desktop 与 CLI 已共享保存的 Codex 账号，但两份列表和可选验证复选框让人误以为身份独立。

## 决策

UI 显示一份保存清单，并在提醒前放置独立 API 编辑器。Desktop 切换后始终验证 CLI 配置。Desktop 停止时使用既有受保护 CLI 事务，不启动 Desktop。官方登录仍仅在电脑完成；Claude 和远程环境保持当前范围。

## 备选方案

保留两份列表可以保留既有控件，却错误呈现共享账号主目录。隐藏 CLI 列表而不保留其 API 编辑器和模型探测，会丢失有用能力。

## 影响

Desktop 和 CLI 可能报告不同成功结果，需保留部分成功反馈。[此前可选同步决定](../../implemented/feature/2026-10-14-local-cli-account-sync.md)部分重叠，作为历史理由保留。活跃账号同步和 WSL 笔记在本项狭窄的本地 Codex 决定之外仍适用。

定向账号 UI、CLI 编辑器、WSL 作用域及后端账号/同步/接入夹具验证该行为，不进行个人账号切换或模型请求。

[账号展示与平台范围修正](../bug-fix/2026-10-26-account-ui-and-platform-scope.md)替代桌面编辑器放置方式，并在不支持的网关跳过 WSL 初始化；账号清单和端点身份契约保留。

[受管 CLI 账号同步](../bug-fix/2026-11-10-managed-cli-auth-sync.md)补充严格同 provider API 条件下的活服务认证缓存同步和未知回执核验；单一账号清单与其他维护路径保持。
