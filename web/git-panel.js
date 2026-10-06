'use strict';

class GitPanel {
  constructor(workbench, session, tab) {
    this.workbench = workbench; this.session = session; this.tab = tab; this.mode = 'changes'; this.historyLimit = 40;
    this.node = (...args) => workbench.node(...args);
    this.button = (...args) => workbench.button(...args);
  }
  dispose() { this.tab.controller?.abort(); this.diffController?.abort(); this.historyDiffController?.abort(); }
  async load() {
    this.dispose(); const controller = this.tab.controller = new AbortController();
    const head = this.node('div', 'wb-git-head'), title = this.node('div', 'wb-git-heading');
    title.append(this.node('strong', '', 'Git'), this.node('span', '', '当前项目的变更'));
    const refresh = this.button('刷新', () => this.load(), '刷新 Git 状态');
    head.append(title, refresh);
    const status = this.node('p', 'wb-status', '正在读取 Git 状态…'); status.setAttribute('role', 'status');
    this.tab.body.replaceChildren(head, status);
    try {
      const result = await this.workbench.request(this.workbench.url(this.session, 'git-status'), undefined, controller.signal);
      if (controller.signal.aborted) return;
      if (!result.available) { status.textContent = result.message; return; }
      this.state = result;
      const branch = result.branch === '(detached)' ? '分离 HEAD · ' + result.commit.slice(0, 8) : result.branch;
      title.replaceChildren(this.node('strong', '', branch || 'Git'), this.node('span', '', result.project + ' · ' + (this.session.host === 'local' ? '此电脑' : 'SSH')));
      title.firstChild.title = branch;
      const notes = [];
      if (result.operation) notes.push(result.operation === 'merge' ? '合并进行中：解决冲突后暂存并提交，或中止合并' : '仓库正在变基或拣选，请在电脑上完成');
      if (result.commit === '(initial)') notes.push('尚无提交');
      if (result.scope !== '.') notes.push('文件差异仅限当前项目目录');
      if (result.upstream) notes.push(result.upstream + (result.ahead === null ? '' : ' · 领先 ' + result.ahead + ' / 落后 ' + result.behind) + '（本地记录）');
      status.textContent = notes.join(' · '); status.hidden = !notes.length;
      const modes = this.node('div', 'wb-git-modes');
      for (const [key, label] of [['changes', '变更'], ['history', '历史'], ['branches', '分支']]) {
        const button = this.button(label, () => { this.mode = key; this.load(); }); button.setAttribute('aria-pressed', String(this.mode === key)); modes.append(button);
      }
      this.tab.body.append(modes);
      if (this.actionError) this.tab.body.append(this.node('p', 'wb-status error', this.actionError));
      if (this.mode === 'history') { await this.history(controller); return; }
      if (this.mode === 'branches') { await this.branches(controller); return; }
      const actions = this.node('div', 'wb-git-actions');
      const stage = this.button('全部暂存', () => this.execute({action:'stage-all'}).catch(()=>{}));
      const unstage = this.button('全部取消暂存', () => this.execute({action:'unstage-all'}).catch(()=>{}));
      const staged = result.entries.filter(e => !e.untracked && !e.conflict && e.index !== '.');
      stage.disabled = !result.entries.some(e => e.untracked || e.conflict || e.worktree !== '.'); unstage.disabled = !staged.length;
      const commit = this.button((result.operation === 'merge' ? '完成合并' : '提交') + (staged.length ? ' · ' + staged.length : ''), () => this.commitDialog()); commit.className = 'primary';
      commit.disabled = (!staged.length && result.operation !== 'merge') || result.scope !== '.' || result.entries.some(e => e.conflict) || result.branch === '(detached)';
      actions.append(stage, unstage, commit);
      if (result.operation === 'merge') actions.append(this.button('中止合并', () => this.confirm('中止本次合并', '将恢复合并前的状态，本次合并中的冲突处理也会被撤销。', {action:'abort-merge'}, '中止合并')));
      this.tab.body.append(actions);
      const layout = this.node('div', 'wb-git-layout'); this.list = this.node('div', 'wb-git-list'); this.list.setAttribute('aria-label', 'Git 变更文件');
      this.detail = this.node('div', 'wb-git-detail'); this.detail.append(this.node('p', 'wb-empty', '选择文件查看差异'));
      layout.append(this.list, this.detail); this.tab.body.append(layout);
      const groups = [['conflict', '冲突', e => e.conflict], ['unstaged', '未暂存', e => !e.conflict && !e.untracked && e.worktree !== '.'], ['staged', '已暂存', e => !e.conflict && !e.untracked && e.index !== '.'], ['untracked', '未跟踪', e => e.untracked]];
      let restored;
      for (const [section, name, filter] of groups) {
        const entries = result.entries.filter(filter); if (!entries.length) continue;
        const group = this.node('section', 'wb-git-group'); group.append(this.node('h3', '', name + ' · ' + entries.length));
        for (const entry of entries) {
          const row = this.button('', () => this.show(entry, section, row), entry.path); row.className = 'wb-git-file'; row.dataset.section = section; row.dataset.path = entry.path;
          const code = section === 'conflict' ? 'U' : section === 'untracked' ? '?' : entry[section === 'staged' ? 'index' : 'worktree'];
          const labels = {M: '修改', A: '新增', D: '删除', R: '重命名', C: '复制', T: '类型变化', U: '冲突', '?': '新文件'};
          const badge = this.node('span', 'wb-git-badge', code); badge.dataset.change = code; badge.title = labels[code] || code; badge.setAttribute('aria-label', badge.title);
          const paths = this.node('span', 'wb-git-path'); paths.append(this.node('strong', '', entry.path));
          if (entry.oldPath) paths.append(this.node('small', '', '原路径：' + entry.oldPath));
          row.append(badge, paths);
          const rowGroup = this.node('div', 'wb-git-row'), action = this.button(section === 'staged' ? '−' : '＋', () => this.execute({action:section === 'staged' ? 'unstage' : 'stage', path:entry.path}).catch(()=>{}), (section === 'staged' ? '取消暂存 ' : '暂存 ') + entry.path);
          action.className = 'wb-git-row-action'; action.disabled = !!entry.submodule; rowGroup.append(row, action); group.append(rowGroup);
          if (this.selected === section + ':' + entry.path) restored = [entry, section, row];
        }
        this.list.append(group);
      }
      if (!result.entries.length) {
        layout.replaceChildren(this.node('div', 'wb-git-clean', '当前项目没有未提交的改动'));
        this.selected = null;
      } else if (restored) this.show(...restored);
    } catch (error) { if (!controller.signal.aborted) { status.hidden = false; status.textContent = error.message; status.classList.add('error'); } }
  }
  async show(entry, section, row) {
    this.diffController?.abort(); const controller = this.diffController = new AbortController();
    this.selected = section + ':' + entry.path;
    for (const button of this.list.querySelectorAll('.wb-git-file')) { button.classList.toggle('selected', button === row); button.setAttribute('aria-pressed', String(button === row)); }
    const header = this.node('div', 'wb-git-diff-head');
    const labels = {staged: '已暂存 · HEAD → 暂存区', unstaged: '未暂存 · 暂存区 → 工作区', untracked: '未跟踪 · 新文件', conflict: '冲突 · 当前工作区内容'};
    header.append(this.node('strong', '', entry.path), this.node('small', '', labels[section]));
    const content = this.node('div', 'wb-git-diff'); content.setAttribute('tabindex', '0'); content.setAttribute('aria-label', '文件差异');
    content.append(this.node('p', 'wb-status', '正在读取差异…')); this.detail.replaceChildren(header, content);
    try {
      const result = await this.workbench.request(this.workbench.url(this.session, 'git-diff', entry.path) + '&section=' + section, undefined, controller.signal);
      if (controller.signal.aborted) return;
      this.renderDiff(result, content, header);
    } catch (error) { if (!controller.signal.aborted) content.replaceChildren(this.node('p', 'wb-status error', error.message), this.button('重试', () => this.show(entry, section, row))); }
  }
  renderDiff(result, content, header) {
      content.replaceChildren();
      const messages = {binary: '二进制或非 UTF-8 文件，暂不提供文本差异。', large: '文件超过差异预览限制，请在电脑上查看。', submodule: '此项为子模块，请在电脑上查看子模块内部的改动。'};
      if (messages[result.kind]) { content.append(this.node('p', 'wb-status', messages[result.kind])); return; }
      if (result.kind === 'conflict') { const pre = this.node('pre', 'wb-git-conflict', result.text); content.append(pre); return; }
      if (!result.text) { content.append(this.node('p', 'wb-status', '没有文本差异，可能是权限变化、重命名或空文件。')); return; }
      const lines = result.text.split('\n'); if (lines[lines.length - 1] === '') lines.pop();
      let oldLine = 0, newLine = 0, added = 0, removed = 0, inHunk = false;
      const fragment = document.createDocumentFragment();
      for (const text of lines) {
        const hunk = text.match(/^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/);
        const line = this.node('div', 'wb-diff-line'); let old = '', next = '';
        if (hunk) { oldLine = Number(hunk[1]); newLine = Number(hunk[2]); inHunk = true; line.classList.add('hunk'); }
        else if (inHunk && text.startsWith('+')) { next = newLine++; added++; line.classList.add('added'); }
        else if (inHunk && text.startsWith('-')) { old = oldLine++; removed++; line.classList.add('removed'); }
        else if (inHunk && text.startsWith(' ')) { old = oldLine++; next = newLine++; }
        else line.classList.add('meta');
        const oldCell = this.node('span', 'wb-diff-number', old), nextCell = this.node('span', 'wb-diff-number', next);
        oldCell.setAttribute('aria-hidden', 'true'); nextCell.setAttribute('aria-hidden', 'true');
        line.append(oldCell, nextCell, this.node('code', '', text)); fragment.append(line);
      }
      content.append(fragment); header.append(this.node('span', 'wb-diff-summary', '+' + added + ' / −' + removed));
  }

  async execute(params, version = this.state.version) {
    if (this.busy) return;
    this.busy = true; this.actionError = '';
    for (const button of this.tab.body.querySelectorAll('button')) button.disabled = true;
    try {
      const result = await this.workbench.request(this.workbench.url(this.session, 'git-action'), {...params, version});
      this.workbench.notify(result.message);
      if (result.outcome !== 'success') this.actionError = result.message;
      return result;
    } catch (error) { this.actionError = error.message; throw error; }
    finally { this.busy = false; if (this.session.files?.includes(this.tab)) await this.load(); }
  }
  dialog(title, description, label, action, fields = []) {
    const dialog = this.node('dialog', 'picker wb-git-dialog'), form = this.node('form');
    const head = this.node('div', 'picker-head'), close = this.button('×', () => dialog.close(), '关闭');
    head.append(this.node('h2', '', title), close);
    const error = this.node('p', 'error'); error.setAttribute('role', 'alert');
    const submit = this.node('button', 'primary', label); submit.type = 'submit';
    form.append(head, this.node('p', 'muted', description), ...fields, error, submit); dialog.append(form);
    let pending = false;
    dialog.addEventListener('cancel', event => { if (pending) event.preventDefault(); });
    dialog.addEventListener('close', () => dialog.remove());
    form.onsubmit = async event => {
      event.preventDefault(); if (pending) return; pending = true; submit.disabled = close.disabled = true; error.textContent = '正在执行…';
      try { await action(); dialog.close(); }
      catch (failure) { error.textContent = failure.message; }
      finally { pending = false; submit.disabled = close.disabled = false; }
    };
    document.body.append(dialog); dialog.showModal(); return dialog;
  }
  confirm(title, description, params, label) {
    const version = this.state.version;
    this.dialog(title, description, label, () => this.execute(params, version));
  }
  commitDialog() {
    const version = this.state.version, list = this.node('ul', 'wb-commit-files');
    for (const entry of this.state.entries.filter(e => !e.untracked && e.index !== '.')) list.append(this.node('li', '', entry.path));
    if (!list.children.length && this.state.operation === 'merge') list.append(this.node('li', '', '文件内容不变，本次提交记录分支合并关系。'));
    const message = this.node('textarea'); message.required = true; message.rows = 4; message.placeholder = '提交说明'; message.setAttribute('aria-label', '提交说明');
    this.dialog('提交到 ' + this.state.branch, '仅提交下列已暂存的内容，保留未暂存改动。使用电脑或服务器现有的 Git 身份、签名和 hooks。', '确认提交', () => this.execute({action:'commit', message:message.value}, version), [list, message]);
  }
  graph(commit, lanes) {
    let lane = lanes.indexOf(commit.id), incoming = lane >= 0;
    if (lane < 0) { lane = lanes.length; lanes.push(commit.id); }
    const before = [...lanes]; lanes.splice(lane, 1);
    for (const [index, parent] of commit.parents.entries()) if (!lanes.includes(parent)) lanes.splice(index === 0 ? Math.min(lane, lanes.length) : lanes.length, 0, parent);
    const ns = 'http://www.w3.org/2000/svg', svg = document.createElementNS(ns, 'svg'), width = Math.max(before.length, lanes.length, 1) * 16 + 8;
    svg.setAttribute('viewBox', '0 0 ' + width + ' 76'); svg.setAttribute('width', width); svg.setAttribute('height', '76'); svg.setAttribute('class', 'wb-history-graph'); svg.setAttribute('aria-hidden', 'true');
    const x = column => 12 + column * 16;
    const line = (start, from, end, to, color) => { const p = document.createElementNS(ns, 'path'); p.setAttribute('d', 'M' + x(start) + ' ' + from + ' C' + x(start) + ' ' + ((from+to)/2) + ' ' + x(end) + ' ' + ((from+to)/2) + ' ' + x(end) + ' ' + to); p.setAttribute('class', 'wb-graph-lane lane-' + color % 5); svg.append(p); };
    before.forEach((id, index) => { if (id !== commit.id) line(index, 0, lanes.indexOf(id), 76, index); });
    if (incoming) line(lane, 0, lane, 30, lane);
    for (const parent of commit.parents) line(lane, 30, lanes.indexOf(parent), 76, lane);
    const dot = document.createElementNS(ns, 'circle'); dot.setAttribute('cx', x(lane)); dot.setAttribute('cy', '30'); dot.setAttribute('r', commit.parents.length > 1 ? '5' : '4'); dot.setAttribute('class', 'wb-graph-dot lane-' + lane % 5); svg.append(dot);
    return svg;
  }
  async history(controller) {
    const toolbar = this.node('div', 'wb-history-toolbar'), scope = this.node('select'); scope.setAttribute('aria-label', '历史范围');
    for (const [value, label] of [['all','所有分支与标签'],['current','当前分支']]) { const option = this.node('option', '', label); option.value = value; scope.append(option); }
    scope.value = this.historyRef || 'all'; scope.onchange = () => { this.historyRef = scope.value; this.historyLimit = 40; this.load(); };
    toolbar.append(scope, this.node('span', 'muted', '本地提交记录'));
    const layout = this.historyLayout = this.node('div', 'wb-git-layout wb-history-layout'), list = this.node('div', 'wb-git-list wb-history-list');
    this.collapseHistory = this.button('收起详情', () => { this.selectedCommit = null; layout.classList.remove('wb-history-selected'); this.collapseHistory.hidden = true; }); this.collapseHistory.hidden = true; toolbar.append(this.collapseHistory);
    this.detail = this.node('div', 'wb-git-detail'); this.detail.append(this.node('p', 'wb-empty', '选择提交查看说明和文件差异')); layout.append(list, this.detail);
    list.append(this.node('p', 'wb-status', '正在读取历史…')); this.tab.body.append(toolbar, layout);
    const result = await this.workbench.request(this.workbench.url(this.session, 'git-history') + '&limit=' + this.historyLimit + '&ref=' + scope.value, undefined, controller.signal);
    if (controller.signal.aborted) return;
    list.replaceChildren(); const lanes = [];
    for (const commit of result.commits) {
      const row = this.button('', () => this.showCommit(commit), commit.subject || commit.id.slice(0, 8)); row.className = 'wb-history-row'; row.dataset.commit = commit.id;
      const text = this.node('span', 'wb-history-text'); text.append(this.node('strong', '', commit.subject || '(无提交标题)'), this.node('small', '', commit.id.slice(0, 8) + ' · ' + commit.author + ' · ' + commit.date.slice(0, 10)));
      if (commit.refs) text.append(this.node('span', 'wb-history-refs', commit.refs));
      row.append(this.graph(commit, lanes), text); list.append(row);
    }
    if (!result.commits.length) list.append(this.node('p', 'wb-status', '尚无提交记录'));
    if (result.hasMore && this.historyLimit < 1000) list.append(this.button('加载更早的提交', () => { this.historyLimit += 40; this.load(); }));
    else if (result.hasMore) list.append(this.node('p', 'wb-status', '已显示最近 1,000 条提交'));
    if (this.selectedCommit) { const selected = result.commits.find(c => c.id === this.selectedCommit); if (selected) this.showCommit(selected); }
  }
  async showCommit(commit) {
    this.diffController?.abort(); this.historyDiffController?.abort(); const controller = this.diffController = new AbortController();
    this.selectedCommit = commit.id; this.historyLayout.classList.add('wb-history-selected'); this.collapseHistory.hidden = false;
    for (const row of this.tab.body.querySelectorAll('.wb-history-row')) { row.classList.toggle('selected', row.dataset.commit === commit.id); row.setAttribute('aria-pressed', String(row.dataset.commit === commit.id)); }
    this.detail.replaceChildren(this.node('p', 'wb-status', '正在读取提交…'));
    try {
      const result = await this.workbench.request(this.workbench.url(this.session, 'git-commit') + '&revision=' + commit.id, undefined, controller.signal);
      if (controller.signal.aborted) return;
      const info = this.node('details', 'wb-commit-info'), summary = this.node('summary', '', result.message.split('\n')[0] || commit.id.slice(0, 8));
      info.append(summary, this.node('p', '', result.message), this.node('small', '', result.author + ' · ' + result.date), this.node('code', '', result.id));
      const parents = this.node('div', 'wb-commit-parents');
      for (const parent of result.parents) parents.append(this.button('父提交 ' + parent.slice(0, 8), () => this.showCommit({id:parent})));
      info.append(parents);
      const picker = this.node('select', 'wb-history-file'); picker.setAttribute('aria-label', '提交中的文件');
      for (const entry of result.entries) { const option = this.node('option', '', entry.change + ' · ' + entry.path); option.value = entry.path; picker.append(option); }
      const label = this.node('p', 'wb-status', result.entries.length + ' 个文件' + (result.parents.length > 1 ? ' · 与第一父提交比较' : ''));
      const diffArea = this.node('div', 'wb-history-diff-area');
      picker.onchange = () => this.showHistoricalDiff(commit.id, picker.value, diffArea);
      this.detail.replaceChildren(info, label);
      if (result.entries.length) { this.detail.append(picker, diffArea); picker.onchange(); }
      else this.detail.append(this.node('p', 'wb-empty', '此提交没有当前项目范围内的文件变更'));
    } catch (error) { if (!controller.signal.aborted) this.detail.replaceChildren(this.node('p', 'wb-status error', error.message)); }
  }
  async showHistoricalDiff(revision, path, area) {
    this.historyDiffController?.abort(); const controller = this.historyDiffController = new AbortController();
    const header = this.node('div', 'wb-git-diff-head'), content = this.node('div', 'wb-git-diff'); content.tabIndex = 0;
    content.append(this.node('p', 'wb-status', '正在读取差异…')); area.replaceChildren(header, content);
    try {
      const result = await this.workbench.request(this.workbench.url(this.session, 'git-history-diff', path) + '&revision=' + revision, undefined, controller.signal);
      if (!controller.signal.aborted) this.renderDiff(result, content, header);
    } catch (error) { if (!controller.signal.aborted) content.replaceChildren(this.node('p', 'wb-status error', error.message)); }
  }
  async branches(controller) {
    const toolbar = this.node('div', 'wb-git-actions'), create = this.button('创建分支', () => {
      const name = this.node('input'); name.required = true; name.placeholder = '例如 feature/new-work'; name.setAttribute('aria-label', '新分支名称');
      const version = this.state.version;
      this.dialog('创建并切换分支', '新分支从当前提交开始。', '创建并切换', () => this.execute({action:'create-branch', branch:name.value.trim()}, version), [name]);
    });
    create.disabled = this.state.scope !== '.' || !!this.state.operation; toolbar.append(create); this.tab.body.append(toolbar);
    const list = this.node('div', 'wb-branch-list'); list.append(this.node('p', 'wb-status', '正在读取分支…')); this.tab.body.append(list);
    const result = await this.workbench.request(this.workbench.url(this.session, 'git-branches'), undefined, controller.signal);
    if (controller.signal.aborted) return;
    list.replaceChildren();
    for (const branch of result.branches) {
      const card = this.node('div', 'wb-branch-card'), name = this.node('div');
      name.append(this.node('strong', '', branch.name), this.node('small', '', (branch.current ? '当前分支 · ' : '') + branch.id.slice(0, 8) + (branch.upstream ? ' · ' + branch.upstream : '')));
      const actions = this.node('div', 'wb-git-actions');
      if (!branch.current) {
        const change = this.button('切换', () => this.confirm('切换分支', '从 ' + this.state.branch + ' 切换到 ' + branch.name + '。工作区文件会更新为目标分支的内容。', {action:'switch-branch', branch:branch.name}, '确认切换'));
        const merge = this.button('合并到当前分支', () => this.confirm('合并分支', '将 ' + branch.name + ' 合并到 ' + this.state.branch + '。可能产生合并提交或需要处理冲突。', {action:'merge', branch:branch.name}, '开始合并'));
        change.disabled = merge.disabled = this.state.scope !== '.' || !!this.state.operation; actions.append(change, merge);
      }
      card.append(name, actions); list.append(card);
    }
    if (!result.branches.length) list.append(this.node('p', 'wb-status', '首次提交后会显示分支记录'));
    if (this.state.entries.length) list.prepend(this.node('p', 'wb-status', '切换和合并前，请先提交或在电脑上保存未提交的改动。'));
  }

}
