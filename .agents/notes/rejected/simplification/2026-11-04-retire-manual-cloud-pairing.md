# Agent Note: 云设备连接统一为自动目录与桌面开关

Status: rejected — 用户明确项目尚未上线，无需保留旧数据库或绑定数据，保留历史兼容的前提不成立。

## Problem

手机已使用同账号自动目录，但桌面 IPC、连接器仍公开 candidates/approve/reject/revoke，Server 仍暴露手动请求与旧 approve。两套路径的关闭语义不同，旧分支删除可信密钥，现行开关则保留密钥。继续维护会扩大授权接口和误用风险。

## Proposal

三端只保留自动目录、独立加密证明以及带 revision 的桌面 access 开关。删除旧云配对 IPC、分派、界面死分支与 Server 请求/批准入口；直接连接的 QR 配对独立保留。服务器历史迁移不可改写，升级保留设备 ID、公钥、可信 pin 和撤销事实；本地旧名称的证明文件仍是当前持久化文件，不重命名或自动删除。

## 记录归属

检索 cloud、pairing、自动发现后的归属：[同账号目录](../../implemented/feature/2026-11-03-account-device-auto-discovery.md)部分重叠，继续持有自动发现与可逆开关；[桌面手动批准](../../implemented/feature/2026-11-03-desktop-approved-cloud-pairing.md)的加密证明仍有效，手动请求部分由本篇取代，保留历史理由并互链。cloud logout、listener injection 和通知文档仅关联账号/监听/通知边界，保留各自决定；其他 provider 配对无关。

## Alternatives considered

- 保留旧入口能让旧 App/桌面继续调用，但两套授权语义已冲突，且用户明确要求桌面也清理；选择协同升级，不增加 fallback。
- 删除历史表和本地证明文件能减少存储名称，但会破坏已有密钥关系和升级证据；仅退役执行路径，保留历史数据和明确的读取迁移。

## Acceptance criteria

旧 IPC/HTTP 操作被拒绝，自动目录、首次安全码核对、离线关闭、恢复保留密钥与迟到开启封锁继续通过。测试改走当前接口，保留原有密钥和账号隔离断言；补充旧入口拒绝及历史迁移回归。同步三端文档与笔记。

## Risks

旧版本客户端必须升级；不能把加密协议中的 pair 证明消息误删成旧 HTTP 配对请求。当前关闭失败仍保留本地封锁，不自动重试未知开启或修改真实设备权限。

后续决定见[未上线云流程收敛](../../implemented/simplification/2026-11-04-prerelease-cloud-contract-cleanup.md)。本篇保留被否方案的理由，避免再次把数据迁移当作本轮清理的前提。
