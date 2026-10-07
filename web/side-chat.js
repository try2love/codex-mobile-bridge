'use strict';

// Native side chats need Desktop's fork/discard lifecycle. The follower IPC
// currently cannot create them; never substitute a persisted thread or sidecar.
class SideChatPanel {
  constructor(workbench, session, tab) {
    const n=(...args)=>workbench.node(...args);
    const content=n('div','wb-side-unavailable');content.setAttribute('role','status');
    content.append(n('h3','','原生侧边聊天暂不可用'),
      n('p','','当前桌面连接暂不支持创建临时侧边聊天，请先在 Codex 电脑端使用。'),
      n('p','muted','侧边聊天基于当前会话，关闭或失效后无法恢复。'));
    tab.body.append(content);
  }
}
