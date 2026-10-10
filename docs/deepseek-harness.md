# Codex、Claude Desktop 与 DeepSeek Harness 接入

v2.0.0 在同一个网关提供三个应用各自的会话列表、配置和工作台。既有账号、工作目录和任务仍由原客户端拥有；共享界面不代表三个应用具有完全相同的模型、权限和工具能力。

## 自动发现与启用

打开网关 App 后自动扫描已安装客户端。在 **客户端与接入** 查看安装、配置和连接状态；无需先启动网关即可扫描、管理账号或填写支持的 API。后续安装了新客户端，可重新扫描。

- **Codex**：识别本机桌面程序及当前账号/API，沿用本机和已配置 SSH 会话。
- **DeepSeek Harness**：复用桌面数据目录、账号、模型、项目和历史，安装并核验本地接入插件。插件需要更新且客户端仍在运行时，先结束任务，再按提示「重启并接入」。未完成登录/API 设置时，在 Harness 中完成后重新扫描。
- **Claude Desktop**：接入 Code 和 Cowork，普通 Chat 不在范围内。macOS/Windows 提供原生初始化助手；macOS 首次使用可能需要给网关连接组件辅助功能权限，Claude 的开发者模式和目录信任仍由用户确认。Linux 可以发现安装，但自动连接暂不支持。

只有配置就绪的客户端可以启用。单纯打开网关控制面板或扫描不会自动启动所有应用；启动网关后才按启用状态接入。Web/手机只显示已启用的客户端，只有一个应用时隐藏底部切换栏。创建会话归属当前应用，Claude Code/Cowork 分组可以折叠。

## 连接恢复与退出

Web、手机和桌面网关的应用管理提供状态、重新连接与启停。运行中、等待授权、排队和结果未确认的操作会限制退出/切换；操作前重新核验任务与进程，不使用展示缓存作为安全判断。

退出选择区分 **仅停用手机接入**、**正常退出 App**、**强制退出 App**。强制退出需要额外确认，可能中断客户端内的任务，不能把它当作正常退出失败后的自动回退。程序身份不明时仍会拒绝，避免结束其他程序。退出 Claude 的确认会说明重新初始化可能需要解锁电脑。

Claude 优先复用已有连接。屏保/锁屏时，已建立的本地连接可以继续通信；首次初始化或需要重新操作 Console 时，可能等待退出屏保、解锁和系统授权。没有无界面冷启动保证，不模拟解锁，不修改密码或锁屏策略。重新连接不会自动重发消息。电脑睡眠时三个客户端均不能保持远程通信。

Harness 升级保留原配置与备份，只有安装记录、插件来源、端点和数据目录互相验证后才恢复旧接入；不要求用户盲目移除另一个网关。配置排版变化不影响识别，残留实例会按身份与任务状态处理。无法核验的配置保留原样，提示检查而非覆盖。

同一台电脑升级时，先停止旧网关再启动新版，保留原网关数据目录。不要让两份网关并发操作同一套客户端和操作回执。

## 账号、额度与模型

在桌面 **客户端与接入 → 管理** 保存、重命名、编辑和删除各应用支持的接入；Web/手机只查看与切换已保存配置。删除档案只删除网关保存的副本，不等于退出原客户端账号。

| 应用 | 可管理内容 | 边界 |
| --- | --- | --- |
| Codex | 官方登录、导入本机配置、自定义 API、上游模型、额度和重置卡 | 详见[账号与接入](account-switching.md)；切换重启原应用 |
| Claude | 保存当前登录或第三方网关配置；编辑 API 地址、Bearer/x-api-key、密钥及模型 ID | 官方登录仍在 Claude 完成；模型 ID 需上游支持；保存登录/切换可能需受保护重启 |
| DSH | 保存当前官方账号/API，新增或编辑 DeepSeek 官方 API Key，查询可用余额 | 该表单不接收任意第三方地址；其他提供商需先在 Harness 配置 |

账号面板显示当前接入和可查询额度，不把查询失败显示为零。打开时查询，可见时每五分钟更新，手动查询立即执行并重新计时。模型与推理能力来自对应客户端；无可用会话或上游无法返回时明确提示未知，不能据 Codex 列表推断其他客户端模型。

## 会话工具与上下文

Claude/DSH 复用聊天列表、消息、复制、附件、文件/Git/终端标签、通知、下载和分屏组件；具体可用操作以各适配器的能力为准。Codex 临时侧边聊天、Goal、Fast 和 Skills 不自动等同于其他应用支持。

上下文圆环位于权限旁：Codex 使用最近请求的占用，Claude Code 使用实际上下文汇总，DSH 使用 context pressure 投影并标记估算含义。缺少容量/用量时显示未知，不使用累计计费 Token 推算占比。Cowork 未提供相同汇总接口时不显示虚假的百分比。

## 兼容性与验证

内部协议可能随上游版本变化。安装发现、模拟协议、源码回归、打包冒烟与真实账号/模型运行是不同验证范围；Linux 网关仍为实验性，不能将原生包成功启动理解为所有客户端都已支持。

Claude 连接器和部分 Harness 插件参考 MIT 项目 [2389859005/coding-mobile](https://github.com/2389859005/coding-mobile/tree/7a8f003dcb88bb28c6a043b7c8531e337c6b8c9b)，归属及许可保留在 `bridge/clients/LICENSE.coding-mobile`。实现及历史验证见[架构导航](architecture/README.md)、[平台整合记录](architecture/platform-validation-integration.md)。

## 可选：Harness 官方 Web 转发

网关可以启动本机 DeepSeek Harness，并通过同一个登录入口转发它的官方 Web 界面。Codex 聊天入口继续保留；Harness 使用自己的工作区、模型设置、会话记录和权限确认界面。

### 使用

1. 按 [Harness 官方说明](https://github.com/deepseek-ai/deepseek-harness)单独安装 Harness。网关不自动安装，也不随安装包捆绑 Harness。
2. 在桌面网关 App 的 **Harness 官方 Web** 页点击 **扫描本机 Harness**，或选择程序。支持独立可执行程序和 npm 包中的 `@deepseek-ai/dsh/lib/bin.js`。JavaScript 入口需要 Node.js；Windows 的 `.cmd` / `.bat` 不直接执行。
3. 选择工作目录和 Harness 数据目录并保存。默认数据目录是网关数据目录下的 `harness-home`，与既有 Harness 配置分开。请勿让两个运行中的 Harness 共用这个目录。
4. 启动网关，再点击 **启动 Harness**。首次启动可能需要更长时间。模型提供商及凭据在官方 Web 界面中设置。
5. 手机登录原来的网关地址，点击聊天列表下方 **Harness 官方 Web ↗**。也可以访问 `网关地址/harness/`；未登录时先登录，再进入 Harness。桌面页的 **打开 Harness** 使用相同入口。
6. 返回 Codex 网关可使用浏览器返回，或重新打开网关首页。停止 Harness 不停止 Codex 网关；停止网关会一并停止它启动的 Harness，运行中的 Harness 任务会中断。重新启动网关后需手动启动 Harness。

Harness 程序、目录配置和进程启停只在桌面控制器中提供。Web 入口不提供网关层的安装、配置或启动接口；官方 Harness 界面自身的设置功能仍然保留。

### 网络和访问

- 复用已有局域网、HTTPS 隧道或反向代理入口，无需额外公开 Harness 端口。
- 自建反向代理需要允许 `/harness/` 下的 WebSocket Upgrade，并设置足够的上传大小和连接超时；仅打开 HTML 页面不代表实时交互已连通。
- 所有 Harness 页面、资源、HTTP API 和 WebSocket 都要求有效的网关登录。写请求和 WebSocket 必须来自当前入口的精确同源地址。
- Harness 的启动令牌和登录 Cookie 只保存在网关进程内。浏览器不接收它们，网关 Cookie 也不会转发给 Harness。
- 注销、撤销登录、封禁设备 IP 或停止 Harness 后，已有 WebSocket 会断开。
- 启动状态日志不记录 Harness 的原始输出，避免令牌、模型凭据和文件内容进入网关日志。
- 支持有 Content-Length 的流式上传，单次上限 300 MiB；文件下载按流转发。不接受分块编码的请求体。

所有登录网关的设备使用同一个 Harness 实例和数据目录，与官方单用户 Web 模式一致；此功能不提供不同用户之间的工作区隔离。

### 兼容性与范围

已在 macOS 上验证官方 npm 版本 **`@deepseek-ai/dsh@0.2.0-rc.2`**：启动、认证、官方页面及资源、模型目录和工作区接口、WebSocket 连接，以及真实 Electron 控制面板的保存和启停。测试未提交模型推理请求，未验证付费模型执行和实际工具批准流程。

Windows / Linux 采用相同转发协议和各平台子进程启动方式，尚待对应系统实测。移动浏览器已检查 390 × 844 视口，布局由官方 Harness 控制。本轮未在 Android / iOS 真机验证；原生手机 App 的附件下载适配尚未覆盖 Harness，请优先使用手机浏览器。

此独立 Web 入口不合并 Codex 与 Harness 的聊天列表、账号、模型列表或通知；不提供 Harness 的 SSH 运行管理。Harness 自身显示的内部服务地址仍为回环地址，手机应使用网关 `/harness/` 入口。

### 开发验证

```sh
python3 -B -m unittest discover -s tests -p test_harness.py -v
npm run test:desktop
```

协议测试使用本地模拟运行时，不调用模型，覆盖认证隔离、二进制上传下载、实时连接和进程关闭。真实桌面冒烟脚本为 `scripts/smoke-harness.cjs`，需用 Electron 运行，并设置全新的隔离 `CMB_DATA_DIR` 与已安装程序的 `CMB_HARNESS_EXECUTABLE`；禁止指向正在使用的数据目录。

上游协议参考：[Web 包](https://github.com/deepseek-ai/deepseek-harness/tree/5badb15009ae1756c3afe0ae0cef1faafc290ccc/packages/bundle/web-app)、[公开部署说明](https://github.com/deepseek-ai/deepseek-harness/blob/5badb15009ae1756c3afe0ae0cef1faafc290ccc/docs/user/guide/public-deployments.md)。仓库版本的 `--public-url` 尚未出现在上述 npm 版本中，因此本实现使用共同支持的 `--profile web --no-open --port` 与官方前端的相对路径。
