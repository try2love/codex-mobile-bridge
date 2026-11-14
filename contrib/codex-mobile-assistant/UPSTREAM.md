# 完整源码贡献说明

这份贡献将 Codex Mobile Assistant 的 Windows、网页、网关与 Android / iOS 实现放在独立目录，供上游维护者评估复用。代码最初基于 `try2love/codex-mobile-bridge` 的 `cec08a0d20dac9a3f77374f01bec818e933345d7` 扩展；本次 PR 基于上游 v2.0.0 的 `1e2d3dd98228e50bc807e4259b1446a9f04d9910`。

上游 v2 已重构为多客户端工作台，并拥有自己的桌面与手机实现。此贡献保留完整扩展及依赖关系，不替换上游现有网关、网页、手机工程、工作流或入口，也不宣称所有功能都是上游缺失功能。当前适合以草稿 PR 评审目录位置、维护方式及值得后续移植的部分。

## 评审入口

下表给出功能与上游当前目录的对应关系，仅用于定位；当前没有跨目录导入或自动接入。

| 功能 | 本扩展入口 | 上游 v2 相关目录 |
| --- | --- | --- |
| Windows 双模式连接、托盘、凭据保存 | `固定域名入口.pyw`、`app/desktop_support.py` | `desktop/`、`bridge/features/network/` |
| Codex 会话、模型、权限、时间线 | `app/bridge/service.py`、`model.py`、`timeline.py` | `bridge/clients/codex/`、`bridge/features/sessions/` |
| 文件传输、图片、项目关联 | `app/bridge/attachments.py`、`files.py`、`mounts.py` | `bridge/features/workspace/` |
| 手机网页、旧 WebView 兼容 | `app/web/` | `web/features/`、`web/hosts/`、`web/layouts/` |
| Android 原生下载与提醒 | `mobile/android/` | `mobile/android/`、`mobile/shared/` |
| iOS 下载、分享、实验性后台提醒 | `mobile/ios/`、`mobile/ios/TaskMonitor/` | `mobile/ios/`、`mobile/shared/` |

这里的本扩展路径均相对于 `contrib/codex-mobile-assistant/`，上游路径相对于仓库根目录。

## 本次整理的范围

- 纳入已有本地 Windows 1.6.27、Android 1.0.24（25）、iOS 1.0.29（30）的源码，以及最新网关和网页改进。
- 清空个人域名和服务器默认值；旧设置仅保留用户明确保存的入口。
- 使用可移植的 cloudflared 下载路径、SHA-256 校验和 Windows 打包规格；保留修复启动等待与临时隧道重试的代码。
- 保留原始 MIT 许可、上游署名和第三方许可证；不包含运行数据、聊天记录、个人设置、密钥或安装包。旧界面截图没有纳入此贡献。
- 手机 Actions 文件作为手动工作流模板保存于 `scripts/`，不改变根目录 CI 或发布行为。

## 当前限制

该扩展仅接入 Codex Desktop；没有移植 v2 的 Claude、DeepSeek 或其他根目录功能。网关端口默认仍为 8787，不能与占用同端口的其他网关同时启动。数据在本扩展自己的 `app/.local/`，不迁移或共享上游配置。

iOS 后台提醒依赖 TrollStore 私有权限，属于实验功能；普通 Apple 签名安装不支持该权限组合。内部 Codex IPC 和手机后台行为都需要在目标版本验收。

本次只做源码整理与静态核对，未运行测试、构建、启动程序、浏览器或真机验证。`app/tests/` 和历史发布说明随源码保留，不能据此认定此快照已验证。现有上游 CI 不等于本扩展的兼容性验证。
