# Agent Note: 新建 Codex 聊天保留原生无名称状态

Status: implemented

## 问题

新建表单预填并强制提交“新聊天”，后端无条件调用 thread/name/set，因此写入了永久原生名称。直接去掉改名也不完整：实际 0.167 新建的空聊天虽可读取元信息，却没有可供下一连接恢复的 rollout。

## 决策

聊天名称默认留空，仍允许填写至多 120 字。Codex App、本机/SSH CLI、WSL 复用一个仅用于刚创建线程的保存步骤：有名称沿用 thread/name/set；无名称调用 thread/section/move，保留新线程初始 sectionId，并由原生接口物化与 flush 空历史。随后仍执行原有完整读取、项目归属和模型交接检查。创建请求的身份与未知结果去重规则不变。

该步骤不能用于已有聊天，也不能伪造名字、消息、数据库或原生历史。原生无名称状态保留 App/TUI 后续命名机会；裸 app-server 不保证主动调用模型生成标题，网关不新增付费标题请求。

依据固定版本源码 [0.167 thread_manager.rs](https://github.com/openai/codex/blob/086ccc833bc9cf01024ad2dd6af7dbd421b6bf9a/codex-rs/core/src/thread_manager.rs#L1174)：显式放置先确保 rollout 物化再 flush。sectionId 为 null 是原生支持的无分区状态。官方 0.164.0 固定源码也具有相同接口和物化步骤；未在旧二进制执行。本地存储需有原生 SQLite state DB。原生不支持该接口或持久化失败时仍保留未知创建回执，不改名回退、不重复新建。

## 备选方案

只跳过 thread/name/set 会产生跨连接无法恢复的空聊天；设置空白或固定占位名字仍违反原生命名意图；发送假消息会改变历史并可能计费。均未采用。暂存前端草稿到首条消息才新建会改变现有空聊天和项目交接流程，本次不扩大改造。

## 影响与验证

新隔离 home 中用实际独立 CLI 0.167.0 完成 start、无分区放置、正常退出、另一进程 resume：同 ID、name=null、turns=[]，rollout 已存在。App 内置 0.167.0-alpha.4.1 通过修改后的 create_empty 再跨进程恢复，得到同样结果。未调用 turn/start、正式凭据或付费模型。

28 项创建及 CLI 针对检查通过（其中一项仅 Windows 专属检查在 Mac 跳过）；28 项 WSL 新建、项目和交接检查通过。真实浏览器确认名称初始为空、非必填、空值有效。三端最终构建与完整页面创建仍待验证。
