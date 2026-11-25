# Agent Note: macOS 自用构建使用独立本地身份

Status: implemented

## Problem

用户要求编译当前版本供自己的 Mac 使用。正常桌面构建要求持有正式发布私钥，因而在 ZCode 组件清单签名处停止；Apple 的 ad-hoc 签名已经成功，缺少的不是 Apple 开发者证书。

## Decision

在独立工作树或源码快照中构建 `Codex Mobile Bridge Local`，沿用已有的 `.test` 产品标识和 ad-hoc 签名流程。在主仓库忽略的 `.local/macos-local-signing/` 下保留独立 Ed25519 私钥，目录权限为 0700，私钥为 0600；已有密钥必须复用。只把对应公钥写入隔离源码的组件验证模块及打包公钥文件，重新冻结网关，并执行原有组件清单签名钩子。本地构建配置不进入正式构建入口。

Local 的 package name 使用 `codex-mobile-bridge-local`，由现有更新器的产品判断关闭官方自动更新；产品显示名为 `Codex Mobile Bridge Local`，默认 Electron 资料目录与正式版分离。版本保持当前源码版本。私钥只在 afterSign 包装钩子调用原有签名钩子时进入环境，随后删除，不进入编译、二维码检查、子进程、源码包或应用包。

主仓库的正式公钥、运行时信任根和发布配置保持原样。自用包仍要求有效清单、精确组件摘要、同产品身份、内核代码身份及原有对端认证。后续本地重建复用同一把本地密钥，不将本地身份用于签署正式更新。

使用 Codex 随附的 Python 构建时，冻结网关保留它的 `codex-rejected-cas.pem`，放在冻结的 `ssl` 模块同级。签名和进程能启动都不能证明 TLS 可用，替换前须用打包后的网关跑 HTTPS 信任检查。

就地更新沿用原应用和资料目录。先验证旧应用备份及同卷安装副本，记录网关状态和资料文件摘要，再退出目标应用，等其私有工作进程结束后交换应用包。新版以渲染进程、针对原资料目录的只读 `snapshot-stream` 和状态查询判断就绪；`management-stream` 按需启动。启动后复核状态和文件摘要，验收失败时用交换保留的旧包回退。

## Existing decisions reviewed

- [正式包与 Test 的发布身份](../../implemented/architecture/2026-11-07-zcode-production-local-identity.md)：部分重叠；复用其签名顺序和运行时校验，在隔离的自用构建中使用自己的信任根，不替换正式发布者。
- [私有启动器与网关绑定](../../implemented/architecture/2026-11-03-zcode-private-launcher-packaging.md)：部分重叠；启动器仍绑定最终网关摘要。
- ZCode 桌面管理、原生会话、官方远控、跨平台接入及云/移动端笔记：检索命中的是运行时行为或其他平台的验收，与本次自用打包身份无关，不修改其决定。

## Alternatives considered

继续要求正式发布私钥可以直接复用官方构建，但个人编译不应以取得发布者私钥为前提。跳过 afterSign 或只打 ad-hoc 签名可以快速生成安装包，却会缺失 ZCode 本机组件身份，不能作为完整自用包交付。直接替换主仓库公钥最省准备步骤，但容易污染后续正式发布和更新信任，因此使用隔离工作树。

更换构建 Python 可以避开本机运行时专用数据的收集问题，但也会更换解释器和依赖环境。对于已定位的单文件遗漏，随冻结包原样携带拒绝列表即可复用现有环境并保持证书校验规则。

## Testing

### 官方域名预设更新

同版本 Local 构建仅更新控制界面的官方 Origin，复用原本地 Ed25519 信任根和逐字节相同的冻结网关。签名、内嵌公钥、原生助手及打包后的 HTTPS 检查通过。在同卷覆盖更新时只退出控制器，运行中的网关 PID 和实例保持不变，控制器及渲染进程正常重启。安装前后两条配对记录、自启动状态及云配置、权限和会话文件摘要一致；未改旧域登录态，等官方入口切换后由用户重登。构建与本机验收在 `.tmp/local-domain-20261124/`。

### `2.2.2-preview.3` 就地更新

基于本地 `main` 的合并提交 `5692cfc3` 构建 arm64 应用。首包的签名及隔离启动通过，打包后的 HTTPS 验收失败；已安装的 `2.2.2-preview.1` 通过同一用例。最小冻结程序复现了 `SSLContext` 加载拒绝列表时的 `FileNotFoundError`。在隔离构建中补齐原文件并重签后，实际包通过本地组件清单、内嵌公钥、原生助手、架构、递归签名和 HTTPS 验收。HTTPS 用例覆盖公开入口、自定义 CA、不受信证书和主机名不匹配。6 项组件身份测试和 3 项签名钩子测试通过；扫描实际包的 981 个文件，未发现本地私钥。

`Codex Mobile Bridge Local.app` 已从 `2.2.2-preview.1` 更新到 `2.2.2-preview.3`。实际安装包的签名、渲染进程和只读网关工作进程通过检查。安装前后网关保持停止，自启动保持关闭，云配置和两台配对设备保留。`desktop.json`、`config.json`、`cloud-account.json`、`cloud-bindings.json`、`cloud-access.json`、`cloud-device-proofs.json`、`cloud-web-access.json`、`auth-sessions.json` 的摘要逐项比对一致。保留旧版应用包以便回退；未启动网关验证真实手机或云链路。构建与安装证据位于 `.tmp/local-client-update-20261124/`。

### `2.2.1-preview.1` 归档交付

`2.2.1-preview.1` 的 arm64 Local DMG 与 ZIP 均已生成。两个实际归档分别通过架构、代码签名、启动器绑定、本地密钥清单和 HTTPS 信任检查；额外读取冻结 PYZ，确认组件验证模块内嵌本地公钥。6 项组件身份测试和 3 项签名钩子测试通过，错误密钥仍被拒绝；原生冻结 Test 进程通过双向认证及不同构建接续夹具。

隔离启动实际 Local App，界面与内置 worker 加载成功，网关保持停止，官方更新状态为 `unsupported`。扫描 1,017 个应用文件，未发现本地私钥。日志、构建输入、本地包装钩子与验收记录保存在主仓库 `.tmp/build-macos-local-20261114/`；交付文件位于 `dist/desktop-local/`，本地构建源码及补丁随交付保留。

## Consequences

自用编译不再依赖正式发布者的私钥，同时保留组件身份边界。本地版没有 Apple Developer ID 或公证，跨机器分发仍可能触发 Gatekeeper。官方应用内更新不适用于此包，后续更新需要重新本地编译。丢失本地私钥会改变下一次构建的组件身份，不能视为同签名构建的无缝接续。打包及身份验收不代替真实 ZCode 聊天或手机连接验收。
