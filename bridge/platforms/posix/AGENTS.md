# POSIX 共用实现维护

- 本目录服务 macOS/Linux 的进程启动与信号、Unix socket、连续 PTY。不要按两个系统复制相同逻辑。
- 不得反向导入 `bridge.clients` 或 `bridge.features`。原生实现不决定账号、会话授权、客户端启用或重启；调用方负责路径/PID 归属及任务状态检查。
- `desktop.py` 只执行调用方选定的原生操作，保留环境变量、工作目录和进程组行为；不要扩大信号发送范围。
- `transport.py` 保持字节流语义，IPC 消息格式与目标 owner 由客户端层处理。
- `terminal.py` 的部分方法也被 Windows ConPTY 实现复用。输入编号、输出游标、尺寸、取消与关闭语义属于共同契约，修改时核对 Windows 调用方。
- 该终端源码还会通过 SSH 发送到远端 Python；远端未安装完整网关包。新增导入、拆文件或改资源位置时，核对源码装载及 `scripts/gateway-resources.json`，不能只验证本地 import。
- SSH 执行环境与网关本机系统不同，不得根据本机 Windows/macOS 标志选择远端 shell 或进程行为。
- 验证应覆盖受影响的本地 PTY、远程装载及 Windows 公共方法依赖；明确区分源码验证、打包资源验证与真实终端验证。
