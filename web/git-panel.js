'use strict';

class GitPanel {
  constructor(workbench, session, tab) {
    this.workbench = workbench; this.session = session; this.tab = tab;
    this.node = (...args) => workbench.node(...args);
    this.button = (...args) => workbench.button(...args);
  }
  dispose() { this.tab.controller?.abort(); this.diffController?.abort(); }
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
      const branch = result.branch === '(detached)' ? '分离 HEAD · ' + result.commit.slice(0, 8) : result.branch;
      title.replaceChildren(this.node('strong', '', branch || 'Git'), this.node('span', '', result.project + ' · ' + (this.session.host === 'local' ? '此电脑' : 'SSH')));
      title.firstChild.title = branch;
      const notes = [];
      if (result.commit === '(initial)') notes.push('尚无提交');
      if (result.scope !== '.') notes.push('仅显示当前项目目录');
      if (result.upstream) notes.push(result.upstream + (result.ahead === null ? '' : ' · 领先 ' + result.ahead + ' / 落后 ' + result.behind) + '（本地记录）');
      status.textContent = notes.join(' · '); status.hidden = !notes.length;
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
          row.append(badge, paths); group.append(row);
          if (this.selected === section + ':' + entry.path) restored = [entry, section, row];
        }
        this.list.append(group);
      }
      if (!result.entries.length) {
        layout.replaceChildren(this.node('div', 'wb-git-clean', '当前项目没有未提交的改动'));
        this.selected = null;
      } else if (restored) this.show(...restored);
    } catch (error) { if (!controller.signal.aborted) { status.textContent = error.message; status.classList.add('error'); } }
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
    } catch (error) { if (!controller.signal.aborted) content.replaceChildren(this.node('p', 'wb-status error', error.message), this.button('重试', () => this.show(entry, section, row))); }
  }
}
