# Web 与手机共享界面

`index.html` 是浏览器入口，手机外壳也加载同一页面。脚本仍按 HTML 中的顺序加载，共享现有全局对象。

| 目录 | 职责与入口 |
| --- | --- |
| `shell/` | `app.js` 组织页面启动、网关请求与模块交互；`style.css` 提供基础布局。 |
| `features/chat/` | 会话列表、消息时间线、输入与附件、权限和工作模式。 |
| `features/accounts/` | 官方账号、API 接入、额度及客户端账号管理。 |
| `features/clients/` | 应用切换、启用管理与 Claude/DeepSeek 会话界面。 |
| `features/workspace/` | 文件与下载、Git、终端、侧边聊天、子任务和分屏工作台。 |
| `features/settings/` | `presentation.js` 管理本地外观偏好。 |
| `shared/` | 翻译、Markdown、图片查看器与共享视觉样式。 |
| `client-icons/`、`vendor/` | 客户端原始图标和有许可证记录的第三方浏览器资源。 |

## 静态地址契约

`assets.json` 将现有公开地址映射到磁盘文件，例如 `/app.js` 对应 `shell/app.js`。后端仅发布 `bridge/api/assets.py` 固定白名单允许的入口，以及原有受限的字体资源；清单本身不会扩大公开范围。搬移源文件时更新清单；HTML 中的公开地址、查询版本和脚本顺序保持稳定。

桌面网关通过本地文件引用 `shared/i18n.js` 和 `features/accounts/`；这些路径还需要出现在根 `package.json` 的打包清单中。图片地址相对于页面，CSS 地址相对于公开样式 URL；不要根据脚本所在目录改写浏览器地址。

从仓库根目录运行 `npm run test:desktop`。该命令递归发现 `tests/` 和 `mobile/tests/` 下的 `*.test.js`、`*.test.cjs`、`*.test.mjs`，无需手动添加测试清单。资源映射与桌面本地引用由 `tests/frontend-assets.test.cjs` 验证；相关功能测试按实际磁盘路径读取源码。

`tests/browser/*.browser.js` 是依赖真实页面和隔离网关夹具的浏览器检查，不属于 Node 测试；需要通过对应浏览器驱动单独执行，不能将仅加载脚本视作检查通过。
