# 手机网关核心

本目录基于 [try2love/codex-mobile-bridge](https://github.com/try2love/codex-mobile-bridge) 修改。原许可证保留在 [LICENSE](LICENSE)，基线提交记录在 [SOURCE_VERSION.txt](SOURCE_VERSION.txt)。

双模式 Windows 入口及新版功能见[仓库 README](../README.md)。

仅使用命令行网关时，可在此目录运行 `python run.py --lan`；该核心使用 Python 标准库。外网访问还需要 cloudflared 或自行配置反向代理。

凭据和运行数据默认为本目录的 `.local/`，不得上传或公开。模型任务仍由已安装的 Codex Desktop 执行。细节见 [ARCHITECTURE.md](ARCHITECTURE.md)。
