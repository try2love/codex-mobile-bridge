# Agent Note: macOS 自用构建使用独立本地身份

Status: implemented

## Problem

用户要求编译当前版本供自己的 Mac 使用。正常桌面构建要求持有正式发布私钥，因而在 ZCode 组件清单签名处停止；Apple 的 ad-hoc 签名已经成功，缺少的不是 Apple 开发者证书。

## Decision

本次交付在独立工作树中构建 `Codex Mobile Bridge Local`，沿用已有的 `.test` 产品标识和 ad-hoc 签名流程。在主仓库忽略的 `.local/macos-local-signing/` 下生成并保留独立 Ed25519 私钥，目录权限为 0700，私钥为 0600；已有密钥必须复用。只把对应公钥写入该工作树的组件验证模块及打包公钥文件，重新冻结网关，并执行原有组件清单签名钩子。此处记录一次自用构建，没有新增正式构建入口。

Local 的 package name 使用 `codex-mobile-bridge-local`，由现有更新器的产品判断关闭官方自动更新；产品显示名为 `Codex Mobile Bridge Local`，默认 Electron 资料目录与正式版分离。版本保持当前源码版本。私钥只在 afterSign 包装钩子调用原有签名钩子时进入环境，随后删除，不进入编译、二维码检查、子进程、源码包或应用包。

主仓库的正式公钥、运行时信任根和发布配置保持原样。自用包仍要求有效清单、精确组件摘要、同产品身份、内核代码身份及原有对端认证。后续本地重建复用同一把本地密钥，不将本地身份用于签署正式更新。

## Existing decisions reviewed

- [正式包与 Test 的发布身份](../../implemented/architecture/2026-11-07-zcode-production-local-identity.md)：部分重叠；复用其签名顺序和运行时校验，在隔离的自用构建中使用自己的信任根，不替换正式发布者。
- [私有启动器与网关绑定](../../implemented/architecture/2026-11-03-zcode-private-launcher-packaging.md)：部分重叠；启动器仍绑定最终网关摘要。
- ZCode 桌面管理、原生会话、官方远控、跨平台接入及云/移动端笔记：检索命中的是运行时行为或其他平台的验收，与本次自用打包身份无关，不修改其决定。

## Alternatives considered

继续要求正式发布私钥可以直接复用官方构建，但个人编译不应以取得发布者私钥为前提。跳过 afterSign 或只打 ad-hoc 签名可以快速生成安装包，却会缺失 ZCode 本机组件身份，不能作为完整自用包交付。直接替换主仓库公钥最省准备步骤，但容易污染后续正式发布和更新信任，因此使用隔离工作树。

## Testing

`2.2.1-preview.1` 的 arm64 Local DMG 与 ZIP 均已生成。两个实际归档分别通过架构、代码签名、启动器绑定、本地密钥清单和 HTTPS 信任检查；额外读取冻结 PYZ，确认组件验证模块内嵌本地公钥。6 项组件身份测试和 3 项签名钩子测试通过，错误密钥仍被拒绝；原生冻结 Test 进程通过双向认证及不同构建接续夹具。

隔离启动实际 Local App，界面与内置 worker 加载成功，网关保持停止，官方更新状态为 `unsupported`。扫描 1,017 个应用文件，未发现本地私钥。日志、构建输入、本地包装钩子与验收记录保存在主仓库 `.tmp/build-macos-local-20261114/`；交付文件位于 `dist/desktop-local/`，本地构建源码及补丁随交付保留。

## Consequences

自用编译不再依赖正式发布者的私钥，同时保留组件身份边界。本地版没有 Apple Developer ID 或公证，跨机器分发仍可能触发 Gatekeeper。官方应用内更新不适用于此包，后续更新需要重新本地编译。丢失本地私钥会改变下一次构建的组件身份，不能视为同签名构建的无缝接续。打包及身份验收不代替真实 ZCode 聊天或手机连接验收。
