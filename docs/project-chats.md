# 新建项目聊天 / Create a project chat

在 Web 或手机的聊天列表点击 **新建**，先选择运行环境，再选择已有项目或点击 **选择目录**。目录选择器可以打开其他文件夹，也可以展开 **新建文件夹**，在选定的父目录中创建项目文件夹。不要求该目录以前有过聊天，不会初始化 Git 或覆盖已有文件。

只有一个运行环境时直接显示其名称；多个环境时可以切换。项目路径默认显示末两级目录，展开后可查看完整路径。更换环境会清除尚未确认的目录选择，避免把另一台电脑或另一种接入的目录带入新聊天。

| 接入 | 项目目录所在位置 | 最后一步 |
| --- | --- | --- |
| Codex App | 选定的本机或已登记 SSH 主机 | 创建并打开 |
| Codex CLI | 选定的本机或已登记 SSH 主机 | 创建聊天 |
| WSL Codex | 选定发行版、用户及配置目录对应的环境 | 创建聊天 |
| Claude Desktop Code / Cowork | 当前 Desktop 环境 | 填写首条消息，创建并发送 |
| Claude Code CLI | 本机或已登记 SSH 环境 | 填写首条消息，创建并发送 |
| DeepSeek Harness Desktop | 当前 Harness 工作区所在电脑 | 创建并打开 |

浏览和选择目录不会创建聊天或发送消息。Claude 需要首条消息才能创建会话，因此按钮明确标注 **创建并发送**；其他接入先创建空聊天，之后由用户输入发送。提交后若结果尚不能确认，请先检查原环境，不要重复创建。

Codex App、CLI 和 WSL 的聊天名称可留空；填写后使用该名称，留空保留原生无名称状态，后续由原客户端命名。创建空聊天本身不会调用模型生成名称。

Codex App 和 DSH 的项目聊天会复用所选目录对应的原生项目。本机 Codex 目录尚未登记时，会打开电脑 App 的空白新建页，由 App 登记项目；若电脑端需要确认目录，请先完成确认，再返回重试。确认原生项目已保存后才创建聊天，登记结果未知时不会重复登记或创建。支持项目归属接口的 Codex 会核对新聊天保存后的归属，DSH 使用原生工作区 ID 创建聊天。此操作不移动已有聊天，也不改写已有项目设置。

Codex 本机新目录需要 App 支持目录登记入口；旧运行时仍可沿用 App 已保存的项目。SSH 的新目录请先在电脑 App 添加为项目，再返回选择；已有 SSH 项目及 Codex CLI 创建不受此限制。DSH 的任意新目录创建需要更新后的连接器及支持原生工作区接口的 Harness；旧连接器仍可使用其已有能力，升级连接器后才显示选择其他目录的入口。DSH 没有独立 CLI 或 SSH 接入选项。

Claude Desktop Code 若提示需要信任工作区，请在电脑的 Claude Desktop Code 页面打开所选目录，亲自确认“信任工作区”，再返回重试。所选目录、聊天名称和首条消息会保留；网关不会自动授予信任。超时或连接中断仍需先检查原环境，不能按信任拒绝直接重试。

目录权限使用原运行环境的权限；创建失败不会自动切换用户、账号或执行环境。选择项目不迁移已有会话，也不修改原项目配置。

## English

Select **New** in Web or mobile, choose a runtime environment, then select an existing project or **Choose directory**. The directory picker also offers a collapsed **New folder** action. A directory does not need an existing chat. Creating a folder does not initialize Git or overwrite files.

The same picker serves Codex App, local/SSH Codex CLI, WSL Codex, Claude Desktop Code/Cowork, local/SSH Claude Code CLI, and the existing DeepSeek Harness Desktop connection. Paths belong to the selected environment. Changing environments clears an unconfirmed directory selection. Expand the short path summary to see the full path.

Browsing does not create a chat or send a message. Codex App and DSH use **Create and open**; Codex CLI and WSL use **Create chat**. Claude requires an initial message and explicitly uses **Create and send**. An uncertain submission is not replayed automatically. Codex App and DSH reuse the native project for the selected directory. A new local Codex directory opens an empty Desktop creation page so the App can register the project. Complete any folder confirmation on the computer before retrying. The bridge waits for the native project record before creating a chat and does not repeat an unconfirmed registration. Existing chats and project settings are retained. Older runtimes retain the saved-project path. For a new SSH directory, add it as a project in the computer App first; existing SSH projects and Codex CLI creation keep their current behavior. Arbitrary DSH directories require an updated connector and Harness native workspace support. DSH does not offer a separate CLI or SSH connection.

If Claude Desktop Code requires workspace trust, open the selected folder in Desktop Code, explicitly approve trust, and return to retry. The selected folder, chat title and first message remain in the dialog. The bridge never grants trust automatically. A timeout or disconnect remains an uncertain submission: inspect the original environment before retrying.

Creation uses the original environment's permissions and account. Existing chats, project settings and connections are not migrated.
