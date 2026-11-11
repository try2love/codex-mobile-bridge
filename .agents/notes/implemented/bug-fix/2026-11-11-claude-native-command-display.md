# Agent Note: Claude 原生命令记录与普通聊天分开展示

Status: implemented

## 问题

Claude CLI 的 `/exit` 会写入 caveat、command、stdout 三条原生 user 记录。普通文本投影既把内部 XML 显示在气泡里，也把 `isMeta` caveat 当成会话标题。实际 WSL 2.1.308 记录确认了相同 sessionId/promptId 和连续 parentUuid，但普通用户也可以输入相同标签，不能全局剥除 XML。

## 决策

Claude 历史模块统一识别完整原生命令链：CLI 入口、user 角色、meta caveat、同会话同 prompt、连续父子 UUID，以及完整命令标签必须同时成立。仅投影命令名/参数与原生输出；不修改 JSONL、消息 ID、时间、角色或普通消息内容。桌面 connector 只补充证明链所需的字符串字段，本机和独立远端 worker 复用同一函数。

列表标题优先保留原生 custom-title 和普通用户问题；只有命令的会话以首条已核验命令作为标题。meta、synthetic 记录不作为标题。分页从链中间开始时证据不足，保持原文，不能猜测转换。

## 备选方案

按标签正则全局删除最少代码，但会损坏用户实际发送的 XML。隐藏所有 user 命令记录会丢失用户操作与输出。直接重写原生日志会破坏原客户端身份与持久化约定，均不采用。

## 影响

完整链显示 `/exit` 和原生输出，普通消息保持原样，各宿主继续使用共享消息 UI。部分历史页缺少前置 caveat 时仍可能显示原文；这不被当成权限或接续成功，缺目录、未知权限和历史 429 仍按各自真实状态处理。

## 验证

读取真实 WSL 原生记录的结构与关系，未更改历史或发送模型请求。5 项 Python 定向检查通过，覆盖投影身份、原文保留、断链、本机/独立远端标题和既有 custom-title；1 项 Node 历史分页检查通过，确认必要字段透传而 cwd 不进入投影。最终安装包上的真实分页展示仍待集成验收。

## 既有记录审计

- [原生重命名](../feature/2026-11-04-native-session-renames.md)：部分重叠，原生 custom-title 优先级与不回写日志保持不变；本文只修复默认标题读取。
- [Claude 远端目录预检](2026-11-11-claude-remote-directory-preflight.md)：无决策冲突，目录和权限失败不因文本显示修复而绕过。
