# Agent Note: 云响应发送完毕后等待手机关闭通道

Status: implemented

## Problem

用户报告退出云服务重新登录后聊天列表为空并显示网关连接中断。手机新令牌登记、端到端握手和桌面本地认证成功，桌面也收到 `/api/sessions`，但此前仅分层测试未覆盖 Python 桌面到 Rust 手机的完整结束时序。Go 中继 + 生产桌面 Connector/Channel + 实际 Rust DLL/Dart Repository 的合成链路复现首次列表读取失败。把桌面在发送 response_end 后立即退出改为等待手机关闭，同一链路连续四轮退出重登、32 次列表读取通过，证明不是单纯登录凭据失效。

## Decision

桌面 `forward_http` 发送 response_end 后调用 `wait_response_close`，不立即由 worker finally 发送外层 Close；最多等待五秒，消费仍在途的合法 credit，正常由手机关闭 WebSocket 触发取消并释放通道。累计尾部 credit 不超过窗口大小四，非法消息直接拒绝；超时释放、不新增请求重放或扩大并发队列。Response_end 只发送一次，访问关闭/网关停用仍即时取消。

正式回归覆盖正文成功结束仍保持通道、合法尾部 credit、非法输入、超时和取消；完整合成链路使用生产手机 Repository/Rust DLL、Go API/Hub、桌面 Connector/Channel 与合成本地 HTTP 响应，验证多轮重新登录的身份保持和只读请求。当前桌面 Python 网关已在核验 PID、实例令牌和源码/数据目录后，通过已有管理入口加载修复，不结束编码客户端或聊天任务。

## 记录归属

检索当前笔记，没有既有 response_end/credit 收尾决定。[云监听器组装](../architecture/2026-11-02-cloud-listener-injection.md)部分重叠，持有独立 HTTP 认证与共享业务服务；[自动设备目录](../feature/2026-11-03-account-device-auto-discovery.md)持有重新登记不丢密钥及授权。手机响应接收修复与服务端关闭隔离各自保留，不以它们的分层测试替代三端联调。

## Alternatives considered

- 发完 response_end 立即外层 Close 最省通道占用，但双向流量控制的在途 credit 仍可能令手机读写任务提前结束；让响应接收方关闭提供明确时序。
- 引入新的 response_ack 消息最明确，但要同时修改手机/桌面协议并发布；现有手机已在接收完成时主动关闭，因此复用它并设置有界等待。
- 固定睡眠可暂缓竞态，但既不能确认接收完成又平白增加延迟；等待现有关闭事件并限制时间。

## Testing

旧实现三端链路在已完成认证、桌面收到 `/api/sessions` 后失败，诊断记录网关仍 connected；新实现同链路四轮退出重登、32 次列表读取和36次认证通过。正式 `test_forwarded_http_response_does_not_finish_before_receiver_close` 在旧函数基线上也复现过早结束，在新函数上通过；取消、非法/超额 credit、总期限与空闲超时分别覆盖。81 项云端 Python 测试、5 项资源测试和模块边界检查通过，无新增依赖例外。

本机真实网关源码 SHA256 为 `876c8bd8f2e31b6948d51a00219c8bdb4904213cf70840bb0cd7bfbf4dd5164f`。2026-11-04 18:29（UTC+8）独立后台进程 34156 加载此源码，云端实际查询确认 gateway online；账号、可信公钥和绑定文件哈希保持一致。手机保持原已登录账号，通过 ADB 读取当前屏幕并执行只读返回/选电脑/刷新，确认真实聊天列表恢复、红色错误消失；连续三次刷新后远端会话也显示，未发送聊天或执行真实退出/登录。

一次初始重载使用 FastCtx 任务派生后台网关，任务结束后的进程清理使该网关退出，而状态文件仍是 connected。随即通过 PID 存活、云端 online 和手机画面发现不一致，改为 PowerShell 隐藏独立进程启动，核对父启动器退出后网关仍存活；没有把状态文件当作当前在线证据。此临时部署问题与原产品响应收尾竞态分别记录。

联调使用本机 PostgreSQL 独立 schema、随机回环端口和合成凭据。桌面生产 origin 限制仍是 HTTPS；仅隔离夹具替换精确回环 origin 校验，未改生产配置或关闭真实 TLS。夹具前两轮因 Dart 测试 HTTP 拦截、chunked 控制请求和遗漏的夹具 origin 校验失败，不计作产品失败或通过。有效旧链路失败保存在 `ffi-repro5.log`，修复后在 `fixed-ffi.log`。完整材料与截图位于 `D:/github/.codex-tools/cloud-relogin-20261104/`。

## Consequences

响应发送方不再抢先销毁仍在收尾的双向通道，重新登录后的认证与列表读取可以稳定完成；代价是不关闭的客户端最多占用一个通道五秒，仍受总通道限制。超时或非法消息不会自动重试业务。桌面网关重载期间中继断开，随后自动恢复；手机未重装、账号/本地数据/数据库/绑定未重置，编码客户端未重启。真实手机只读列表恢复不等同于所有会话写入、附件与推送均已实测。
