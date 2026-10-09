# Preview 3 Windows 本地测试

此工作树基于 `feature/preview3-client-autoconnect` 的 `d56cb0c`，本地修复分支为 `codex/preview3-windows`。

## 启动与手机测试

双击项目根目录的 `start-preview3-windows.cmd`。它启动 `dist/desktop/win-unpacked` 中的已编译 App，并使用独立的 `.local/windows-preview3-user` 配置目录。

本机已经准备好端口 **8788** 的测试配置：手机与电脑在同一网络时，打开网关面板显示的局域网地址。当前地址为 `http://10.21.154.171:8788/`；本机网页访问遵循现有设置，关闭时 `127.0.0.1:8788` 返回 403 属正常行为。展开面板里的二维码扫码登录，也可在面板查看首次登录凭据。原有 8787 网关使用原配置继续运行。

新实例连接电脑现有的 Codex 数据目录。请在手机选择已保存的项目，新建一个测试聊天；该聊天会真实出现在 Codex 中。测试完成可从新网关的托盘选择“停止网关并退出”。

运行配置中的 **Codex 程序路径留空**可自动发现，并在 Codex 更新移除旧运行时后重新定位。手动填写的路径会被尊重；路径失效时应清空或改成当前的 `codex.exe`。CLI 运行时与桌面 GUI 可执行文件是不同配置。

旧版已记录为“创建结果尚不确定”的请求不会自动重放，以免重复创建。更新后先检查已有会话列表，再新建一次测试聊天。

## Windows 客户端扫描修复

客户端页的“重新扫描”现在同时读取当前用户的 Store 包注册信息、安装注册表、App Paths 和当前会话的桌面进程。它不再依赖遍历受保护的 WindowsApps 目录，也支持安装到其他盘符的 `DSH Desktop`。Store 包优先使用清单中声明的桌面入口，避免把 Codex 的启动垫片或命令行运行时当作 GUI。

Claude 优先根据所选应用进程的 `--user-data-dir` 定位活动配置；应用未运行时，检查对应 Store 包目录、`Local/Claude-3p`、`Local/Claude-Data` 和传统 Roaming 目录的配置证据。手动指定的数据目录保持优先。账号模块沿用这些目录；无法确定配置归属时拒绝切换账号，避免向错误目录写入。

扫描只发现应用和配置，不会自动完成 Claude 或 DSH 的连接、修改原生开发者设置或切换账号。“已安装”“正在运行”和“已连接”是不同状态。更新本地测试版后重新打开启动脚本，客户端页会自动重新扫描，也可以手动点击“重新扫描”。

Codex 详情中的“扫描桌面程序”也使用 Windows 原生发现结果，可定位 Store 安装的桌面入口，即使 CLI 位于独立更新目录。

Claude 自动启动与重连只准备后台通信，不会自动打开 Claude、弹出开发者工具或模拟键盘操作。已有连接器可在网关重启后重新接回；尚未初始化或 Claude 退出后，界面显示“需要手动初始化”。点击“手动连接”才会启动前台引导，这一过程仍需短暂保持 Claude 窗口焦点，不能称为首次全程静默连接。

手动连接时，若 Claude 在系统托盘、没有可见窗口，助手通过所选 Claude 的原生启动入口恢复窗口。窗口识别限定当前 Windows 会话、进程和完整程序路径。连接脚本一次性写入，并在完整读回核对后提交，已移除逐字键盘输入；重试会复用已有开发者工具，保留无关草稿。当前 Windows 版的开发者菜单路径为 `Menu → Help → Troubleshooting → Enable Developer Mode`；助手保留 Claude 自己的确认弹窗，需要在弹窗中确认后继续连接。

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

Windows Claude 通过原生菜单 `Menu → File → Exit` 请求正常退出，可能短暂恢复 Claude 窗口并显示菜单，不模拟键盘、不强杀进程。助手保留 Claude 的任务与保存确认，不会替用户点击“仍要退出”等按钮；如有提示，请在电脑端确认或取消。

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
