# Webhook 通知

Webhook 将现有通知以 HTTP POST JSON 发到你配置的服务。无需安装额外插件；Mac、Windows、Linux 网关使用相同配置，Web、Android 与 iOS 可通过直连网关的“第三方通知 → Webhook”管理。电脑网关中的入口是“消息通知 → Webhook”。此功能从 v2.1.3 起提供。

## 配置与测试

1. 新建接收目标并命名，填写完整 HTTPS 地址，选择“自动识别”，或选择通用自定义、飞书、钉钉、Discord、Telegram。可添加多个同类机器人；旧版单个 Webhook 配置作为独立的默认目标保留。签名或密钥查询参数会保留；普通 HTTP 仅允许本机测试地址。
2. 在“高级设置”按服务要求填写请求头，例如 `{"Authorization":"Bearer YOUR_TOKEN"}`。请求头必须是 JSON 字符串键值对象。
3. 调整 JSON 正文模板，保存配置，再点击测试。保存不会发送通知；测试只发送已保存配置，不使用表单中未保存的修改。
4. 接收服务确认测试后，单独启用该目标并保存。每个目标可分别编辑、测试、停用或删除。聊天提醒继续由全局和单会话通知设置控制，网关入口与安全通知沿用各自开关。

通用 Webhook 默认模板：

```json
{
  "event": "{{event}}",
  "title": "{{title}}",
  "body": "{{body}}",
  "session_name": "{{session_name}}",
  "url": "{{url}}"
}
```

仅 JSON **字符串值**中的 `{{字段名}}` 会被替换，支持嵌套对象和数组。字段名、表达式或脚本不会执行。中文、引号和换行会正确编码为 JSON；通用正文最多 64 KiB，飞书正文最多 20 KiB，模板最多 16 KiB。

## 飞书机器人与签名校验

在飞书群中创建自定义机器人，将它的 Webhook 地址填入网关。选择“自动识别”时，官方飞书机器人地址会使用飞书协议；旧配置仍保留通用默认模板时，也会自动采用飞书文本格式。自定义模板不会被自动覆盖，可以点击“使用所选模板”替换。

```json
{
  "msg_type": "text",
  "content": {
    "text": "{{title}}\n{{body}}\n{{url}}"
  }
}
```

如果机器人启用了“签名校验”，在网关勾选同名选项并填写机器人生成的签名密钥，然后保存、测试。时间戳和签名由网关在每次发送时生成，不需要写入模板。密钥保存后不显示，留空保留原值；清除或更换接收目标时须重新填写。只关闭签名开关可以保留密钥供以后使用，但飞书机器人本身也必须允许未签名请求。

测试会检查飞书的业务返回码。格式、签名或时间偏差、关键词、IP 白名单和限流错误会显示对应提示；HTTP 200 不再直接视为成功。飞书确认接受后，请在群内核对通知。关键词和 IP 白名单仍在飞书端配置；模板文本应包含你设置的关键词，运行网关的电脑应符合 IP 白名单要求。详见[飞书自定义机器人官方说明](https://open.feishu.cn/document/client-docs/bot-v3/add-custom-bot)。

## 选择其他服务模板

选择服务后可以编辑它的默认 JSON；已有自定义正文会保留，点击“使用所选模板”可替换。展开“可用字段”可查看占位符及含义。各服务的接收地址与必填内容不同：

| 服务 | 接收地址 | 默认正文与设置 |
| --- | --- | --- |
| 通用自定义 | 自建 HTTPS 接收端 | 上文通用 JSON，可按接收端要求修改 |
| 飞书 | 机器人生成的 `open.feishu.cn` Webhook | `msg_type` 和 `content.text`；可选签名 |
| 钉钉 | 机器人生成的 `oapi.dingtalk.com/robot/send?access_token=…` | `msgtype` 和 `text.content`；可选加签 |
| Discord | 频道生成的 `discord.com/api/webhooks/…` | `content`，默认不触发 @ 提醒；正文最多 2000 字符 |
| Telegram | `https://api.telegram.org/bot<token>/sendMessage` | 必须在模板内填写 `chat_id`；`text` 最多 4096 字符 |

钉钉加签密钥填写在专用签名字段；网关生成毫秒时间戳与 URL 签名参数，不能套用飞书签名。Discord 发送会等待服务器确认，Telegram 检查 `ok`，钉钉检查 `errcode`；服务拒绝时不会显示发送成功。Telegram 这里使用的是 Bot API 的发送接口，不是接收更新的 `setWebhook` 接口。

官方协议：[钉钉发送接口](https://open.dingtalk.com/document/orgapp/custom-robots-send-group-messages)、[钉钉加签](https://open.dingtalk.com/document/robots/customize-robot-security-settings)、[Discord Webhook](https://docs.discord.com/developers/resources/webhook#execute-webhook)、[Telegram sendMessage](https://core.telegram.org/bots/api#sendmessage)。

| 字段 | 内容 |
| --- | --- |
| `event` | `task.completed`、`approval.required`、`gateway.started`、`gateway.address_changed`、`security.ip_blocked` 或 `test`；已开启的账号提醒使用 `account.*` |
| `event_id` | 供接收端去重的事件标识；同次事件重试时保持一致 |
| `title` / `body` | 现有通知标题与摘要，不包含完整聊天或模型回复 |
| `session_name` | 聊天名称；仅在开启“包含会话名称”时提供 |
| `url` | 对应聊天或当前网关的访问链接；无链接时为空 |
| `client` / `host` | 事件来源；不适用时为空 |
| `count` | 合并通知或事件计数，以字符串替换 |

## 已保存配置与投递

完整地址可能包含密钥，因此平常显示“已保存”与服务域名；地址输入框支持隐藏和查看，只有主动点击查看才读取完整已保存地址。普通配置刷新不会返回完整地址，请求头和签名密钥也不回传页面。地址或请求头留空会保留已保存值；使用对应清除选项删除。更换地址会清除旧请求头；更换地址或协议类型也不自动沿用原签名密钥，需要重新填写。请将认证信息放在专用配置中，不要写进可回读的正文模板。

每个 Webhook 目标默认关闭，复用既有通知策略，并拥有独立的去重记录与失败重试。旧版单目标配置无需迁移；新增目标不会补发已完成的聊天历史。停用、删除或更换某个目标只会取消该目标的待发送重试。被动通知不会启动、接管或切换聊天。

通用接收端 HTTP 2xx 只表示请求被接受；内置服务按各自协议判断成功。仍需在下游确认是否收到。超时可能发生在接收端已经处理之后，因此不能保证严格只发送一次；接收端可按 `event_id` 去重。请求不会跟随重定向。除飞书和钉钉之外的动态签名或非 JSON 协议，仍需由你自己的适配服务转换。

## English

**Available since v2.1.3:** configure **Push alerts → Webhook** on Web/mobile, or **Notifications → Webhook** in the desktop gateway. Settings are shared across macOS, Windows and Linux; Android/iOS use the gateway's Web interface. Shared-relay configuration permissions are unchanged.

Create and name each destination, then set its HTTPS endpoint, optional JSON string-to-string headers and JSON body template. You can add multiple bots of the same service; an existing single Webhook remains available as the default destination. Save before testing: a test uses saved settings only, and saving does not send anything. Enable each destination after confirming receipt; you can edit, test, disable or delete it independently. Existing global/per-chat, gateway-entry, security and account notification preferences remain in control.

Use `{{event}}`, `{{event_id}}`, `{{title}}`, `{{body}}`, `{{session_name}}`, `{{url}}`, `{{client}}`, `{{host}}` and `{{count}}` inside JSON string values. No scripts, expressions or key substitution are supported. Templates are limited to 16 KiB and rendered bodies to 64 KiB. Chat names require the existing title opt-in; full conversations and model replies are not included.

Saved endpoints stay masked during ordinary configuration reads. Use the address visibility button to retrieve and view the full saved URL explicitly. Headers and signing secrets are never returned to the UI. Blank inputs retain saved values; explicit clear controls remove them. Changing the endpoint clears old headers unless replacements are supplied. Keep credentials out of the readable body template. HTTPS is required except for loopback HTTP tests, and redirects are rejected.

**Feishu bots:** Auto-detect recognizes official Feishu custom-bot endpoints, or select Feishu explicitly. The unchanged generic default becomes the Feishu text format above; custom templates are preserved. Use the selected-template button to replace a custom template explicitly. If the bot requires signature verification, enable it and enter its signing secret in the dedicated field. The gateway generates a fresh timestamp and signature on every attempt. Saved secrets are masked; a blank field preserves the secret for the same target, and changing the endpoint or protocol requires a replacement. Feishu bodies are limited to 20 KiB. Tests validate the business response code and report format, signature/time, keyword, IP-whitelist and rate-limit failures. See the [official guide](https://open.feishu.cn/document/client-docs/bot-v3/add-custom-bot).

**Other presets:** choose Custom Webhook, DingTalk, Discord or Telegram and edit the supplied JSON. Custom text is preserved until you explicitly apply the selected template. The expandable field reference lists every supported placeholder. DingTalk uses `msgtype`/`text.content` and supports optional signing with its own millisecond timestamp algorithm. Discord uses `content` (up to 2000 characters), disables mentions by default and waits for message confirmation. Telegram uses the Bot API `/bot<token>/sendMessage` endpoint; fill `chat_id` in the template and keep `text` within 4096 characters. DingTalk requires `errcode: 0`; Telegram requires `ok: true`. These are outgoing notification presets, not incoming bot-update subscriptions.

Delivery fans out to enabled destinations and reuses existing policies, with separate deduplication and retries for each destination. It does not activate chats or replay completed history. Disabling, deleting or changing one destination cancels only its pending retries. Generic HTTP 2xx confirms receiver acceptance; presets use service-specific success responses. Confirm downstream receipt and use the stable `event_id` to deduplicate retries after ambiguous timeouts. Dynamic signatures beyond Feishu/DingTalk and non-JSON protocols require a separate adapter.

## 完成通知中的回答预览

通知设置提供“包含最后回答预览”，默认关闭。打开后，已完成事件优先使用当前已读取的最后一条助手回答，合并空白并截取前 280 个字符；缺少最终回答时保持普通完成提醒。过程说明、待回答问题、失败事件和等待审批提醒不作为回答预览。

预览可能出现在手机锁屏、系统通知和已配置的第三方通知服务中。发送前会重新检查开关；关闭后尚未发送的通知恢复通用文案，已送达的内容无法撤回。预览不额外激活或读取聊天，不写入完成去重账本；启用后的手机通知入箱会按既有通知保留规则保存通知正文。
