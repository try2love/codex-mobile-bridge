# Agent Note: 官方远控的三端本机控制通道

Status: implemented

## 问题

官方远控实验原先只开放 Mac，Python 客户端固定使用 Unix socket。直接把 Node 服务地址换成 Windows 命名管道，不能保留私有目录、用户/会话与拒绝远端访问的边界。三端界面必须表达真实可用性，不能因一个可选客户端初始化失败而停止整个网关。

Linux 成品检查进一步复现：登录钥匙串锁定时，同步 `safeStorage.isEncryptionAvailable()` 在 `app.ready` 之后阻塞，控制器尚未建立 IPC、主窗口也未创建；未配置官方登录的用户同样受影响。`status()` 重复同步探测还会让保存、断开和退出再次受阻，因此只调整窗口创建顺序不足以解决问题。

## 决策

macOS/Linux 继续使用原 Unix socket、0700 临时目录和 0600 发现记录。Windows 使用随网关打包的 Python `local-service-host`，由 Electron 通过匿名 stdio 持有；不安装额外软件，不开放 TCP，不复制官方账号或聊天业务。业务仍只由 `OfficialRemote.handle` 执行。

Windows holder 复用已有命名管道创建、对端 PID、用户、交互桌面会话与可取消 I/O。`local_listener` 只增加可选安全属性工厂，为每个新管道实例在创建时设置当前 SID 的 DACL，保留首实例和拒绝远端客户端标记。安全描述符在每次创建调用结束后释放，不能把已释放的地址留给后续实例。服务父子关系由原生进程信息核验；Python 客户端与 holder 必须同用户、同会话、同运行时镜像，服务 PID/创建时间必须匹配发现记录。不会为 SSH session 0 放宽原有交互桌面校验。

Windows 发现记录位于当前资料目录的 `.local-services/<name>/ipc.json`。目录和记录只允许当前用户，DACL 禁止继承；读取通过句柄核验所有者、DACL、重解析点和硬链接，并核对资料目录、服务名与本机管道地址。256-bit 随机令牌在派发前认证，沿用四字节长度帧、64 MiB 上限、16 个并发连接和既有等待预算。重建连接不重放请求；内部异常只返回安全提示。父进程关闭匿名 stdin 后 holder 正常退出，只移除仍属于自身令牌的记录，不影响原 Agent。

官方服务初始化不阻塞主窗口与核心 IPC 注册；官方本机服务就绪后在后台恢复已保存登录。未配置加密登录文件时，启动、状态、保存链接和退出均不探测钥匙串。状态只返回缓存的安全存储可用性及 `secureStoragePending`、`restoring`、`loginSaved`，不返回 IPC 地址或凭据。`configured` 仍表示实际恢复成功；已保存且正在恢复时，连接请求可等待同一恢复任务，不伪报登录或连接成功。

加密、解密和可用性检查使用当前打包 Electron 的异步 safeStorage API；同一原生检查的在途请求合并，本机存储等待12秒后给出可重试反馈，不改变网络和模型等待预算。世代、窗口引用和文件身份约束晚回调；忘记登录、关闭窗口、切换链接或外部文件变化后，旧结果不得重写凭据或连接旧目标。显式点击登录可重试安全存储，若旧密文尚在则先恢复它；已有有效登录时仍可正常进入账号登录流程。

Windows 凭据沿用 DPAPI，Mac 沿用钥匙串；同应用身份和安全存储未变时，异步实现兼容旧同步密文，恢复不主动重写密文。Linux 除检查 libsecret/KWallet 后端外，还要求密文为原生钥匙串的 `v11` 格式，拒绝 `v10` 固定密钥回退和未知格式。异步可用性为真不等于安全钥匙串已可用。既有加密文件保持原地，不跨机器复制登录数据；没有同步或明文回退。

## 备选方案

普通 Node `net.Server` 可以最少代码打开 Windows 命名管道，但当前公开 API 不提供自定义 DACL；对应 [libuv 创建实现](https://github.com/libuv/libuv/blob/v1.x/src/win/pipe.c) 使用默认安全描述符，也没有现有 Python helper 的拒绝远端标记。不能为减少代码改为 TCP 或依赖不可验证的 Node 私有句柄，因此选内部 holder。把官方 RPC 业务搬入 Python 会产生第二套客户端和回执规则，故只转发经过身份与令牌核验的请求。

Linux 允许明文 fallback 能减少首次配置失败，但不满足登录持久保存的保密要求；[Electron safeStorage 文档](https://www.electronjs.org/docs/latest/api/safe-storage)明确指出 `basic_text` 不提供可靠保护，因此保持失败关闭，不自动安装密钥环或修改系统安全设置。

把同步检查放进 Promise 或延迟到窗口创建后，仍会阻塞主线程；独立加密子进程又增加运行身份和协议。当前 Electron 44.5.1 已有异步接口，直接复用即可。其锁定的 Chromium 实现保留 Mac/Windows 的 `v10` 和 Linux 钥匙串的 `v11` 兼容参数，但同时提供 Linux 固定密钥 `v10` 回退，因此不能仅凭异步 API 返回可用就保存登录。[异步接口](https://github.com/electron/electron/blob/v44.5.1/docs/api/safe-storage.md)与[Linux 回退实现](https://github.com/chromium/chromium/blob/152.0.7977.130/components/os_crypt/async/browser/posix_key_provider.cc)为此边界的依据。

## 验证

- 锁定的 Ubuntu 登录钥匙串上，单次有界探针到达 `app.ready` 和 `before-sync` 后停在同步可用性调用，7秒后仅结束自有探针；没有加解密或输入凭据。用户手动解锁后原 Test 的状态接口恢复，验证了实际阻塞点。
- 22个控制器场景覆盖未配置零探测、在途合并、超时、拒绝 Linux 弱密文、旧格式兼容、迟到恢复/保存拒绝、显式登录恢复及启动途中关闭时清理迟到监听；核心窗口不等待可选服务及界面显式重试分别有定向检查。模拟密文用例不代替真实用户密文恢复，成品复核单独记录。

- macOS：共享帧与损坏记录检查通过；Node holder 转发、异常脱敏和启动失败隔离测试通过；既有 Unix socket 认证测试在允许本机 socket 后通过。Linux 存储后端枚举的定向测试拒绝 `basic_text` 和 `unknown`。
- Windows 实机：原生私有 ACL/硬链接拒绝与命名管道 PID/字节流检查通过；SSH session 0 按原边界拒绝身份接入。临时、非提权的交互计划任务进一步通过 Node → holder → Python → Node 正向链路，错误脱敏、两次处理、父进程继续运行与发现记录移除均确认；任务随后注销。没有退出原 App 或发送模型消息。
- Ubuntu 实机：复用已安装 Test 的 Electron Node 模式运行 Unix RPC，帧交换、错误脱敏、两次处理、父进程保留和发现记录清理通过。系统没有独立 Node 安装，本次未安装。真实安全存储探针已找到同用户图形环境，但临时运行时以 exit 1 结束，未取得 backend 结果；不能据此认定密钥环不可用或未解锁，停止该探针路径。
- 最终 Test 128.13：Ubuntu 完整成品在同用户图形会话、独立空配置中正常启动；对该进程的私有 socket 读取真实控制器状态，`available=true`、`secureStorageAvailable=true`，未登录且未配对。此结果通过真实系统安全存储后端的允许列表检查，不再将旧临时运行时探针失败当成阻塞；不代表已验证官方凭据加密后的跨进程恢复。
- 最终 Test 128.13：Windows 完整成品在交互 session 1 启动，内部 holder 与父进程记录匹配；同版本打包网关的 `management-stream` 经命名管道取得实际官方状态，返回“请登录与 ZCode 原 App 相同的官方账号”。这是空账号启动安全存储门禁通过的证据，尚未验证真实账号的加密保存与恢复。两端使用独立资料、关闭自动启动及全部 Agent 接入，保留现有任务，不发送模型请求。
- Windows Test 128.15 已在用户完成本机官方登录后，以同一应用身份/资料目录正常更新重开，自动恢复真实官方配对和既有测试会话的发送权限，原ZCode进程保持不变；未再次登录、未发送模型消息。具体步骤和耗时见[首次连接引导与验证](../feature/2026-11-04-zcode-connection-choice-and-api.md#首次连接引导)。Linux 的官方登录、真实配对与官方凭据持久恢复仍须用户在该电脑完成验证，不能使用 Mac/Windows 密文或模拟账号代替。无 ZCode descriptor 的旧 128.12 临时 `clients` 探针未覆盖官方 RPC，不能作为成品通过或产品故障的依据。

## 影响

Windows 增加一个由网关拥有的内部 holder，空闲时阻塞等待 stdio/管道，不新增周期轮询。换取与 Unix 路径等价的用户隔离和明确的进程归属，原 App 不承担该服务的生命周期。构建需包含新 holder、Windows helper 和 Node 分发模块；CLI 和主进程共享启动契约，不能要求用户安装 Node、Python 或 Homebrew。

本地可重用夹具在 `.tmp/preview3-local-rpc-probe.cjs`、`.tmp/preview3-storage-probe.cjs` 和 `.tmp/preview3-linux-native-probe.py`，不提交脚本中的机器路径、运行结果或截图。Windows/Ubuntu 的测试资料与实际登录、活跃 App 路径分开，临时 runtime 副本已清理。

## 既有记录审计

- [官方远控实验](../feature/2026-11-04-zcode-official-remote-experiment.md)：部分重叠；保留同账号、设备归属与官方协议边界，本篇负责本机 IPC 与平台安全存储。
- [连接选择与 API](../feature/2026-11-04-zcode-connection-choice-and-api.md)：部分重叠；互斥方式、持久配置与任务保护继续沿用。
- [原生私有启动器打包](2026-11-03-zcode-private-launcher-packaging.md)：服务对象不同；原 Agent 管道 custodian 不由本篇替代，不合并或移除。
