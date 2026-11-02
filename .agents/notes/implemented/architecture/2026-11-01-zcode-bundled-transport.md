# Agent Note: ZCode 传输复用网关运行时

Status: implemented

## 问题

ZCode 原型通过外部 Node 执行 CDP 传输。这是 Bridge 原型引入的依赖，不能要求用户额外安装 Homebrew 或 Node；ZCode 自己的内部运行组件仍由 ZCode 管理。

## 决策

ZCode 的 loopback 发现与 CDP 请求使用网关已有 Python 运行时。WebSocket 帧处理从 Codex 传输提取到 `bridge/clients/websocket.py`，保留原 Codex 错误类型和调用契约。ZCode 的 renderer 脚本仍在原 App 中执行，沿用原 owner、会话、账号、工作区与未知回执处理。

不新增运行依赖、不捆绑 ZCode 的 Node、不修改其安装包。仅接受已核验 renderer 的 127.0.0.1 端点，禁止重定向和代理；模块注册表必须唯一匹配，CDP 对象组在结束时释放。请求保持原有有界超时。

## 备选方案

捆绑另一份 Node 也能消除 Homebrew 要求，但增加体积、更新与签名维护。继续使用系统 Node 最省原型改动，却将开发机依赖转嫁给用户。两者均不如复用网关已有运行时。

## 影响

删除独立 `transport.mjs` 和资源项，保留原生 renderer 协议。共享 WebSocket、ZCode 适配器及 CDP 约束测试通过；本机真实原会话在配置的 Node 路径故意不存在时仍读取到 37 条消息，未发起模型请求。Windows/Linux 原生适配不由此推定完成。

## 相关记录审计

[ZCode 协议原型](../feature/2026-10-31-zcode-native-session-prototype.md) 部分重叠，本文仅替代外部 Node 传输决定，原 App 身份、授权与文件边界不变；共享能力与项目功能的原有记录不被替换。
