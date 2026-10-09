# 桌面会话接入（v2 Preview）

此功能基于 `v2.0.0-preview.3`，当前位于本地 `feature/preview3-client-autoconnect` 分支，尚未发布。

## 自动发现与接入

打开桌面网关后，会自动扫描 Codex、Claude Desktop 和 DeepSeek Harness。进入 **客户端与接入** 可查看安装、配置和连接状态。无需先启动网关即可扫描客户端、管理 Codex 账号或添加 API。后续安装了客户端，点击 **重新扫描并接入** 即可。

- **Codex**：识别本机桌面程序及当前账号或 API，沿用现有会话。
- **DeepSeek Harness**：复用桌面客户端的数据目录、账号、模型、项目和历史，自动安装本地接入插件。首次安装后，正在运行的 Harness 需要重启；结束任务后点击 **重启并接入**。尚未完成首次设置时，点击 **打开并接入**，在 Harness 中完成登录或 API 配置后重新扫描。
- **Claude Desktop**：自动识别安装位置，并通过原生开发者工具连接 Code / Cowork，沿用已有登录，无需复制连接脚本。macOS 首次使用需在系统设置授予连接组件辅助功能权限；Claude 自身的开发者模式和目录信任提示由用户确认。需要重启时会单独显示 **重启并接入**，扫描不会自动重启运行中的 Claude。收到经过校验的连接回执后才显示“已连接”。当前 macOS / Windows 接入仍为实验性，普通 Chat 不支持。

只有确认账号或模型配置可用的客户端才能启用。网页和手机端只显示已启用的客户端；只有一个客户端时隐藏底部切换栏。客户端安装、扫描和重启仅在桌面网关提供。

### Claude 连接与退出

- **屏幕保护程序**：已有有效连接继续通过本地连接器通信。首次接入或需要重新操作 Console 时，显示等待退出屏保、解锁或返回当前用户桌面的状态，桌面恢复后自动继续。不会模拟解锁或反复打开 Console；电脑睡眠时仍无法通信。
- **手机重新连接**：在 Web / 手机 App 的“应用管理”中点击 Claude 的“重新连接”。需要启用密码保护、已在电脑端配置接入，并且 Claude 仍在运行。它只恢复已有客户端的连接，不重启应用、不重发消息；已连接时也会核验通信是否正常。操作后保留当前聊天和草稿。有请求结果尚未确认时，需先在电脑端核对，再通过桌面控件重新接入。
- **断连时退出**：普通应用开关仍会在任务状态不明时拒绝关闭。macOS / Windows 桌面网关另有“退出 Claude…”入口，核对当前进程并要求确认。无法核验任务时，需明确确认可能中断任务；已知任务仍在运行或等待授权时继续拒绝。只请求原生正常退出，未退出成功不会关掉启用开关，也不会强杀进程。

Harness 插件安装保留原有配置及备份。若此前使用过 Bridge 测试版，扫描会验证并恢复已有接入，显示 **已恢复此前的 Harness 接入**；无需返回旧网关移除配置。正在工作的插件、连接令牌和桌面任务保持原状，重新打开新网关后仍可继续使用。操作回执沿用原接入记录，避免恢复连接后重复执行同一请求。

同一台电脑升级测试时，请停止旧网关后再用新网关操作会话，避免旧版网关并发占用操作记录；无需卸载旧插件，也无需重启 Harness。

Harness 自动调整配置文件的换行和排版不会影响接入识别。只有安装记录、插件来源和数据目录能够互相验证的旧接入才会自动恢复；无法验证的配置保留原样，并提示重新扫描或检查。扫描不会中断正在运行的桌面任务，也不会重启 Codex。

## 验证范围

自动检查覆盖：macOS、Windows、Linux 安装位置发现，后续安装重扫，网关停止时的配置与账号管理，插件幂等安装、认证与 CSRF、请求去重、权限回答及中继转发。平台模拟检查不替代对应系统的真实桌面联调；模型回复、工具执行和客户端版本兼容性需要分别验收。Claude 原生组件已在 macOS 编译并通过焦点保护自检，完整接入仍需本机系统权限和真实连接回执验证；Windows 原生操作尚待真机验证。

Claude 连接器及部分 Harness 插件代码参考 MIT 项目 [2389859005/coding-mobile](https://github.com/2389859005/coding-mobile/tree/7a8f003dcb88bb28c6a043b7c8531e337c6b8c9b)。归属及许可保留在 `bridge/integrations/LICENSE.coding-mobile`。

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
