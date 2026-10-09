# Preview 3 Windows 本地测试

此工作树基于 `feature/preview3-client-autoconnect` 的 `d56cb0c`，本地修复分支为 `codex/preview3-windows`。

## 启动与手机测试

双击项目根目录的 `start-preview3-windows.cmd`。它启动 `dist/desktop/win-unpacked` 中的已编译 App，并使用独立的 `.local/windows-preview3-user` 配置目录。

启动器通过当前用户已经运行的 Windows 资源管理器创建独立进程，核对实际程序、启动时间和父进程后才报告成功。它不会依附于发起命令的 Codex 工具会话；仅使用隐藏窗口或新进程组标志并不能保证这一点。资源管理器不可用或无法核验时会明确报错，不退回普通子进程启动。启动回执保存在 `.local/windows-preview3-launch/latest.json`。请勿直接从 Codex 终端启动成品 EXE 来代替此启动器。

本机已经准备好端口 **8788** 的测试配置：手机与电脑在同一网络时，打开网关面板显示的局域网地址。当前地址为 `http://10.21.154.171:8788/`；本机网页访问遵循现有设置，关闭时 `127.0.0.1:8788` 返回 403 属正常行为。展开面板里的二维码扫码登录，也可在面板查看首次登录凭据。原有 8787 网关使用原配置继续运行。

新实例连接电脑现有的 Codex 数据目录。请在手机选择已保存的项目，新建一个测试聊天；该聊天会真实出现在 Codex 中。测试完成可从新网关的托盘选择“停止网关并退出”。

运行配置中的 **Codex 程序路径留空**可自动发现，并在 Codex 更新移除旧运行时后重新定位。手动填写的路径会被尊重；路径失效时应清空或改成当前的 `codex.exe`。CLI 运行时与桌面 GUI 可执行文件是不同配置。

旧版已记录为“创建结果尚不确定”的请求不会自动重放，以免重复创建。更新后先检查已有会话列表，再新建一次测试聊天。

## Windows 客户端扫描修复

客户端页的“重新扫描”现在同时读取当前用户的 Store 包注册信息、安装注册表、App Paths 和当前会话的桌面进程。它不再依赖遍历受保护的 WindowsApps 目录，也支持安装到其他盘符的 `DSH Desktop`。Store 包优先使用清单中声明的桌面入口，避免把 Codex 的启动垫片或命令行运行时当作 GUI。

Claude 优先根据所选应用进程的 `--user-data-dir` 定位活动配置；应用未运行时，检查对应 Store 包目录、`Local/Claude-3p`、`Local/Claude-Data` 和传统 Roaming 目录的配置证据。手动指定的数据目录保持优先。账号模块沿用这些目录；无法确定配置归属时拒绝切换账号，避免向错误目录写入。

扫描只发现应用和配置，不会自动完成 Claude 或 DSH 的连接、修改原生开发者设置或切换账号。“已安装”“正在运行”和“已连接”是不同状态。更新本地测试版后重新打开启动脚本，客户端页会自动重新扫描，也可以手动点击“重新扫描”。

Codex 详情中的“扫描桌面程序”也使用 Windows 原生发现结果，可定位 Store 安装的桌面入口，即使 CLI 位于独立更新目录。

Windows 网页或手机端普通“开启”会启动或复用 Claude，优先恢复已有连接；未恢复时自动尝试后台初始化。当前 Store 版本支持原生 `--startup` 隐藏主窗口启动，并在已开启开发者模式时，通过应用自己的 `CLAUDE_DEV_TOOLS` 入口创建开发者工具。此入口仍受 Claude 原生限制控制，不修改安装包或开发者设置。Claude 可能短暂激活调试窗口；脚本提交不要求用户保持前台焦点。未知版本保留明确失败与重试入口。等待期间显示正在启动或连接，只有真实签名心跳通过才显示已连接。

Claude 已在托盘运行且没有开发者工具时，网关通过已核验的原生应用入口恢复同一个进程的窗口内容，再打开开发者工具并初始化；直接显示隐藏的 Windows 窗口不足以恢复 Claude 菜单。运行超过 30 秒的已有进程跳过冷启动等待，新进程保留 8 秒宽限。原生恢复可能短暂显示主窗口或调试窗口，但不依赖前台键盘输入。初始化结束、取消或超时后，仅在原主窗口身份不变、没有新用户输入或原生确认、主窗口不在前台时恢复原来的隐藏状态；无法确认时保留窗口。

手机“应用管理”和会话页的“初始化连接”在 Windows 上也优先走后台路径：通过原生辅助接口选择 Console，一次写入并完整读回脚本，再向准确的开发者工具窗口提交，不使用全局键盘输入。仅在确定尚未提交、用户显式初始化且桌面可交互时，才允许原有前台引导回退。普通开启不执行该回退。提交回执丢失时先等待实际连接，避免自动重复输入。原生登录、目录信任和开发者设置确认仍由用户处理。

DSH 开启时自动启动并接入。应用管理和会话页会持续显示正在启动或等待 Harness 连接的原因；等待期间每 2 秒刷新状态，连接后恢复通常的刷新间隔。Windows 等待 120 秒（其他平台 60 秒）仍未连接时显示超时与重试入口，后台继续观察到连接后自动恢复。等待期间保留聊天缓存，停止无效的聊天列表和详情请求；成功连接后清除过时的重启提示。账号或 API 尚未配置的提示与连接进度分别显示。

手动连接时，若 Claude 在系统托盘、没有可见窗口，助手通过所选 Claude 的原生启动入口恢复窗口。窗口识别限定当前 Windows 会话、进程和完整程序路径。连接脚本一次性写入，并在完整读回核对后提交，已移除逐字键盘输入；重试会复用已有开发者工具，保留无关草稿。当前 Windows 版的开发者菜单路径为 `Menu → Help → Troubleshooting → Enable Developer Mode`；助手保留 Claude 自己的确认弹窗；当前尝试会结束，用户确认后点击“重试初始化”再连接。

Store 应用禁止直接运行安装目录中的 EXE 时，使用已注册的应用入口启动。运行中的 Claude 从目标进程读取应用身份；首次启动则核对安装注册信息和清单中的准确入口，不要求手工填写包名或更改 WindowsApps 权限。

DSH 的 **Harness 数据目录**是存放配置、会话和连接插件的数据目录，本机为 `C:\Users\19297\.dsh`，不是 `F:\DSH\DSH Desktop` 程序安装目录。该字段已与账号名称输入框分开绑定，扫描和刷新不会再把路径误写入账号名称。

针对这次问题的回归检查：

```powershell
.\.tmp\build-env\Scripts\python.exe -B -m unittest tests.test_windows_discovery tests.test_desktop_discovery tests.test_claude_setup tests.test_client_scan tests.test_client_inventory tests.test_client_launch tests.test_client_lifecycle tests.test_claude_accounts -v
node --test tests/client-connections-ui.test.cjs tests/client-accounts-ui.test.cjs
.\.tmp\build-env\Scripts\python.exe -B -m unittest tests.test_claude_native_helper -v
.\.tmp\build-env\Scripts\python.exe -B -m unittest tests.test_windows_launch -v
.\.tmp\build-env\Scripts\python.exe -B -m unittest tests.test_claude_background -v
```

## 构建

### 停用手机接入与退出电脑 App

手机“应用管理”和网关客户端开关现在提供三个明确选项：

- **仅停用手机接入**：保存停用状态，保留电脑 App、正在运行的任务和已有终端；即使尚未连接、任务状态未知或程序路径失效也可停用。停用后到达的聊天、文件和终端操作会被阻止。
- **同时退出电脑 App**：请求客户端正常退出；已知正在运行或等待确认的任务仍会阻止退出。Claude 通过原生菜单处理自己的退出与保存确认，具体行为见下文。
- **取消**：不发送停用请求，开关保持原状。按 Escape 也会取消。

Windows DSH 2.0.17 的 Host 是 Electron Node utility 进程，已修复此前把它当作普通子进程过滤的问题。原生退出会核对唯一 Host、连接实例与完整任务状态；新连接器使用 DSH 自己的 `appExit` 完成清理。

正在运行的 revision 3 连接器也可正常退出：网关先核对所选 DSH 安装包内的原生安装器退出逻辑、进程和数据目录，再调用应用自带的 `--dsh-installer-quit` 通道。它使用 DSH 的正常退出流程，不显示窗口。本机已对 DSH 2.0.17 与 revision 3 连接器实测：发起请求后约 **10.3 秒**相关进程全部消失，前台窗口未改变。这个时间是本机测试结果，实际耗时取决于客户端清理工作。新连接器仍只在应用已停止后更新，无需为了本次退出先重启升级插件。

Windows Claude 先通过原生菜单 `Menu → File → Exit` 在后台请求正常退出，不要求占用前台焦点。只有确定尚未提交退出、且桌面可交互时，才允许一次恢复窗口的前台回退；菜单可能短暂出现。退出不模拟键盘、不强杀进程。助手保留 Claude 的任务与保存确认，不会替用户点击“仍要退出”等按钮；如有提示，请在电脑端确认或取消。

只有用户显式选择“同时退出电脑 App”（`quitDesktop: true`）时，尚未连接或任务证据不完整的 Claude 才可把退出确认交给原生客户端。若网关已读到任务正在运行或等待确认，仍会拒绝退出；账号切换和自动重连不使用这一例外。

两种退出方式都必须等所选应用的全部相关进程消失后才报告成功；窗口隐藏、菜单已点击或请求已提交都不算完成。取消或退出失败时，接入开关保持原状。

刷新手机页面即可加载新的关闭选项。相关回归命令：

```powershell
$env:PYTHONPATH='tests'
.\.tmp\build-env\Scripts\python.exe -B -m unittest test_client_lifecycle test_client_launch test_dsh_windows_quit test_dsh_utility_host test_deepseek_migration test_deepseek_recovery test_claude_native_helper test_claude_setup -v
node --test tests/client-lifecycle-ui.test.cjs tests/client-navigation.test.cjs tests/client-connections-ui.test.cjs tests/deepseek-native-quit.test.mjs
```

### 编译步骤

在 Windows x64 上使用原生 Python 3.12 或更高版本、Node.js 24。此工作树已准备好 `.tmp/build-env` 和 `node_modules`。

```powershell
# 首次准备新工作树时执行：
python -m venv .tmp/build-env
.\.tmp\build-env\Scripts\python.exe -m pip install -r requirements-desktop.txt
npm.cmd ci
# Electron 44 的包不自动执行二进制下载：
node node_modules/electron/install.js

# 编译辅助程序、网关、Windows App、ZIP 和安装程序：
.\.tmp\build-env\Scripts\python.exe scripts/build-desktop.py
npm.cmd run build:windows
```

输出位于 `dist/desktop`。使用项目根目录的启动脚本测试，可与已安装版本同时运行。安装包面向正式替换安装；替换前需退出旧网关以释放文件占用。

```powershell
.\.tmp\build-env\Scripts\python.exe -B -m unittest discover -s tests -v
npm.cmd run test:desktop
npm.cmd run test:updater
npm.cmd run test:windows-app
```

构建和运行日志位于 `.tmp`，网关日志和登录配置位于 `.local/windows-preview3-user`。这些目录被 Git 忽略。

## Codex 正常退出

Windows Codex 的关闭按钮会隐藏到托盘，不能用 `CloseMainWindow()` 判断退出。手机选择“同时退出电脑 App”以及 Codex 账号切换时，网关改为通过应用自己的文件菜单调用正常退出。本机安装版本的退出项实际显示为“退出 ChatGPT Ctrl+Q”；助手精确核对退出项，不会点击旁边的“退出登录”。

退出前核对程序路径、用户会话、PID 和进程创建时间。托盘中有唯一可信主窗口时，只恢复原窗口且不要求前台焦点，不重新启动应用。菜单可能短暂显示；已有原生保存或任务确认会保留，网关不会代替用户确认。助手最多等待 20 秒，整个退出观察最多 25 秒；只有所选程序的主进程及子进程全部结束才报告成功。

菜单不可用、进程变化或原生确认待处理时，返回具体原因并保留接入开关。请求一旦可能已经提交，网关只核对实际退出结果，不自动重复点击；用户处理提示后可以再次尝试。独立非活动桌面测试覆盖窗口可见、托盘隐藏、确认与取消等场景，不能替代当前 Codex 的真实 Win+L 验收。

## Windows 后台强制结束

Windows 网关运行时，关闭选择框另提供“后台强制结束”，与“正常退出电脑 App”和“仅停用手机接入”分开。每次默认焦点都是“取消”，重试必须重新选择。这个操作直接结束所选桌面程序的进程，不使用菜单、键盘、窗口恢复或输入桌面，因此不以解锁为前提。电脑仍须保持唤醒、用户登录且网络可用。

强制结束可能中断任务或丢失未保存内容，不会执行应用的正常保存、退出确认与清理流程。这是 Windows [TerminateProcess 的进程终止语义](https://learn.microsoft.com/windows/win32/api/processthreadsapi/nf-processthreadsapi-terminateprocess)，不能把它称为后台“正常退出”。DSH 已有后台正常退出通道，可以继续优先使用正常退出；Codex、Claude 的正常退出菜单仍可能需要可交互桌面。

仅这次明确选择才会发送 `enabled: false, quitDesktop: true, forceDesktop: true`。服务端仍要求登录和 CSRF，并核对指定程序的绝对路径、当前用户会话、PID、创建时间与原有进程句柄；不按程序名或整棵父子树批量结束。网关本身及其启动进程受到保护，新出现或身份不明的进程不进入终止集合。只有确认目标进程全部消失才保存停用状态，失败不会显示成已经退出。其他路径的命令行运行时不在此操作的目标中，不能据此宣称所有外部任务均已结束。

## 锁屏与失败重试

这里的锁屏指 Windows 用户仍登录、电脑保持唤醒且网络可用。网关读取自身用户会话和输入桌面状态，不解锁电脑、不切换桌面。锁屏不应让已有连接变成断开，也不会禁止聊天、文件、终端及后台启动与重连请求。手机和桌面管理界面显示独立的锁屏提示。

受支持的 Windows Claude 后台初始化不再以解锁状态作为前提。原生助手核对进程、会话、程序路径、唯一 app://localhost 开发者工具、Console 内部焦点与完整脚本；已有草稿或目标变化时停止。后台连接助手有 25 秒预算，Python 侧 30 秒预算；取消只停止助手，不关闭 Claude。收到当前 generation 的签名心跳后，定向关闭空白的调试窗口；清理失败保持已连接状态并提示，不关闭用户草稿或主窗口。前台回退仍保留锁屏与焦点保护，不会在安全桌面发送全局输入。

Claude 正常退出优先使用原生后台菜单；菜单导航最多等待 8 秒，失去响应的导航不会再并发发起前台回退。退出一旦可能已提交，就继续观察实际进程退出，不因回执中断再次点击。原生任务、保存或信任确认由用户处理。某些客户端菜单在锁屏时不向 Windows 辅助接口开放，届时会保留失败原因并允许解锁后重试，不能宣称当前 Claude 的所有冷启动和退出场景都已支持锁屏。

启动和退出失败会保留具体原因与对应重试操作，退出失败不会把仍然连通的客户端显示成断开。浏览器等待超过 45 秒或网络中断时显示“结果待确认”，先刷新核对实际状态；这不代表服务端已取消。状态读取与生命周期操作串行，未确认前不能重复提交。DSH 恢复操作重试会重新预览并确认。

用户实测 DSH 在 Win+L 锁屏下可以启动和退出。Claude 的新路径已经过默认沙箱、非活动桌面的定向输入与草稿保护测试，并在当前安装版本上完成真实正常退出、隐藏主窗口冷启动、后台脚本提交及签名心跳验证。测试没有替用户锁屏；独立非活动桌面不等于真实 Win+L 端到端验收，最终实机结果见 `dist/desktop/WINDOWS-TEST-REPORT.md`。

## Claude / DSH 会话加载缓存

手机和网页切换应用时优先恢复已读过的列表与正文，随后刷新。页面可见时，每 30 秒至多预取一次已启用且已连接应用的列表；不会因此启动应用或初始化连接，也不批量预取聊天正文。重复点击当前应用图标保留当前会话和草稿。同一详情的并发读取共享请求，首个有效响应直接显示；列表刷新不再等待正文。

缓存只驻留当前页面内存，正文最多保留 12 个会话、估算 8 MiB；关闭或刷新页面后重新获取。首次未缓存的读取仍受客户端桥接和网络速度影响。切换账号、停用接入或退出登录会作废旧读取，迟到结果不能重新填回缓存；旧账号的写请求也不能取消新账号列表读取。

刷新失败或客户端断开时，可以继续阅读已有缓存；发送、停止和审批必须等当前详情刷新成功并确认连接。写操作前后使旧读取失效，失败重试先刷新，避免基于过期会话状态操作。

本轮可在不重启网关与隧道的情况下更新三个网页资源；手机刷新页面即可加载。独立合成会话测试确认：同一详情从两次请求降为一次，约 500ms 的首次回包不再被丢弃；切回已缓存应用同步恢复列表和正文。这是页面逻辑测试，不代表手机真实绘制或网络延迟保证。
