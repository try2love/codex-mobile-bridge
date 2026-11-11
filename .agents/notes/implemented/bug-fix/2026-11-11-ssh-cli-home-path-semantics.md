# Agent Note: SSH CLI 配置目录使用目标端路径语义

Status: implemented

## 问题

Windows 网关把 Linux SSH 握手返回的 `/home/.../.codex` 交给本机 `Path.is_absolute()`，因而在创建、恢复、归档及创建配置查询之前错误拒绝合法的远端配置目录。旧夹具使用网关自己的临时目录，未覆盖跨系统路径。

## 决策

Codex CLI 的运行时目录统一经 `_runtime_home` 核验。本机保留本机 `Path`，SSH 使用 `PurePosixPath`，且后续握手必须匹配第一次核验的远端 home。模型目录助手把任意 `PurePath` 显式序列化为原始目标路径，不通过本机解析或映射。

此修复保留 SSH 登录 shell 的 `CODEX_HOME` 选择、原主机、原 provider、原 cwd 和已安装运行时。只读路径核验不启动或恢复线程。

## 备选方案

只删除 `is_absolute()` 可以绕过误拒绝，但会接受相对路径且遗漏目录换代；依据网关系统分支也无法描述 SSH 目标。因此保留绝对路径与 home 身份比较，只更换路径解释器。

## 验证

`tests/test_cli_remote_home.py` 在 CLI 模块的本机路径类型替换为 `PureWindowsPath` 时覆盖 Linux home 的 resume、create、archive 和 creation_context；相对路径、Windows 路径与 home 变化均在写入前拒绝。模型助手同时覆盖 `PurePosixPath` JSON 传输。

真实 Windows 通过已保存的 88Server SSH 别名，以已核验源码的产品 proxy 通路连接现存原生服务，initialize 返回 `/home/sp/.codex`；同一 Windows 进程中的旧 `Path.is_absolute()` 返回 false，新 `_runtime_home()` 接受并固定该 POSIX home。独立 socket peer 元数据对应目标用户的 0.160 二进制，握手版本也为 0.160。该检查未启动/退出 daemon、读取或接续固定会话、发送模型请求。原始证据为 `.tmp/feedback-20261111-10/root-windows-ssh-handshake.json` 和 `root-ssh-peer.json`；它证明实际跨系统握手与错误路径判断，不代替完整会话恢复或该版本排空验收。

## 影响

跨系统接入不会因网关路径语义误判而失败；远端 home 保持独立，不借用本机账号。已有 SSH 模型助手与会话协议保持不变。
