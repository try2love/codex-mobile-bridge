# 消息管理 / Message management

在聊天的“更多 → 消息管理”中查看待发送队列和未确认记录。存在待处理记录时，输入区上方也会提供入口。Web、Android 和 iOS 使用同一页面。

- **调整顺序**：拖动消息左侧手柄，或使用“上移 / 下移”。仅尚未发送、由网关持有的消息可以排序；顺序保存在网关，后续发送按此顺序执行。
- **撤回**：从待发送队列移除。已经交给客户端的消息无法通过此操作撤销。
- **编辑**：在独立编辑区修改正文，保存后保持原排队位置和原附件；聊天输入框原有草稿、引用和附件保持不变。消息已开始发送时不能再修改，编辑内容会保留。
- **未确认记录**：进入会话时按原发送 ID 核对结果。已确认的记录可解除旧版客户端阻塞；结果未知或查不到时保留。检查聊天记录后，可显式忽略该记录，操作不会撤销、重发或删除聊天消息。
- **断网和多个页面**：撤回、排序或保存编辑的结果不明确时不会自动重试。其他页面修改了队列或编辑记录时，刷新或重新打开后再操作。

断开连接时，仍可编辑草稿、收起输入框、调整工具栏，以及查看本机上次同步的上下文用量、模型和额度。点击「存入待发」后，这条消息会出现在输入区上方。交给网关之前可编辑或撤回，也可以继续写下一条草稿。手机重新打开后会恢复两者。重新连到同一网关、原账号的会话可以发送时，页面按顺序投递待发消息。回执中断时，页面会显示「发送结果待确认」，并使用原消息编号核对。尚未选择「存入待发」的草稿会留在输入框。

网关明确拒绝、尚未发送的消息可在卡片上编辑或移除。保存编辑会在原排队位置重新待发，并保留附件和 Skill；其他待发消息和输入框草稿不变。发送结果未知时继续核对，不会因为查不到回执而自动重发。

Codex Desktop、本机 CLI、SSH CLI 和 WSL 的网关队列支持管理。Claude / DSH 已交给原生客户端的队列仍由原生客户端管理；页面会说明限制，不提供无效的排序。当前设备保存的未确认记录可以单独处理。

在本机 Codex App 选择“完成后发送”的消息会单独显示在“Codex 中待发送”。打开消息管理时会自动刷新，它们继续由 Codex 发送，编辑、排序和撤回请在电脑端操作。

旧版 Android App 请在原电脑连接中选择“更多 → 刷新页面”，再打开会话；只切回前台可能仍在使用旧页面。它只核对该电脑、账号、接入及会话的记录，不会清空其他草稿，也不会自动发送。

## English

Open **More → Message management** in a chat. A shortcut also appears above the composer when records need attention. Web, Android and iOS share this page.

- Drag a queued message by its handle, or use **Move up / Move down**. The gateway persists the order and uses it for dispatch.
- **Withdraw** removes an unsent message. It cannot undo a message already handed to the client.
- **Edit** changes a queued message in place, keeping its position and attachments. The separate editor preserves your chat draft and quotes. If dispatch has started, the edit is kept but cannot be applied.
- Old unconfirmed records are checked against their original submission IDs. Unknown or missing results remain visible for explicit dismissal; dismissal never resends or deletes chat messages.
- Ambiguous operations are not retried automatically. Conflicts between pages require refreshing or reopening the manager.

While disconnected, you can edit your draft, collapse the composer, rearrange its tools, and view the last synced context usage, models, and quota. Choose **Save to outbox** to keep a message on this device. You can edit or withdraw it before handoff and start another draft. Both survive reopening the mobile app. When the same gateway and session account are available to send, the app delivers pending messages in order. If a delivery receipt is interrupted, the app checks the original message ID. Text left in the composer stays a draft.

Messages explicitly rejected before sending can be edited or removed from their cards. Saving an edit returns the message to its original queue position, keeping its attachments and Skills. Other pending messages and your composer draft stay intact. Unknown results remain under receipt verification; a missing receipt never triggers an automatic resend.

Gateway-owned Codex Desktop, local CLI, SSH CLI and WSL queues are supported. Claude / DSH native queues remain managed by their native clients; unsupported actions are not offered. Device-local unconfirmed records can still be managed.

Messages queued in the local Codex App appear under **Queued in Codex** and refresh while Message management is open. Codex remains responsible for sending them. Use the desktop app to edit, reorder, or withdraw them.

In older Android apps, choose **More → Refresh page** in the existing computer connection, then reopen the chat. Returning to the foreground alone may keep the old page running. No app reinstall or automatic resend is required.
