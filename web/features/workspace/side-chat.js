'use strict';

// Only navigate children here. Their composer, requests, attachments and
// lifecycle all use the normal session controller, never a second chat stack.
class SideChatPanel {
  constructor(workbench, session, tab) {
    Object.assign(this, {workbench, session, tab});
    this.creationId = uuid();
    this.temporaryCreationId = uuid();
    const n = (...args) => workbench.node(...args);
    this.toolbar = n('div', 'wb-toolbar');
    this.createButton = workbench.button('新建侧边聊天', () => this.create());
    this.temporaryButton = workbench.button('新建临时侧聊', () => this.create(true));
    this.refreshButton = workbench.button('↻', () => this.load(), '刷新');
    this.toolbar.append(this.createButton, this.temporaryButton, this.refreshButton);
    this.help = n('p', 'wb-status', '临时侧聊沿用创建时的上下文；结束或网关重启后清除。');
    this.status = n('p', 'wb-status'); this.status.setAttribute('role', 'status');
    this.list = n('div', 'wb-agents-list');
    tab.body.append(this.toolbar, this.help, this.status, this.list);
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
      const more = chat.temporary ? this.workbench.button('结束', () => this.end(chat), '结束临时侧聊') :
        this.workbench.button('⋯', () => this.workbench.openSessionMenu?.(chat, more), '聊天菜单');
      row.append(open, more); this.list.append(row);
    }
    if (!this.chats.length) this.list.append(n('p', 'wb-empty', '此聊天还没有侧边聊天。'));
    this.list.scrollTop = scroll;
  }
  open(chat) {
    if (!this.disposed) return this.workbench.openChat(chat.id, chat.host || this.session.host);
  }
  async create(temporary = false) {
    if (this.disposed || this.creating) return;
    this.creating = true; this.createButton.disabled = this.temporaryButton.disabled = true;
    this.status.textContent = BridgeI18n.t('正在创建侧边聊天…');
    try {
      const key = temporary ? 'temporaryCreationId' : 'creationId';
      const result = await this.workbench.request(this.url(), {action: temporary ? 'create-temporary' : 'create', creationId: this[key]});
      this[key] = uuid();
      if (this.disposed) return;
      await this.load(); await this.open(result);
    } catch (error) {
      if (!this.disposed) this.status.textContent = BridgeI18n.t(error.message);
    } finally {
      this.creating = false; this.createButton.disabled = this.temporaryButton.disabled = false;
    }
  }
  async end(chat) {
    if (this.creating || this.ending || !confirm(BridgeI18n.t('结束临时侧聊并清除内容？主聊天不受影响。'))) return;
    const cleanup = this.workbench.temporaryCleanup?.(chat.id, chat.host || this.session.host);
    this.ending = true;
    try {
      await this.workbench.request(this.url(), {action: 'close', id: chat.id});
      try {
        if (this.workbench.current === this.session) await this.workbench.close('child:' + chat.id);
      } finally { cleanup?.(); }
      await this.load();
    } catch (error) { if (!this.disposed) this.status.textContent = BridgeI18n.t(error.message); }
    finally { this.ending = false; }
  }
  // Closing a view never sends stop/archive/delete and never destroys history.
  dispose() { this.disposed = true; this.controller?.abort(); }
}
