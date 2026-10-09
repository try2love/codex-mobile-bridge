# Preview 3 Windows 本地测试

此工作树基于 `feature/preview3-client-autoconnect` 的 `d56cb0c`，本地修复分支为 `codex/preview3-windows`。

## 启动与手机测试

双击项目根目录的 `start-preview3-windows.cmd`。它启动 `dist/desktop/win-unpacked` 中的已编译 App，并使用独立的 `.local/windows-preview3-user` 配置目录。

本机已经准备好端口 **8788** 的测试配置：电脑打开 `http://127.0.0.1:8788/`，手机与电脑在同一网络时，打开网关面板显示的局域网地址。展开面板里的二维码扫码登录，也可在面板查看首次登录凭据。原有 8787 网关使用原配置继续运行。

新实例连接电脑现有的 Codex 数据目录。请在手机选择已保存的项目，新建一个测试聊天；该聊天会真实出现在 Codex 中。测试完成可从新网关的托盘选择“停止网关并退出”。

运行配置中的 **Codex 程序路径留空**可自动发现，并在 Codex 更新移除旧运行时后重新定位。手动填写的路径会被尊重；路径失效时应清空或改成当前的 `codex.exe`。CLI 运行时与桌面 GUI 可执行文件是不同配置。

旧版已记录为“创建结果尚不确定”的请求不会自动重放，以免重复创建。更新后先检查已有会话列表，再新建一次测试聊天。

## 构建

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
