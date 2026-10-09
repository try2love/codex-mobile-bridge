# Preview 3 Windows 源码测试指引

在 Windows x64 开发机上，`start-preview3-windows.cmd` 可从仓库内的 `dist/desktop/win-unpacked` 启动已构建的桌面 App。脚本为测试实例使用独立的 `.local/windows-preview3-user` 数据目录，并将启动回执写入 `.local/windows-preview3-launch`。该脚本依赖当前用户的 Explorer 会话；无法核验父进程、会话与目标程序时会报错。它不会构建或安装应用。

合并后的客户端扫描读取安装注册信息、App Paths、当前桌面会话和用户指定路径。CLI 运行时与 GUI 路径分别核验。Claude 的配置目录优先采用所选运行进程的证据；停止状态只在目录证据足够时选择，无法判定账号归属时拒绝切换。扫描、运行和连接是不同状态。

Windows Claude 的开启先尝试恢复已有签名连接，再按经核验的原生入口尝试后台启动及初始化。`--startup` 和开发者工具入口只在所选安装版本的源码形态符合已验证条件时使用；未知版本返回可重试状态。显式“初始化连接”允许在确定后台助手尚未提交输入、桌面可交互时使用前台引导。取消只停止本次助手。连接状态必须以当前 generation 的签名回执为准。

停用接入、正常退出和后台强制结束是三个独立选择。正常退出需核对已知任务与所选应用进程；Claude 和 Codex 使用原生菜单，DSH 使用已核验的原生退出通道。Windows 后台强制结束只有本次显式选择 `enabled: false, quitDesktop: true, forceDesktop: true` 才会请求，服务端重新核对网关状态、程序路径、会话、PID、创建时间及进程句柄。任何退出方式都只有在所选目标进程实际消失后才保存停用状态；失败或回执不确定时不得自动重试或改用强制结束。

网页和手机切换 Claude/DSH 时可先显示当前页面内存中的列表及详情，再刷新。缓存不授予发送、停止或审批权限；写操作、账号切换、停用与退出登录会作废相关读取。启动和退出的页面超时表示结果待确认，用户须刷新状态后再操作。

源码层回归入口：

```powershell
python -B scripts/test-backend.py --source-only
npm.cmd run test:desktop
```

`--source-only` 跳过 Swift、C# 和 Electron 原生执行夹具。需要在专门的隔离 Windows 测试环境里验证原生助手、打包和锁屏行为；源码测试不代表这些结果。来源分支 `codex/preview3-windows` 曾报告所选 Windows 测试机上验证了 DSH 退出、Claude 后台提交与签名连接、Codex 原生退出及部分锁屏场景；这些记录属于来源分支，不能当作本次目录整合后的真机验收。具体版本与环境应在本次整合代码重新测试时记录。
