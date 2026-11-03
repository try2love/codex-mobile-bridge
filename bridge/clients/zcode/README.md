# ZCode 桌面接入

macOS 和 Windows 网关扫描已安装的 ZCode。普通启动时可在网关或手机管理应用、查看本机保存的聊天。停用手机访问后，电脑上的应用和任务继续运行。退出与强制退出是独立操作；Windows 正常退出使用 ZCode 自己的托盘菜单，请在可交互的桌面上使用。

普通本机接入不会启动调试端口。保存的聊天标记为只读，不能通过这条通道发送消息或处理审批。连接后以原 App 的会话协议、项目目录和原生订阅核对控制权限。安装版本用于展示，模型选择、Skill 和附件依照原 App 当次提供的接口开放。切换已保存账号前，请先停用手机访问并退出 ZCode。

## 旧版显式会话原型

以下说明仅适用于单独配置的开发原型。原 App 负责执行、模型、账号和工作区。设计和证据见 [Agent Note](../../../.agents/notes/implemented/feature/2026-10-31-zcode-native-session-prototype.md)。

## 使用边界

- 仅使用专门的原生测试聊天，明确保存其 sessionId 和绝对 workspace 路径；本脚本不创建聊天。
- 仅连接已由正常 Launch Services 启动、带 loopback 调试端口的原 App。禁止将调试端口开放到局域网或公网；它不是网关服务端口。
- 通过原 App 的 SessionDataLayer 租约共享同一会话投影，可与桌面同时查看；不另行 subscribe 或抢回订阅。
- 只读不自动启动或恢复冷会话。原 App/host 世代变化后，旧绑定停止写入。
- 使用新请求 UUID 执行新操作。超时或结果未知时只查回执，不能改 UUID 重发同一操作。
- 真实测试前必须核验原生会话执行器 `snapshot.config.model` 为 `GLM-5.3-Flash`、`snapshot.config.thought` 为 `low`。不能仅以原 App 下拉框或 Bridge 显示值为准；两者可能尚未同步。配置不符时停止发送，先同步并再次核验。已通过且源码未变的测试不重复消耗免费额度。

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

`history` 只读原生已有运行时；`detail` 需先显式 attach，随后读取原 App 已同步的快照。适配器无自动轮询或重连计时器。结束原型实测时，先核验原 App 任务，再正常退出关闭临时调试端口；不要强杀。

## 针对性检查

```sh
node --test tests/zcode-prototype.test.mjs
python3 -B -m unittest discover -s tests -p test_zcode_prototype.py -v
python3 -B scripts/check-boundaries.py
```

## Mac Test 手机入口

私有 `desktop-sessions/settings.json` 的 `zcodePrototype` 配置启用绑定的原生测试聊天，通过现有共享聊天页面和鉴权路由访问。模型、账号和新建聊天仍由原 App 管理。

Mac Test 已验证 HTTPS 发送、原生附件和回复，以及局域网读取同一最新结果；窄屏布局使用浏览器模拟验证，不等于 iPhone 真机验收。Windows/Linux、SSH、任意冷会话和 App 更新迁移尚未验证，不能直接将临时调试入口作为正式接入发布。具体证据与限制见 [手机 Test 记录](../../../.agents/notes/implemented/feature/2026-10-31-zcode-mobile-test.md)。
