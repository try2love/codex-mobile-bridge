# 开发说明

本扩展独立放在 `contrib/codex-mobile-assistant/`；下文命令以该目录为工作目录。上游根目录的测试、构建与发布流程尚未接入此扩展。本次贡献未执行下述命令。

Windows 图形入口建议 Python 3.13 x64，依赖固定在根目录 requirements 文件中。网页不需要 Node 构建，也不依赖 CDN。

原生手机 App 位于 `mobile/android` 和 `mobile/ios`，加载同一网页入口。工具链、手动打包与设备核对说明见 [手机 App 文档](mobile.md)；修改网页无需重新打包原生容器。

运行网关测试（按需）：

```powershell
cd app
python -B -m unittest discover -s tests -v
```

`app/tests/` 下的 JavaScript 文件是前端测试材料；它们不包含浏览器运行依赖。变更接口或 UI 时请同步维护测试中的脚本列表和 DOM 夹具。

调试请使用临时数据目录及自己的测试账号。对真实桌面会话的发送、审批、删除都会影响真实任务。

提交时仅纳入源码。`.local`、日志、SQLite、EXE、压缩包及下载组件已在 `.gitignore` 中排除；二进制发布请使用 GitHub Releases。
