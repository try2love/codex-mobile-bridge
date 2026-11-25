'use strict';

class ComposerToolbar {
  static nextBodyId = 0;

  constructor({
    form,
    tools,
    bottom,
    actions,
    input,
    controls,
    workMode,
    body,
    hint,
    onPreferenceChange,
    sendState,
  }) {
    Object.assign(this, { form, tools, bottom, actions, input, controls, workMode, body });
    this.labels = Object.fromEntries(composerTools.map((tool) => [tool.id, tool.label]));
    this.shortLabels = Object.fromEntries(
      composerTools.map((tool) => [tool.id, tool.shortLabel || tool.label]),
    );
    this.preferences = ComposerPreferences.shared();
    this.layout = this.preferences.value.layout;
    this.display = this.preferences.value.display;
    this.wrappers = {};
    this.toolCopies = {};
    this.menuTools = new Map();
    this.groups = {};
    this.disposed = false;
    this.followUpMode = this.preferences.value.followUpMode;
    this.collapsed = false;
    this.buildLayout(hint);
    this.buildToolControls();
    this.buildSubmitControl(sendState);
    this.buildStatus();
    this.outbox = ComposerUI.node('section', 'offline-outbox');
    this.outbox.hidden = true;
    this.form.before(this.outbox);
    this.onOfflineChange = ({ detail }) => {
      const current = this.offlineRoute, route = detail?.route;
      if (current && route?.provider === current.route.provider && route?.host === current.route.host && route?.session === current.route.session) {
        this.renderOutbox();
        this.requestSync();
      }
    };
    window.addEventListener('bridge-offline-composer-change', this.onOfflineChange);
    this.bindUpdates(onPreferenceChange);
    this.apply();
  }

  buildLayout(hint) {
    this.form.classList.add('custom-composer');
    this.tools.classList.add('toolbar-top');
    this.bottom.classList.add('toolbar-bottom');
    this.frame = ComposerUI.node('div', 'composer-frame');
    this.body.before(this.frame);
    this.frame.append(this.tools, this.body);
    this.moreButton = ComposerUI.button('工具面板', 'more');
    this.moreButton.classList.add('toolbar-more');
    this.moreButton.setAttribute('aria-expanded', 'false');
    this.moreButton.onclick = () => this.openMenu();
    this.controls.more = this.moreButton;
    for (const key of ['topLeft', 'topRight', 'bottomLeft', 'bottomRight']) {
      const group = ComposerUI.node(
        'div',
        'toolbar-group ' + (key.endsWith('Right') ? 'toolbar-right' : 'toolbar-left'),
      );
      group.dataset.toolbarZone = key;
      this.groups[key] = group;
      if (key.startsWith('top')) {
        this.tools.append(group);
      } else {
        this.bottom.insertBefore(group, this.actions);
      }
    }
    this.bottomPin = ComposerUI.node('div', 'toolbar-pin');
    this.bottomScroll = ComposerUI.node('div', 'toolbar-group toolbar-bottom-scroll');
    this.bottomScroll.append(this.groups.bottomLeft, this.groups.bottomRight);
    this.bottom.insertBefore(this.bottomPin, this.actions);
    this.bottom.insertBefore(this.bottomScroll, this.actions);
    this.body.id ||= 'composer-body-' + ++ComposerToolbar.nextBodyId;
    this.restoreButton = ComposerUI.button('展开输入区', 'collapse');
    this.restoreButton.classList.add('toolbar-restore');
    this.restoreButton.hidden = true;
    this.restoreButton.setAttribute('aria-controls', this.body.id);
    this.restoreButton.onclick = () => this.setCollapsed(false);
    this.tools.append(this.restoreButton);
    this.controls.collapse = ComposerUI.button(this.labels.collapse, 'collapse');
    this.controls.collapse.setAttribute('aria-expanded', 'true');
    this.controls.collapse.setAttribute('aria-controls', this.body.id);
    this.controls.collapse.onclick = () => this.setCollapsed(!this.collapsed);
    this.sources = ComposerUI.node('div', 'toolbar-sources');
    this.sources.hidden = true;
    this.form.append(this.sources);
    this.sources.append(this.workMode);
    if (hint) {
      this.input.after(hint);
    }
    this.modeTag = ComposerUI.node('button', 'toolbar-mode-tag');
    this.modeTag.type = 'button';
    this.modeTag.hidden = true;
    this.input.before(this.modeTag);
    this.modeTag.onclick = () => this.setMode('default');
    for (const id of ['plan', 'goal']) {
      const button = ComposerUI.button(this.labels[id], id);
      button.onclick = () => this.setMode(this.workMode.value === id ? 'default' : id);
      this.controls[id] = button;
    }
  }

  buildToolControls() {
    for (const id of composerToolIds) {
      const control = this.controls[id];
      control.dataset.composerTool = id;
      if (['skills', 'permissions', 'attachments'].includes(id)) {
        control.classList.add('toolbar-icon');
        control.querySelector('svg')?.remove();
        this.decorate(id);
      }
      const wrapper = ComposerUI.node('div', 'toolbar-tool');
      wrapper.dataset.tool = id;
      wrapper.append(control);
      this.wrappers[id] = wrapper;
      if (['model', 'quota'].includes(id) && !control.querySelector('svg')) {
        control.prepend(ComposerUI.icon(id));
      }
      if (id === 'sendMode') {
        const icon = ComposerUI.icon(id);
        icon.classList.add('toolbar-select-icon');
        wrapper.prepend(icon);
      }
      if (['more', 'attachments', 'context', 'plan', 'goal', 'collapse'].includes(id)) {
        const copy = ComposerUI.node(
          'span',
          'toolbar-copy',
          ComposerUI.t(this.shortLabels[id]),
        );
        this.toolCopies[id] = copy;
        control.append(copy);
      }
    }
  }

  buildMenu() {
    this.menu = ComposerUI.node('dialog', 'picker toolbar-menu');
    this.menu.setAttribute('aria-label', ComposerUI.t('工具面板'));
    const head = ComposerUI.node('div', 'picker-head');
    this.menuTitle = ComposerUI.node('h2', '', ComposerUI.t('工具面板'));
    this.menuClose = ComposerUI.button('关闭', 'close');
    this.menuClose.className = 'icon-button';
    this.menuClose.onclick = () => this.menu.close();
    this.moreContent = ComposerUI.node('div', 'toolbar-more-tools');
    this.editButton = ComposerUI.button('定制工具栏', 'edit');
    this.editButton.className = 'icon-button toolbar-edit-button';
    this.editLabel = ComposerUI.node('span', '', ComposerUI.t('定制'));
    this.editButton.append(this.editLabel);
    this.editButton.onclick = () => this.edit();
    head.append(this.menuTitle, this.editButton, this.menuClose);
    this.menu.append(head, this.moreContent);
    document.body.append(this.menu);
    this.menu.addEventListener('click', (event) => {
      const button = event.target.closest('[data-menu-tool]');
      if (button) {
        this.activate(button.dataset.menuTool);
      }
    });
    this.menu.addEventListener('close', () =>
      this.moreButton.setAttribute('aria-expanded', 'false'),
    );
  }

  buildSubmitControl(sendState) {
    this.submitControl = new ComposerSubmitControl({
      form: this.form,
      send: this.actions.querySelector('button[type=submit]'),
      stop: this.actions.querySelector('.stop'),
      mode: this.controls.sendMode,
      frame: this.frame,
      preferences: this.preferences,
      getState: () => this.localCompose ? {
        running: false, hasDraft: !!this.input.value.trim() || !!this.offlineRoute?.draft.read().quotes?.length ||
          !!this.offlineRoute?.draft.read().attachmentDrafts?.length,
        pending: !!this.offlineRoute?.saving,
        offline: true,
      } : sendState(),
      isCollapsed: () => this.collapsed,
      onError: (error) => this.showError(error),
    });
    this.actions.append(this.submitControl.root);
    this.sources.append(this.submitControl.stop);
  }

  buildStatus() {
    this.status = ComposerUI.node('p', 'toolbar-status');
    this.status.hidden = true;
    this.status.setAttribute('role', 'status');
    this.input.after(this.status);
  }

  setOfflineRoute(route, offline, draft, account = '', writable = true) {
    const feature = window.BridgeOfflineComposer;
    const key = route ? JSON.stringify(route) : '';
    const previous = this.offlineRoute;
    if (previous?.key === key && previous.offline === offline && previous.account === account && previous.writable === writable) return;
    this.outboxEditor?.close();
    const current = this.offlineRoute = route && feature && writable ? { key, route, offline, draft, account, writable, revision: 0 } : null;
    this.form.toggleAttribute('data-offline-composer', !!route && offline);
    this.form.toggleAttribute('data-offline-composer-readonly', !!route && offline && !writable);
    this.renderOutbox();
    this.requestSync();
    if (!current || !offline && !account) return;
    const initial = draft.read();
    void feature.get(route).then((record) => {
      if (this.offlineRoute !== current) return;
      if (current.revision === 0 && (!account || !record?.account || account === record.account)) {
        const fromOnline = offline && previous?.key === key && previous.account === account && !previous.offline;
        if (offline && (initial.text || initial.quotes?.length || initial.attachmentDrafts?.length || initial.skills?.length) && (fromOnline || !record?.draft)) {
          this.saveOfflineDraft(initial);
        } else if (record?.draft && (offline || record.account === account)) {
          draft.restore(record.draft);
        }
      }
      this.renderOutbox();
      this.requestSync();
    }).catch(() => {
      if (this.offlineRoute === current && offline) this.showError(new Error('离线草稿暂不可用'));
    });
  }

  get localCompose() { return this.form.hasAttribute('data-offline-composer'); }

  offlineRecord() {
    const current = this.offlineRoute;
    if (!current || !current.offline && !current.account) return null;
    const record = window.BridgeOfflineComposer.peek(current.route);
    if (current.account && record?.account !== current.account) return null;
    return record;
  }

  saveOfflineDraft(draft = this.offlineRoute?.draft.read()) {
    const current = this.offlineRoute;
    if (!current || !current.offline && !current.account) return;
    current.revision++;
    if(current.enqueuedContent)draft={...draft,
      attachmentDrafts:draft.attachmentDrafts?.filter(row=>!current.enqueuedContent.attachments.has(row.id)),
      skills:draft.skills?.filter(id=>!current.enqueuedContent.skills.has(id)),
      quotes:draft.quotes?.filter(quote=>!current.enqueuedContent.quotes.has(JSON.stringify(quote))),
    };
    void Promise.resolve(window.BridgeOfflineComposer.saveDraft(current.route, draft)).catch(() => {
      if (current.offline && this.offlineRoute === current) this.showError(new Error('离线草稿保存失败'));
    });
  }

  async enqueueOffline(body, clearSentContent) {
    const current = this.offlineRoute;
    if (!current?.offline || current.saving || this.offlineRecord()?.canSend !== true||
        !body.text.trim()&&!body.attachments?.length&&!body.references?.length) return;
    const draft=current.draft.read();
    current.enqueuedContent={
      attachments:new Set(draft.attachmentDrafts?.map(row=>row.id)),
      skills:new Set(draft.skills),
      quotes:new Set(draft.quotes?.map(quote=>JSON.stringify(quote))),
    };
    current.saving = true;
    this.requestSync();
    const revision = current.revision;
    try {
      await window.BridgeOfflineComposer.enqueue(current.route, body);
      clearSentContent?.(current.enqueuedContent, this.offlineRoute === current);
      if (this.offlineRoute === current && current.revision === revision) {
        current.draft.restore({ text: '', selectionStart: 0, selectionEnd: 0, quotes: [],attachmentDrafts:[] });
      }
      this.renderOutbox();
    } catch {
      if (this.offlineRoute === current) {
        current.enqueuedContent=null;
        if(current.revision!==revision)this.saveOfflineDraft();
        this.showError(new Error('离线消息保存失败，请重试'));
      }
    } finally {
      current.saving = false;
      current.enqueuedContent = null;
      this.requestSync();
    }
  }

  renderOutbox() {
    const current = this.offlineRoute;
    const rows = this.offlineRecord()?.outbox || [];
    const stamp = JSON.stringify([current?.key, current?.offline, ComposerUI.t('待发送'), rows]);
    if (this.outboxStamp === stamp) return;
    this.outboxStamp = stamp;
    this.outbox.hidden = !rows.length;
    this.outbox.setAttribute('aria-label', ComposerUI.t('待发送'));
    this.outbox.replaceChildren(...rows.map((row) => {
      const card = ComposerUI.node('article', 'queue-card');
      const status = row.state === 'checking' ? '发送结果待确认' : row.state === 'failed' ? '待发消息发送失败' : current.offline ? '待发送 · 等待连接' : '待发送';
      card.append(ComposerUI.node('strong', '', ComposerUI.t(status)), ComposerUI.node('p', '', row.body.text));
      const attachments=(row.body.attachments?.length||0)+(row.body.references?.length||0);
      if(attachments)card.append(ComposerUI.node('small','muted',ComposerUI.t('附件：')+attachments));
      if (row.state === 'failed' && row.error) card.append(ComposerUI.node('small','muted',row.error));
      if (row.state === 'checking') {
        card.append(ComposerUI.node('small','muted',ComposerUI.t('请检查原会话中的发送结果。')));
      } else {
        const remove = ComposerUI.textButton(row.state === 'pending' ? '撤回' : '移除', async () => {
          try { await window.BridgeOfflineComposer.remove(current.route, row.id); }
          catch { if(this.offlineRoute === current) this.showError(new Error('待发消息撤回失败')); }
        });
        const actions = ComposerUI.node('div', 'actions');
        actions.append(ComposerUI.textButton('编辑', () => this.editOutbox(current, row)));
        actions.append(remove);card.append(actions);
      }
      return card;
    }));
  }

  editOutbox(current, row) {
    if (this.outboxEditor?.open || this.offlineRoute !== current) return;
    const dialog = this.outboxEditor = ComposerUI.node('dialog', 'picker offline-outbox-editor');
    const title = ComposerUI.node('h2', '', ComposerUI.t('编辑待发消息'));
    const input = ComposerUI.node('textarea');
    input.value = row.body.text;
    input.maxLength = 100000;
    input.setAttribute('aria-label', title.textContent);
    const error = ComposerUI.node('p', 'error');
    error.setAttribute('role', 'alert');
    const save = ComposerUI.textButton('保存', async () => {
      if (!input.value.trim()&&!row.body.attachments?.length&&!row.body.references?.length) return;
      save.disabled = true;
      try { await window.BridgeOfflineComposer.edit(current.route, row.id, input.value); dialog.close(); }
      catch { error.textContent = ComposerUI.t('待发消息编辑失败'); }
      finally { save.disabled = false; }
    });
    const actions = ComposerUI.node('div', 'actions');
    actions.append(save, ComposerUI.textButton('取消', () => dialog.close()));
    dialog.append(title, input, error, actions);
    dialog.addEventListener('close', () => dialog.remove(), { once: true });
    document.body.append(dialog);
    dialog.showModal();
  }

  bindUpdates(onPreferenceChange) {
    this.onInput = () => {
      this.requestInputResize();
      if (this.offlineRoute) this.saveOfflineDraft(this.offlineRoute.draft.read());
    };
    this.input.addEventListener('input', this.onInput);
    this.alerts = Array.from(this.form.querySelectorAll('[role=alert]'));
    this.alertObserver = new MutationObserver(() => {
      if (this.collapsed && this.alerts.some((alert) => alert.textContent.trim())) {
        this.setCollapsed(false);
      }
    });
    for (const alert of this.alerts) {
      this.alertObserver.observe(alert, {
        subtree: true,
        childList: true,
        characterData: true,
      });
    }
    this.modeListener = () => { this.requestSync(); this.saveOfflineDraft(); };
    this.workMode.addEventListener('change', this.modeListener);
    this.unsubscribe = this.preferences.subscribe((value) => {
      const modeChanged = this.followUpMode !== value.followUpMode;
      const layoutChanged = this.layout !== value.layout;
      this.followUpMode = value.followUpMode;
      this.layout = value.layout;
      this.display = value.display;
      if (layoutChanged) this.apply();
      this.requestSync();
      if (modeChanged) {
        onPreferenceChange();
      }
    });
    this.onLanguage = () => this.relabel();
    document.addEventListener('bridge-language', this.onLanguage);
    this.overflowChanged = () => this.scheduleOverflow();
    this.resizeObserver = new ResizeObserver(this.overflowChanged);
    // Tool widths can change inside a group whose outer width stays fixed.
    for (const wrapper of Object.values(this.wrappers)) this.resizeObserver.observe(wrapper);
    this.scrollGroups = [this.groups.topLeft, this.groups.topRight, this.bottomScroll];
    for (const group of this.scrollGroups) {
      this.resizeObserver.observe(group);
      group.addEventListener('scroll', this.overflowChanged, { passive: true });
    }
  }
  toolIcon(id) {
    return ComposerUI.icon(id, this.toolIconState(id));
  }
  toolIconState(id) {
    if (id === 'sendMode') return { sendMode: this.controls.sendMode.value };
    if (id === 'permissions')
      return { permissionMode: this.controls[id].dataset.permissionMode || '' };
    if (id === 'collapse') return { collapsed: this.collapsed };
    return undefined;
  }
  // The editor consumes this snapshot rather than native control markup.
  describeTool(id) {
    const control = this.controls[id];
    const label = id === 'submit' ? composerSubmitTool.label : this.labels[id];
    const shortLabel = id === 'submit' ? '发送' : this.shortLabels[id];
    let previewLabel = ComposerUI.t(shortLabel);
    if (id === 'model') {
      previewLabel =
        Array.from(control.querySelectorAll('span'))
          .filter((node) => !node.hidden)
          .map((node) => node.textContent.trim())
          .join(' ') || previewLabel;
    }
    if (id === 'permissions') {
      previewLabel = control.textContent.trim() || previewLabel;
    }
    if (id === 'sendMode') {
      previewLabel = control.selectedOptions[0]?.textContent || previewLabel;
    }
    return {
      label,
      shortLabel,
      previewLabel,
      icon: this.toolIconState(id),
      disabled: !!control?.disabled,
      reason: control?.title || '',
    };
  }
  decorate(id) {
    const control = this.controls[id];
    const label =
      id === 'attachments'
        ? ComposerUI.t(this.labels[id])
        : control.textContent.trim() || ComposerUI.t(this.labels[id]);
    const icon = control.querySelector('svg');
    if (!icon) {
      control.append(this.toolIcon(id));
    } else if (
      id === 'permissions' &&
      icon.dataset.permissionMode !== (control.dataset.permissionMode || '')
    ) {
      icon.replaceWith(this.toolIcon(id));
    }
    ComposerUI.attribute(control, 'aria-label', label);
    if (!control.disabled || !control.title) {
      ComposerUI.attribute(control, 'title', label);
    }
  }
  showError(error) {
    this.status.textContent = ComposerUI.t(error.message);
    this.status.hidden = false;
    this.setCollapsed(false);
  }
  actionMode() {
    return this.submitControl.action().mode;
  }
  requestSync() {
    if (this.scheduled || this.disposed) {
      return;
    }
    this.scheduled = true;
    queueMicrotask(() => {
      if (this.scheduled && !this.disposed) {
        this.sync();
      }
    });
  }
  apply() {
    for (const key of composerZones) {
      const parent = key === 'more' ? this.sources : this.groups[key];
      this.layout[key].forEach((id, index) => {
        const wrapper = this.wrappers[id];
        const before = key === 'more' ? null : parent.children[index];
        if (wrapper.parentElement !== parent || (key !== 'more' && before !== wrapper)) {
          parent.insertBefore(wrapper, before || null);
        }
      });
    }
    this.scheduleOverflow();
    this.requestSync();
  }

  available(id) {
    return !this.controls[id].hidden;
  }
  shown(id) {
    return this.available(id) && this.display[id] !== 'hidden';
  }
  enabled(id) {
    return this.available(id) && !this.controls[id].disabled;
  }
  sync() {
    if (this.disposed) {
      return;
    }
    this.scheduled = false;
    if (this.localCompose) {
      this.submitControl.send.disabled = this.offlineRecord()?.canSend !== true || !!this.offlineRoute?.saving;
      this.input.disabled = this.form.hasAttribute('data-offline-composer-readonly');
      for (const id of ['skills', 'permissions', 'attachments', 'sendMode']) this.controls[id].disabled = true;
      this.workMode.disabled = true;
    }
    this.submitControl.sync();
    this.syncPlaceholder();
    for (const id of ['skills', 'permissions', 'attachments']) {
      this.decorate(id);
    }
    ComposerUI.hidden(
      this.restoreButton,
      !this.collapsed || this.tools.contains(this.controls.collapse),
    );
    this.syncWorkMode();
    this.syncTools();
    this.syncLayout();
    this.syncModeTag();
    if (this.menu?.open) {
      this.renderMenu();
    }
    this.renderChoice();
    if (this.editor?.dialog.open) {
      this.editor.updateToolState();
    }
  }
  syncWorkMode() {
    for (const id of ['plan', 'goal']) {
      const option = this.workMode.querySelector('[value=' + id + ']');
      const control = this.controls[id];
      const hidden = this.workMode.hidden || !option;
      const disabled = this.workMode.disabled || !option || option.disabled;
      if (control.hidden !== hidden) {
        control.hidden = hidden;
      }
      if (control.disabled !== disabled) {
        control.disabled = disabled;
      }
      ComposerUI.attribute(control, 'aria-pressed', this.workMode.value === id);
      ComposerUI.attribute(
        control,
        'title',
        disabled
          ? option?.title || this.workMode.title || ComposerUI.t('当前接入不支持此功能')
          : ComposerUI.t(this.labels[id]),
      );
    }
  }
  syncTools() {
    for (const id of composerToolIds) {
      const wrapper = this.wrappers[id];
      const control = this.controls[id];
      ComposerUI.hidden(wrapper, !this.shown(id));
      ComposerUI.attribute(wrapper, 'data-display', this.display[id]);
      wrapper.classList.toggle('toolbar-tool-disabled', control.disabled);
      const copy = this.toolCopies[id];
      if (copy) {
        const label =
          id === 'context'
            ? control.title || ComposerUI.t(this.shortLabels[id])
            : ComposerUI.t(this.shortLabels[id]);
        if (copy.textContent !== label) {
          copy.textContent = label;
        }
      }
      if (id === 'model') {
        ComposerUI.attribute(
          control,
          'aria-label',
          control.title || control.textContent.trim() || ComposerUI.t(this.labels[id]),
        );
      }
      if (id === 'sendMode') {
        const icon = wrapper.querySelector('.toolbar-select-icon');
        const selected = control.value;
        if (icon?.dataset.icon !== selected && composerFollowUpModes.includes(selected)) {
          const next = this.toolIcon(id);
          next.classList.add('toolbar-select-icon');
          icon.replaceWith(next);
        }
        ComposerUI.attribute(
          wrapper,
          'title',
          control.selectedOptions[0]?.textContent || ComposerUI.t(this.labels[id]),
        );
      }
    }
  }
  syncLayout() {
    // Empty left groups keep right-aligned tools and the fixed send area anchored.
    for (const key of Object.keys(this.groups)) {
      ComposerUI.hidden(
        this.groups[key],
        key.endsWith('Right') && !this.layout[key].some((id) => this.shown(id)),
      );
    }
    const panelZone =
      composerZones.find((key) => this.layout[key].includes('more')) || 'topLeft';
    const panel = this.wrappers.more;
    const panelParent =
      this.collapsed && panelZone.startsWith('bottom') ? this.tools : this.groups[panelZone];
    if (panel.parentElement !== panelParent) {
      const index = this.layout[panelZone].indexOf('more');
      panelParent.insertBefore(
        panel,
        panelParent === this.tools
          ? panelParent.firstChild
          : panelParent.children[index] || null,
      );
    }
    ComposerUI.hidden(
      this.tools,
      !this.collapsed &&
        !['topLeft', 'topRight'].some((key) => this.layout[key].some((id) => this.shown(id))),
    );
    this.syncBottomLayout();
  }
  syncBottomLayout() {
    const pinned = this.layout.bottomLeft.find((id) => this.shown(id) && !(this.collapsed && id === 'more'));
    for (const key of ['bottomLeft', 'bottomRight']) {
      const group = this.groups[key];
      let index = 0;
      for (const id of this.layout[key]) {
        if (id === pinned || (this.collapsed && id === 'more')) continue;
        const wrapper = this.wrappers[id];
        const before = group.children[index++];
        if (before !== wrapper) group.insertBefore(wrapper, before || null);
      }
    }
    if (pinned && this.wrappers[pinned].parentElement !== this.bottomPin) {
      this.bottomPin.append(this.wrappers[pinned]);
    }
    ComposerUI.hidden(this.bottomPin, !pinned);
  }
  syncPlaceholder() {
    const placeholder = ComposerUI.t(this.localCompose ? this.offlineRecord()?.canSend === true ? '离线撰写，联网后发送' : '离线撰写草稿' : this.actionMode() === 'queue'
      ? '完成后发送 · 输入 / 查看指令'
      : '继续这条聊天 · 输入 / 查看指令');
    if (this.input.placeholder !== placeholder) {
      this.input.placeholder = placeholder;
      if (!this.input.value) this.requestInputResize();
    }
  }
  syncModeTag() {
    const mode = this.workMode.value;
    ComposerUI.hidden(this.modeTag, this.workMode.hidden || !['plan', 'goal'].includes(mode));
    const tag = ComposerUI.t(mode === 'goal' ? '目标模式' : '计划模式') + ' ×';
    if (this.modeTag.textContent !== tag) {
      this.modeTag.textContent = tag;
    }
    if (this.modeTag.disabled !== this.workMode.disabled) {
      this.modeTag.disabled = this.workMode.disabled;
    }
  }
  scheduleOverflow() {
    if (this.overflowFrame || this.disposed) {
      return;
    }
    this.overflowFrame = requestAnimationFrame(() => {
      this.overflowFrame = null;
      const edges = this.scrollGroups.map((group) => {
        const offset = group.scrollLeft;
        return {
          group,
          left: offset > 1,
          right: group.scrollWidth - group.clientWidth - offset > 1,
        };
      });
      for (const { group, left, right } of edges) {
        group.classList.toggle('can-scroll-left', left);
        group.classList.toggle('can-scroll-right', right);
        ComposerUI.attribute(group, 'tabindex', left || right ? 0 : -1);
        ComposerUI.attribute(
          group,
          'title',
          left || right ? ComposerUI.t('左右滑动查看更多工具') : '',
        );
      }
    });
  }
  requestInputResize() {
    if (!this.inputResizeFrame && !this.collapsed) {
      this.inputResizeFrame = requestAnimationFrame(() => {
        this.inputResizeFrame = null;
        this.resizeInput();
      });
    }
  }
  resizeInput() {
    if (this.inputResizeFrame) {
      cancelAnimationFrame(this.inputResizeFrame);
      this.inputResizeFrame = null;
    }
    if (this.body.hidden) return;
    this.input.style.height = 'auto';
    this.input.style.height = Math.min(160, Math.max(48, this.input.scrollHeight)) + 'px';
  }
  setCollapsed(value) {
    const active = document.activeElement;
    const moveFocus =
      (value && this.body.contains(active)) || (!value && active === this.restoreButton);
    this.collapsed = !!value;
    this.body.hidden = this.collapsed;
    this.form.classList.toggle('is-collapsed', this.collapsed);
    const label = this.collapsed ? '展开输入区' : '收起输入区';
    this.labels.collapse = label;
    this.shortLabels.collapse = label;
    this.controls.collapse.setAttribute('aria-expanded', String(!this.collapsed));
    this.controls.collapse.setAttribute('aria-label', ComposerUI.t(label));
    this.controls.collapse.title = ComposerUI.t(label);
    if (this.collapsed) {
      this.input.blur();
    } else {
      this.input.style.height = 'auto';
      this.input.style.height =
        Math.min(160, Math.max(48, this.input.scrollHeight || 48)) + 'px';
    }
    this.sync();
    if (moveFocus) {
      const target = !this.restoreButton.hidden
        ? this.restoreButton
        : this.tools.contains(this.controls.collapse) && this.shown('collapse')
          ? this.controls.collapse
          : this.moreButton;
      target.focus({ preventScroll: true });
    }
  }
  setMode(value) {
    if (
      this.workMode.disabled ||
      this.workMode.hidden ||
      this.workMode.querySelector('[value=' + value + ']')?.disabled
    ) {
      return;
    }
    this.workMode.value = value;
    this.workMode.dispatchEvent(new Event('change', { bubbles: true }));
    this.sync();
  }
  addChoices() {
    return composerTools
      .filter((tool) => tool.addMenu && this.available(tool.id))
      .map((tool) => ({
        label: this.controls[tool.id].getAttribute('aria-label') || ComposerUI.t(tool.label),
        disabled: !this.enabled(tool.id),
        title: this.controls[tool.id].title,
        action: () => {
          if (this.enabled(tool.id)) {
            this.controls[tool.id].click();
          }
        },
      }));
  }
  renderMenu() {
    const focused = document.activeElement;
    let index = 0;
    for (const id of this.layout.more) {
      if (this.display[id] === 'hidden') {
        continue;
      }
      const iconState = this.toolIconState(id);
      const iconKey = iconState?.permissionMode ?? iconState?.collapsed;
      let item = this.menuTools.get(id);
      if (!item) {
        const button = ComposerUI.node('button', 'toolbar-menu-tool');
        button.type = 'button';
        button.dataset.menuTool = id;
        const copy = ComposerUI.node('span');
        const reason = ComposerUI.node('small');
        const icon = ComposerUI.icon(id, iconState);
        button.append(icon, copy, reason);
        item = { button, copy, reason, icon, iconKey };
        this.menuTools.set(id, item);
      } else if (item.iconKey !== iconKey) {
        const icon = ComposerUI.icon(id, iconState);
        item.icon.replaceWith(icon);
        Object.assign(item, { icon, iconKey });
      }
      const { button, copy, reason } = item;
      const disabled = !this.enabled(id);
      if (button.disabled !== disabled) button.disabled = disabled;
      const explanation = button.disabled
        ? (this.available(id) && this.controls[id].title) ||
          ComposerUI.t('当前接入不支持此功能')
        : '';
      if (reason.textContent !== explanation) {
        reason.textContent = explanation;
      }
      ComposerUI.hidden(reason, !explanation);
      const label =
        id === 'permissions'
          ? this.controls[id].getAttribute('aria-label')
          : ComposerUI.t(this.labels[id]);
      if (copy.textContent !== label) {
        copy.textContent = label;
      }
      if (this.controls[id].hasAttribute('aria-pressed')) {
        ComposerUI.attribute(
          button,
          'aria-pressed',
          this.controls[id].getAttribute('aria-pressed'),
        );
      }
      const at = this.moreContent.children[index++];
      if (at !== button) {
        this.moreContent.insertBefore(button, at || null);
      }
    }
    while (this.moreContent.children.length > index) this.moreContent.lastElementChild.remove();
    if (
      focused?.isConnected &&
      this.moreContent.contains(focused) &&
      !focused.disabled &&
      document.activeElement !== focused
    ) {
      focused.focus({ preventScroll: true });
    }
  }
  openMenu() {
    this.submitControl.close();
    if (!this.menu) this.buildMenu();
    this.renderMenu();
    this.menu.showModal();
    this.moreButton.setAttribute('aria-expanded', 'true');
  }
  anchor(id) {
    return this.sources.contains(this.controls[id]) ? this.moreButton : this.controls[id];
  }
  activate(id) {
    if (!this.enabled(id)) {
      return;
    }
    this.menu.close();
    const control = this.controls[id];
    if (control.tagName !== 'SELECT') {
      control.click();
      return;
    }
    this.choice?.dialog.close();
    const dialog = ComposerUI.node('dialog', 'picker toolbar-choice');
    const head = ComposerUI.node('div', 'picker-head');
    const title = ComposerUI.node('h2', '', ComposerUI.t(this.labels[id]));
    const close = ComposerUI.node('button', 'icon-button');
    close.append(ComposerIcons.create('close'));
    close.type = 'button';
    close.setAttribute('aria-label', ComposerUI.t('关闭'));
    close.onclick = () => dialog.close();
    head.append(title, close);
    dialog.append(head);
    const buttons = new Map();
    for (const option of control.options) {
      const button = ComposerUI.node('button', 'toolbar-choice-option');
      button.type = 'button';
      button.onclick = () => {
        if (!this.enabled(id) || option.disabled || option.hidden) {
          return;
        }
        control.value = option.value;
        control.dispatchEvent(new Event('change', { bubbles: true }));
        dialog.close();
      };
      buttons.set(option.value, button);
      dialog.append(button);
    }
    this.choice = { dialog, title, close, id, buttons };
    dialog.addEventListener(
      'close',
      () => {
        dialog.remove();
        if (this.choice?.dialog === dialog) {
          this.choice = null;
        }
      },
      { once: true },
    );
    document.body.append(dialog);
    this.renderChoice();
    dialog.showModal();
  }
  renderChoice() {
    if (!this.choice) {
      return;
    }
    const { dialog, title, close, id, buttons } = this.choice;
    const control = this.controls[id];
    if (title.textContent !== ComposerUI.t(this.labels[id])) {
      title.textContent = ComposerUI.t(this.labels[id]);
    }
    ComposerUI.attribute(close, 'aria-label', ComposerUI.t('关闭'));
    for (const option of control.options) {
      const button = buttons.get(option.value);
      if (!button) {
        continue;
      }
      if (button.textContent !== option.textContent) {
        button.textContent = option.textContent;
      }
      const disabled = option.disabled || control.disabled;
      if (button.disabled !== disabled) button.disabled = disabled;
      ComposerUI.hidden(button, option.hidden);
      ComposerUI.attribute(button, 'aria-pressed', option.value === control.value);
    }
    if (control.hidden && dialog.open) {
      dialog.close();
    }
  }

  async edit() {
    if (this.disposed || this.editingLoad || !this.menu?.open) {
      return;
    }
    this.editingLoad = true;
    this.editButton.disabled = true;
    try {
      await Promise.all([
        BridgeFeatures.load('composer-editor'),
        BridgeFeatures.load('composer-editor-style'),
      ]);
      if (this.disposed || !this.menu.open) {
        return;
      }
      this.menu.close();
      this.editor ||= new ComposerToolbarEditor({
        preferences: this.preferences,
        describeTool: (id) => this.describeTool(id),
        getPlaceholder: () => this.input.placeholder,
        onClose: () => this.moreButton.focus({ preventScroll: true }),
      });
      this.editor.open();
    } catch (error) {
      if (!this.disposed) {
        this.showError(new Error('工具栏编辑器加载失败，请重试。'));
      }
    } finally {
      this.editingLoad = false;
      this.editButton.disabled = false;
    }
  }

  relabel() {
    this.renderOutbox();
    for (const id of composerToolIds) {
      if (['skills', 'permissions', 'attachments'].includes(id)) {
        this.decorate(id);
      } else if (this.controls[id].classList.contains('toolbar-icon')) {
        this.controls[id].setAttribute('aria-label', ComposerUI.t(this.labels[id]));
        this.controls[id].title = ComposerUI.t(this.labels[id]);
      }
    }
    this.moreButton.setAttribute('aria-label', ComposerUI.t('工具面板'));
    this.moreButton.title = ComposerUI.t('工具面板');
    this.restoreButton.setAttribute('aria-label', ComposerUI.t('展开输入区'));
    this.restoreButton.title = ComposerUI.t('展开输入区');
    if (this.menu) {
      this.menuTitle.textContent = ComposerUI.t('工具面板');
      this.menu.setAttribute('aria-label', this.menuTitle.textContent);
      this.menuClose.setAttribute('aria-label', ComposerUI.t('关闭'));
      this.menuClose.title = ComposerUI.t('关闭');
      this.editButton.setAttribute('aria-label', ComposerUI.t('定制工具栏'));
      this.editButton.title = ComposerUI.t('定制工具栏');
      this.editLabel.textContent = ComposerUI.t('定制');
    }
    this.sync();
    this.scheduleOverflow();
  }
  dispose() {
    this.disposed = true;
    window.removeEventListener('bridge-offline-composer-change', this.onOfflineChange);
    this.outboxEditor?.close();
    this.outbox.remove();
    this.scheduled = false;
    this.alertObserver.disconnect();
    this.submitControl.dispose();
    this.resizeObserver.disconnect();
    if (this.inputResizeFrame) cancelAnimationFrame(this.inputResizeFrame);
    this.input.removeEventListener('input', this.onInput);
    if (this.overflowFrame) {
      cancelAnimationFrame(this.overflowFrame);
    }
    for (const group of this.scrollGroups) {
      group.removeEventListener('scroll', this.overflowChanged);
    }
    this.unsubscribe();
    this.choice?.dialog.remove();
    this.menu?.remove();
    this.editor?.dispose();
    document.removeEventListener('bridge-language', this.onLanguage);
    this.workMode.removeEventListener('change', this.modeListener);
  }
}
