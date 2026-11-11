# Agent Note: 原生 CLI 显式接续前恢复失去响应的代理连接

Status: implemented

## 问题

网关保留的 app-server proxy 进程可能仍存活但不再回答读取。现有 connect 只检查进程与 initialize 结果，因此用户再次点击继续仍使用同一连接，thread/loaded/list 超时后没有恢复路径。

Windows Test 8788 的固定 SSH 样本在继续时返回 409，等待 15044ms；同时独立新代理可在 16ms 读取同一原生 0.160 会话的完整三回合。这说明新连接可以读取目标，但尚未定位旧网关卡住的具体 RPC，不能据此宣称现场根因已经确认。

## 决策

只在显式接续、尚未发送 thread/resume 的首次 loaded 检查发生 AppServerReadTimeout 时，替换一次当前代理。关闭按进程代次核对；旧订阅清理与新 connect 由已有 connect_lock 串行。重连仅调用 client.connect，不走启动 daemon 的 fallback。新 initialize 必须再次匹配既定 CODEX_HOME，然后重新读取 loaded 和原生会话身份，最后允许发送一次 resume。

底层读取、被动观察、账号空闲核验、模型目录不透明换连接。写入等待失败仍为 AppServerUnknownResult，不重放；第二次 loaded 超时结束当前接续。关闭的是网关自己的字节代理，不是共享原生服务或原任务。

## 备选方案

在 loaded_threads 底层自动重连可以复用于多个读取入口，但 observe 和原生操作已经绑定了旧进程身份；透明换代会绕过新 home 核验，并让旧回调误清新订阅，因此仅在显式接续前处理。

重启共享 daemon 可能恢复整个服务，但独立读取已成功，且会影响其他原生任务；这里没有依据或必要重启它。延长每次读取超时不能修复保留的失效代理。

## 验证

真实 WebSocket/管道夹具在原 HEAD 的 attach 方法上稳定复现 loaded 超时；当前实现覆盖替换代理后只 resume 一次、不同 home 在 resume 前拒绝、第二次超时不再重连、不启动 daemon，以及并发新 join 等待旧订阅清理。底层额外验证只读超时不自行重连，原有写入未知不重放测试保留。上述共 11 项通过。固定 SSH 样本的新程序现场验收尚待进行；夹具不是原生服务实测结论。

## 影响

正常连接不增加读取。异常接续最多增加一次代理连接及 loaded 检查；原有读取预算没有缩短。本地集成，不改账号、provider、原生历史和会话 ID。
