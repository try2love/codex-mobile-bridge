# ZCode 原生会话协议原型

这是供开发验证的显式入口，尚未注册到正式网关或手机 UI。当前只验证 macOS 上的 ZCode 3.17.1 与 Node 26.12.1。原 App 负责执行、模型、账号和工作区；Bridge 不另起 CLI。

设计和证据见 [Agent Note](../../../.agents/notes/implemented/feature/2026-10-31-zcode-native-session-prototype.md)。

## 使用边界

- 仅使用专门的原生测试聊天，明确保存其 sessionId 和绝对 workspace 路径；本脚本不创建聊天。
- 仅连接已由正常 Launch Services 启动、带 loopback 调试端口的原 App。禁止将调试端口开放到局域网或公网；它不是网关服务端口。
- 同一 renderer 的原生 UI 与此原型不能同时持有同会话订阅。先完成协议验证并 detach，再从桌面打开同一聊天做视觉核验；订阅失效不自动抢回。
- 只读不自动启动或恢复冷会话。原 App/host 世代变化后，旧绑定停止写入。
- 使用新请求 UUID 执行新操作。超时或结果未知时只查回执，不能改 UUID 重发同一操作。

## 手动入口

在仓库 `.tmp/zcode-state/binding.json` 保存本机配置，不提交 Git：

```json
{
  "application": "/Applications/ZCode.app",
  "port": 17742,
  "workspace": "/absolute/path/to/test-project",
  "sessionId": "sess_existing-native-test-session"
}
```

```sh
python3 -B scripts/zcode-prototype.py --state .tmp/zcode-state attach
python3 -B scripts/zcode-prototype.py --state .tmp/zcode-state detail
python3 -B scripts/zcode-prototype.py --state .tmp/zcode-state send < .tmp/send.json
python3 -B scripts/zcode-prototype.py --state .tmp/zcode-state receipt < .tmp/receipt.json
python3 -B scripts/zcode-prototype.py --state .tmp/zcode-state detach
```

`send.json` 包含 UUID `id`、`text`，可选 `projectFiles` 为项目内相对文件路径列表。它复用现有文件访问边界，并通过原生附件上传协议提交，不生成文件提示词。最多 10 个文件、总计 20 MiB。

`stop` 的 JSON 为 `{ "id": "UUID", "executionId": "当前 control.activeWorks 中的标识" }`。

`respond` 的 JSON 为 `{ "id": "UUID", "interactionId": "当前待决项标识", "answer": { "optionId": "原生选项" } }`。选择题可用原生 `action` / `content` 结构，权限项只能使用原生提供的选项。

`receipt.json` 为 `{ "commandId": "原请求 UUID" }`。核对不会重放操作，也不会自动覆盖本地 unknown 记录。

`history` 只读原生已有运行时；`detail` 需先显式 attach，按需重同步快照。无自动轮询或重连计时器。完成后重新核验原 App 任务，再正常退出关闭临时调试端口；不要强杀。

## 针对性检查

```sh
node --test tests/zcode-prototype.test.mjs
python3 -B -m unittest discover -s tests -p test_zcode_prototype.py -v
python3 -B scripts/check-boundaries.py
```

尚未验证 Windows/Linux、移动端、HTTPS、应用更新或重启恢复。正式接入还需要独立原 host 连接与现有共享 UI 的能力投影，不能直接将此调试入口对外发布。
