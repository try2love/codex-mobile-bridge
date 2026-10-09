# 项目开发导航

先读 [docs/architecture/README.md](docs/architecture/README.md)，按功能、客户端和操作系统定位改动。运行与协议约束见 [ARCHITECTURE.md](ARCHITECTURE.md)。

- Web 的宿主适配归 `web/hosts/`，宽窄布局归 `web/layouts/`，两端共同注入脚本归 `mobile/shared/web/`。遵循各自 `AGENTS.md`；原生运行环境与窗口尺寸必须独立。
- `bridge/features/` 放共享业务；`bridge/clients/` 放 Codex、Claude、DeepSeek 协议；`bridge/platforms/` 放原生系统操作。遵循目标平台子目录的 `AGENTS.md`。
- 平台实现不能反向导入客户端或业务模块。共用规则不复制到各系统；操作系统指本机还是 SSH 目标，必须分清。
- 保留原桌面会话的 owner/provider/auth/cwd；被动读取和通知不得启动或切换会话。发送/授权/停止不得加入只读请求合并。写入结果未知不得自动重放。
- 停止应用、切换账号、恢复接入前即时核验进程身份和任务；展示缓存不能作为安全判断。认证、配对、CSRF、文件边界、重定向限制不能为了某系统适配而放宽。
- 旧 Python 模块路径不再是入口；用明确的新 import，不要加全局模块别名。对照表：`docs/architecture/path-map.json`。
- 迁移网页资源时保持公开 URL 与脚本顺序，同步 `web/assets.json`、固定资源白名单和桌面资源清单。SSH 注入的源模块必须保持远端独立可执行，同步 `scripts/gateway-resources.json`。
- 验证入口见 [tests/README.md](tests/README.md)。`python -B scripts/test-backend.py --source-only` 不执行 Swift 或 Windows 原生编译/执行夹具；`npm run test:desktop` 自动发现真正的 Node 测试。浏览器夹具单独运行，不可将加载或空跑算作通过。
- 真实客户端操作、构建、安装、推送、版本号及发布按本次用户授权范围执行。源码整理不隐含这些授权。
- `.tmp/` 放临时测试文件；`.local/` 放本机运行数据与本地归档；二者均不提交。不要提交凭据、聊天数据、安装包或测试截图。
