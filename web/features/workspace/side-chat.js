'use strict';

// Only navigate native children here. Their composer, requests, attachments and
// lifecycle all use the normal session controller, never a second chat stack.
class SideChatPanel {
  constructor(workbench, session, tab) {
    Object.assign(this, {workbench, session, tab});
    this.creationId = uuid();
    const n = (...args) => workbench.node(...args);
    this.toolbar = n('div', 'wb-toolbar');
    this.createButton = workbench.button('新建侧边聊天', () => this.create());
    this.refreshButton = workbench.button('↻', () => this.load(), '刷新');
    this.toolbar.append(this.createButton, this.refreshButton);
    this.status = n('p', 'wb-status'); this.status.setAttribute('role', 'status');
    this.list = n('div', 'wb-agents-list');
    tab.body.append(this.toolbar, this.status, this.list);
    this.load();
  }
  url() { return this.workbench.endpoint(this.session, 'side-chat') + '&view=native'; }
  relabel() { this.paint(); }
  async load() {
    if (this.disposed || this.loading) return;
    const controller = this.controller = new AbortController(); this.loading = true;
    this.refreshButton.disabled = true; this.status.textContent = BridgeI18n.t('正在读取聊天…');
    try {
      const result = await this.workbench.request(this.url(), undefined, controller.signal);
      if (this.disposed) return;
      this.chats = result.chats || []; this.paint();
    } catch (error) {
      if (!this.disposed && !controller.signal.aborted) this.status.textContent = BridgeI18n.t(error.message);
    } finally {
      this.loading = false; this.refreshButton.disabled = false;
    }
  }
  paint() {
    if (this.disposed || !this.chats) return;
    const n = (...args) => this.workbench.node(...args), scroll = this.list.scrollTop;
    this.list.replaceChildren(); this.status.textContent = '';
    for (const chat of this.chats) {
      const row = n('div', 'wb-agent-card wb-side-chat-row'), title = chat.title || '未命名聊天';
      const open = this.workbench.button('', () => this.open(chat), title);
      open.append(this.workbench.raw(n('strong', '', title)));
      if (chat.archived) open.append(n('small', '', '已归档'));
      const more = this.workbench.button('⋯', () => this.workbench.openSessionMenu?.(chat, more), '聊天菜单');
      row.append(open, more); this.list.append(row);
    }
    if (!this.chats.length) this.list.append(n('p', 'wb-empty', '此聊天还没有侧边聊天。'));
    this.list.scrollTop = scroll;
  }
  open(chat) {
    if (!this.disposed) return this.workbench.openChat(chat.id, chat.host || this.session.host);
  }
  async create() {
    if (this.disposed || this.creating) return;
    this.creating = true; this.createButton.disabled = true;
    this.status.textContent = BridgeI18n.t('正在创建侧边聊天…');
    try {
      const result = await this.workbench.request(this.url(), {action: 'create', creationId: this.creationId});
      this.creationId = uuid();
      if (this.disposed) return;
      await this.load(); await this.open(result);
    } catch (error) {
      if (!this.disposed) this.status.textContent = BridgeI18n.t(error.message);
    } finally {
      this.creating = false; this.createButton.disabled = false;
    }
  }
  // Closing a view never sends stop/archive/delete and never destroys history.
  dispose() { this.disposed = true; this.controller?.abort(); }
}
