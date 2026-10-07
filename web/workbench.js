'use strict';

class Workbench {
  constructor({chat, request, csrf, notify, onUnauthorized, openChat}) {
    Object.assign(this, {chat, request, csrf, notify, onUnauthorized, openChat});
    this.sessions = new Map();
    this.scrollPositions = new WeakMap();
    this.strip = this.node('nav', 'workbench-tabs'); this.strip.dataset.i18nAriaLabel='工作台标签';this.strip.setAttribute("data-i18n-aria-label",'工作台标签');this.strip.setAttribute('aria-label', BridgeI18n.t('工作台标签'));
    this.tabs = this.node('div', 'wb-tabs'); this.tabs.setAttribute('role', 'tablist');
    const add = this.button('＋', () => this.newTab(), BridgeI18n.t('新标签页')); add.className = 'wb-add';
    this.mainLabel=this.button('主会话',()=>{this.chat.querySelector('.timeline').focus();},'主会话');this.mainLabel.className='wb-main-label';
    this.toolStrip=this.node('div','wb-tool-strip');this.toolStrip.append(this.tabs,add);this.strip.append(this.mainLabel,this.toolStrip);
    const plus=document.createElementNS('http://www.w3.org/2000/svg','svg');plus.setAttribute('viewBox','0 0 24 24');plus.setAttribute('aria-hidden','true');plus.innerHTML='<path d="M12 5v14M5 12h14"/>';add.replaceChildren(plus);
    this.panel = this.node('section', 'workbench-panel'); this.panel.hidden = true;
    this.layout = this.node('div', 'wb-layout');
    this.mainPane = this.node('div', 'wb-main-pane');
    for (const child of [...chat.children]) if (!child.matches('.chat-head')) this.mainPane.append(child);
    this.divider = this.node('div', 'wb-divider'); this.divider.tabIndex=0;
    this.divider.setAttribute('role','separator'); this.divider.setAttribute('aria-orientation','vertical');
    this.divider.dataset.i18nAriaLabel='调整分屏宽度'; this.divider.setAttribute('aria-label',BridgeI18n.t('调整分屏宽度'));
    this.dropZone = this.node('div','wb-split-drop','拖到这里，在右侧打开');
    this.layout.append(this.mainPane,this.divider,this.panel,this.dropZone);
    chat.querySelector('.chat-head').after(this.strip,this.layout);
    this.splitButton=this.button('',()=>this.splitMode?this.unsplit():this.split(this.current?.active),'在右侧分屏');
    this.splitButton.className='wb-split-button';this.toolStrip.append(this.splitButton);
    const splitIcon=document.createElementNS('http://www.w3.org/2000/svg','svg');splitIcon.setAttribute('viewBox','0 0 24 24');splitIcon.setAttribute('aria-hidden','true');splitIcon.innerHTML='<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M12 4v16"/>';this.splitButton.append(splitIcon);
    this.divider.onpointerdown=event=>{
      if(event.button!==0||!this.splitMode)return;
      event.preventDefault();this.divider.setPointerCapture(event.pointerId);this.layout.classList.add('wb-resizing');
    };
    this.divider.onpointermove=event=>{
      if(this.divider.hasPointerCapture(event.pointerId)){const bounds=this.layout.getBoundingClientRect();this.setRatio((event.clientX-bounds.left)/bounds.width);}
    };
    this.divider.onpointerup=event=>{if(this.divider.hasPointerCapture(event.pointerId))this.divider.releasePointerCapture(event.pointerId);};
    this.divider.onlostpointercapture=()=>this.layout.classList.remove('wb-resizing');
    this.divider.onkeydown=event=>{if(['ArrowLeft','ArrowRight','Home'].includes(event.key)){event.preventDefault();this.setRatio(event.key==='Home'?.5:(this.current.splitRatio||.5)+(event.key==='ArrowLeft'?-.025:.025));}};
    this.dropZone.ondragover=event=>{if(this.dragTab&&this.canSplit()){event.preventDefault();event.dataTransfer.dropEffect='move';}};
    this.dropZone.ondrop=event=>{event.preventDefault();if(this.dragTab)this.split(this.dragTab);this.endDrag();};
    this.media=window.matchMedia('(min-width: 1100px)');
    this.media.addEventListener('change',()=>this.paint());
    this.resizeObserver=new ResizeObserver(()=>{const eligible=this.canSplit();if(eligible!==this.splitEligible)this.paint();else if(this.splitMode)this.setRatio(this.current.splitRatio||.5);});
    this.resizeObserver.observe(chat);
    this.mobileObserver=new MutationObserver(()=>this.paint());this.mobileObserver.observe(document.documentElement,{attributes:true,attributeFilter:['class']});

  }
  canSplit(){return this.media.matches&&!document.querySelector('.bridge-mobile')&&this.chat.clientWidth>=760;}
  get splitMode(){return this.canSplit()&&!!this.current?.splitTab;}
  isVisible(session,tab){return this.current===session&&(this.splitMode?session.splitTab===tab.id:session.active===tab.id);}
  setRatio(value){
    if(!this.current)return;
    const width=this.layout.clientWidth,minimum=Math.min(.5,360/Math.max(width,1));
    const ratio=Math.max(minimum,Math.min(1-minimum,value));this.current.splitRatio=ratio;
    this.chat.style.setProperty('--wb-left',`calc(${ratio*100}% - 4px)`);
    this.divider.setAttribute('aria-valuemin',String(Math.round(minimum*100)));this.divider.setAttribute('aria-valuemax',String(Math.round((1-minimum)*100)));this.divider.setAttribute('aria-valuenow',String(Math.round(ratio*100)));
  }
  split(id){if(!this.canSplit()||!this.current?.files.some(t=>t.id===id))return;this.rememberScroll();this.current.splitTab=id;this.current.active=id;this.paint();}
  unsplit(){if(!this.current)return;this.current.active=this.current.splitTab||this.current.active;this.current.splitTab=null;this.paint();}
  endDrag(){this.dragTab=null;this.layout.classList.remove('wb-dragging');}
  node(tag, cls = '', text) { const n = document.createElement(tag); n.className = cls; if (text !== undefined) { n._rawText=text;n.textContent = ['pre','code'].includes(tag)?text:BridgeI18n.t(text); const source=BridgeI18n.source(text); if(source&&!['pre','code'].includes(tag))n.dataset.i18n=source; } return n; }
  button(text, action, title = text) { const b = this.node('button', '', text); b.type = 'button'; b._rawTitle=title;b.title = BridgeI18n.t(title); b.setAttribute('aria-label', BridgeI18n.t(title)); const source=BridgeI18n.source(title); if(source){b.dataset.i18nTitle=source;b.dataset.i18nAriaLabel=source;} b.onclick = action; return b; }
  raw(node) { if(node._rawText!==undefined)node.textContent=node._rawText;if(node._rawTitle!==undefined){node.title=node._rawTitle;node.setAttribute('aria-label',node._rawTitle);}delete node.dataset.i18n;delete node.dataset.i18nTitle;delete node.dataset.i18nAriaLabel;return node; }
  relabel() {
    const refresh=root=>{for(const n of root.querySelectorAll('[data-i18n]'))if(![n.dataset.i18n,BridgeI18n.dictionary[n.dataset.i18n]].includes(n.children.length?[...n.childNodes].find(c=>c.nodeType===3)?.textContent:n.textContent))delete n.dataset.i18n;BridgeI18n.apply(root);for(const n of root.querySelectorAll('.wb-status,.wb-empty'))if(!n.children.length)n.textContent=BridgeI18n.t(n.textContent);};
    refresh(this.strip);refresh(this.panel);
    for(const session of this.sessions.values())for(const tab of session.files){refresh(tab.body);tab.sideChat?.relabel();}
    for(const dialog of document.querySelectorAll('.picker'))refresh(dialog);
  }
  size(bytes) { return bytes < 1024 ? bytes + ' B' : bytes < 1048576 ? (bytes / 1024).toFixed(1) + ' KB' : (bytes / 1048576).toFixed(1) + ' MB'; }
  fileIcon(entry) {
    const extension = entry.name.includes('.') ? entry.name.split('.').pop().toLowerCase() : '';
    const groups = {py:'python',pyw:'python',cmd:'terminal',bat:'terminal',sh:'terminal',bash:'terminal',zsh:'terminal',ps1:'terminal',js:'script',jsx:'script',ts:'typescript',tsx:'typescript',json:'config',yaml:'config',yml:'config',toml:'config',ini:'config',env:'config',md:'document',txt:'document',pdf:'pdf',png:'image',jpg:'image',jpeg:'image',gif:'image',webp:'image',svg:'image',zip:'archive',gz:'archive',tar:'archive','7z':'archive',rar:'archive',html:'web',css:'web',swift:'code',java:'code',go:'code',rs:'code',c:'code',cpp:'code'};
    const type = entry.kind === 'directory' ? 'folder' : entry.kind === 'blocked' ? 'blocked' : groups[extension] || 'file';
    const icon = this.node('span', 'wb-file-icon wb-icon-' + type); icon.setAttribute('aria-hidden', 'true');
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg'); svg.setAttribute('viewBox', '0 0 36 40'); svg.setAttribute('focusable', 'false');
    const path = (d, cls) => { const p = document.createElementNS(svg.namespaceURI, 'path'); p.setAttribute('d', d); p.setAttribute('class', cls); svg.append(p); };
    if (type === 'folder') {
      path('M3 11a3 3 0 0 1 3-3h8l4 4h12a3 3 0 0 1 3 3v17H3Z', 'wb-icon-fill');
      path('M3 16h30v15a3 3 0 0 1-3 3H6a3 3 0 0 1-3-3Z', 'wb-folder-front');
    } else if (type === 'terminal') {
      path('M5 4h26a3 3 0 0 1 3 3v26a3 3 0 0 1-3 3H5a3 3 0 0 1-3-3V7a3 3 0 0 1 3-3Z', 'wb-icon-fill');
      path('m9 11 5 5-5 5m10 0h7', 'wb-icon-stroke');
    } else {
      path('M7 2h15l9 9v24a3 3 0 0 1-3 3H7a3 3 0 0 1-3-3V5a3 3 0 0 1 3-3Z', 'wb-icon-paper');
      path('M22 2v9h9', 'wb-icon-stroke');
      if (type === 'image') path('M8 23l5-6 5 5 4-4 5 5M11 12h.01', 'wb-icon-stroke');
      if (type === 'blocked') path('M11 27 25 13m-10 0h10v10', 'wb-icon-stroke');
    }
    if (!['folder', 'blocked'].includes(type)) {
      const label = document.createElementNS(svg.namespaceURI, 'text'); label.setAttribute('x', '18'); label.setAttribute('y', type === 'terminal' ? '31' : '33'); label.setAttribute('text-anchor', 'middle');
      label.textContent = (extension || 'FILE').slice(0, 4).toUpperCase(); svg.append(label);
    }
    icon.append(svg);
    if (entry.kind === 'file' && /^(png|jpe?g|gif|webp)$/.test(extension) && entry.size <= 20 * 1024 * 1024) icon.dataset.imagePath = entry.path;
    return icon;
  }
  setThumbnails(enabled) {
    if (this.showThumbnails === enabled) return;
    this.showThumbnails = enabled;
    this.chat.classList.toggle('wb-show-thumbnails', enabled);
    this.refreshThumbnails();
  }
  stopThumbnails() { this.thumbnailObserver?.disconnect(); this.thumbnailController?.abort(); }
  refreshThumbnails() {
    this.stopThumbnails();
    const session = this.current;
    if (!this.showThumbnails || session?.active !== 'files') return;
    const controller = this.thumbnailController = new AbortController(), queue = [];
    let running = 0;
    const pump = () => {
      while (!controller.signal.aborted && running < 2 && queue.length) {
        const icon = queue.shift(); running++;
        this.thumbnail(session, icon, controller.signal).finally(() => { running--; pump(); });
      }
    };
    this.thumbnailObserver = new IntersectionObserver((entries, observer) => {
      for (const entry of entries) if (entry.isIntersecting) { observer.unobserve(entry.target); queue.push(entry.target); }
      pump();
    }, {root:this.panel, rootMargin:'80px'});
    for (const icon of this.panel.querySelectorAll('[data-image-path]')) if (!icon.querySelector('img') && !icon.dataset.thumbnailFailed) this.thumbnailObserver.observe(icon);
  }
  async thumbnail(session, icon, signal) {
    try {
      const result = await this.request(this.url(session, 'preview', icon.dataset.imagePath), undefined, signal);
      if (signal.aborted) return;
      if (result.kind !== 'image') throw Error('No image preview');
      const source = new Image(); source.src = 'data:' + result.mime + ';base64,' + result.data;
      await source.decode();
      if (signal.aborted) return;
      // Keep only a small bitmap in the list, not the original image payload.
      const canvas = document.createElement('canvas'), scale = Math.min(1, 96 / Math.max(source.naturalWidth, source.naturalHeight));
      canvas.width = Math.max(1, Math.round(source.naturalWidth * scale)); canvas.height = Math.max(1, Math.round(source.naturalHeight * scale));
      canvas.getContext('2d').drawImage(source, 0, 0, canvas.width, canvas.height);
      const image = this.node('img', 'wb-thumbnail'); image.alt = ''; image.src = canvas.toDataURL('image/png');
      icon.append(image); icon.classList.add('wb-has-thumbnail');
    } catch (error) { if (!signal.aborted) icon.dataset.thumbnailFailed = 'true'; }
  }
  open(id, host) {
    this.rememberScroll();
    const key = host + '|' + id;
    if (!this.sessions.has(key)) this.sessions.set(key, {id, host, key, active: 'chat', files: [], directory: '', search: '', hidden: false, scroll: 0});
    this.current = this.sessions.get(key); this.paint();
  }
  reset() { this.stopThumbnails(); this.uploadController?.abort(); for (const session of this.sessions.values()) for (const tab of session.files) { tab.controller?.abort(); tab.git?.dispose(); tab.terminal?.dispose(); tab.agents?.dispose(); tab.sideChat?.dispose(); } this.sessions.clear(); this.current = null; this.chat.classList.remove('workbench-active','wb-split');this.endDrag(); this.panel.replaceChildren(); this.panel.hidden = true; this.tabs.replaceChildren(); }
  get isFileVisible() { return !!this.current && !this.splitMode && this.current.active !== 'chat'; }
  url(session, operation = '', path = '') { return '/api/sessions/' + session.id + '/workspace' + (operation ? '/' + operation : '') + '?host=' + encodeURIComponent(session.host) + '&path=' + encodeURIComponent(path); }
  rememberScroll() { if (this.current && !this.isFileVisible) this.current.scroll = this.chat.querySelector('.timeline').scrollTop; }
  select(id) {
    if (!this.current) return;
    this.rememberScroll(); this.current.active = id; if(this.splitMode&&id!=='chat')this.current.splitTab=id; this.paint();
    if (id === 'chat') { const session = this.current; requestAnimationFrame(() => { if (session === this.current && session.active === 'chat') this.chat.querySelector('.timeline').scrollTop = session.scroll; }); }
  }
  paint() {
    const session = this.current; if (!session) return;
    for (const node of this.panel.querySelectorAll('.wb-file-list,.wb-preview-content,.wb-git-list,.wb-git-diff,.wb-branch-list')) this.scrollPositions.set(node, node.scrollTop);
    this.splitEligible=this.canSplit();
    this.tabs.replaceChildren();
    for (const tab of [{id: 'chat', name: BridgeI18n.t('聊天')}, ...session.files]) {
      const group = this.node('div', 'wb-tab');group.dataset.tab=tab.id; group.classList.toggle('selected', this.splitMode ? ['chat',session.splitTab].includes(tab.id) : session.active === tab.id);
      const select = this.button(tab.name, () => this.select(tab.id)); if(tab.id.startsWith('file:'))this.raw(select); select.setAttribute('role', 'tab'); select.setAttribute('aria-selected', String(this.splitMode?['chat',session.splitTab].includes(tab.id):session.active===tab.id));
      group.append(select);
      group.draggable=this.splitEligible&&tab.id!=='chat';
      group.ondragstart=event=>{if(!group.draggable){event.preventDefault();return;}this.dragTab=tab.id;event.dataTransfer.setData('text/plain',tab.id);event.dataTransfer.effectAllowed='move';this.layout.classList.add('wb-dragging');};
      group.ondragend=()=>this.endDrag();
      if (tab.id !== 'chat') group.append(this.button('×', () => this.close(tab.id), BridgeI18n.t('关闭')));
      this.tabs.append(group);
    }
    this.chat.classList.toggle('workbench-active', this.isFileVisible); this.panel.hidden = !this.isFileVisible&&!this.splitMode;
    this.chat.classList.toggle('wb-split',this.splitMode);this.splitButton.hidden=!this.splitEligible;this.splitButton.disabled=!this.splitMode&&session.active==='chat';
    const splitLabel=this.splitMode?'退出分屏':'在右侧分屏';this.splitButton.dataset.i18nTitle=splitLabel;this.splitButton.dataset.i18nAriaLabel=splitLabel;this.splitButton.title=BridgeI18n.t(splitLabel);this.splitButton.setAttribute('aria-label',BridgeI18n.t(splitLabel));this.splitButton.setAttribute('aria-pressed',String(this.splitMode));if(this.splitMode)this.setRatio(session.splitRatio||.5);
    this.panel.replaceChildren();
    const active = session.files.find(t => t.id === (this.splitMode?session.splitTab:session.active));
    if (active) {
      this.panel.append(active.body);
      for (const node of active.body.querySelectorAll('.wb-file-list,.wb-preview-content,.wb-git-list,.wb-git-diff,.wb-branch-list')) node.scrollTop = this.scrollPositions.get(node) || 0;
    }
    this.refreshThumbnails();
  }
  back() { const floating=this.panel.querySelector('.wb-float'); if(floating){floating.querySelector('.wb-float-close').click();return true;} if (!this.isFileVisible) return false; this.select('chat'); return true; }
  async close(id) {
    const session = this.current, index = session.files.findIndex(t => t.id === id);
    if (index < 0) return;
    if (session.files[index].sideChat && !await session.files[index].sideChat.end()) return;
    if (!session.files[index] || session.files[index].id !== id) return;
    session.files[index].controller?.abort(); session.files[index].git?.dispose(); session.files[index].terminal?.dispose(); session.files[index].agents?.dispose(); session.files[index].sideChat?.dispose(); session.files.splice(index, 1);
    if(session.splitTab===id)session.splitTab=null;
    if (this.current !== session) { if (session.active === id) session.active = 'chat'; return; }
    if (session.active === id) this.select(session.files.find(t => t.id === 'files') ? 'files' : 'chat'); else this.paint();
  }
  files() {
    const session = this.current; if (!session) return;
    if (!session.files.some(t => t.id === 'files')) {
      const tab = {id: 'files', name: BridgeI18n.t('文件'), body: this.node('div', 'wb-browser')}; session.files.unshift(tab);
      this.directory(session, tab, session.directory);
    }
    this.select('files');
  }
  newTab() {
    if (!this.current) return;
    const dialog = this.node('dialog', 'picker wb-new-tab'), head = this.node('div', 'picker-head');
    head.append(this.node('h2', '', BridgeI18n.t('新标签页')), this.button('×', () => dialog.close(), BridgeI18n.t('关闭')));
    const choices = this.node('div', 'wb-tab-choices');
    for (const [name, description, action] of [[BridgeI18n.t('文件'), BridgeI18n.t('浏览项目文件、上传与下载'), () => this.files()], ['Git', BridgeI18n.t('历史、暂存、提交与分支管理'), () => this.git()], [BridgeI18n.t('终端'), BridgeI18n.t('运行项目命令、构建与查看输出'), () => this.terminal()], [BridgeI18n.t('侧边聊天'), BridgeI18n.t('继承当前上下文，临时讨论并跨设备接续'), () => this.sideChat()], [BridgeI18n.t('子智能体'), BridgeI18n.t('查看派生任务关系与执行记录'), () => this.agents()]]) {
      const button = this.button('', () => { dialog.close(); action(); }, name);
      button.append(this.node('strong', '', name), this.node('span', '', description)); choices.append(button);
    }
    dialog.append(head, choices); dialog.addEventListener('close', () => dialog.remove()); document.body.append(dialog); dialog.showModal();
  }
  git() {
    const session = this.current; if (!session) return;
    if (!session.files.some(tab => tab.id === 'git')) {
      const tab = {id: 'git', name: 'Git', body: this.node('div', 'wb-git')};
      session.files.push(tab); tab.git = new GitPanel(this, session, tab); tab.git.load();
    }
    this.select('git');
  }
  agents() {
    const session=this.current;if(!session)return;
    if(!session.files.some(tab=>tab.id==='agents')) {
      const tab={id:'agents',name:BridgeI18n.t('子智能体'),body:this.node('div','wb-agents')};
      session.files.push(tab);tab.agents=new AgentsPanel(this,session,tab);
    }
    this.select('agents');
  }
  sideChat() {
    const session=this.current;if(!session)return;
    if(!session.files.some(tab=>tab.id==='sidechat')) {
      const tab={id:'sidechat',name:BridgeI18n.t('侧边聊天'),body:this.node('div','wb-side-content')};
      session.files.push(tab);tab.sideChat=new SideChatPanel(this,session,tab);
    }
    this.select('sidechat');
  }
  terminal() {
    const session=this.current;if(!session)return;
    if(!session.files.some(tab=>tab.id==='terminal')) {
      const tab={id:'terminal',name:BridgeI18n.t('终端'),body:this.node('div','wb-terminal')};
      session.files.push(tab);tab.terminal=new TerminalPanel(this,session,tab);
    }
    this.select('terminal');
  }
  async directory(session, tab, path, append = false) {
    tab.controller?.abort(); const controller = tab.controller = new AbortController();
    if (!append) {
      if (session === this.current) this.stopThumbnails();
      session.directory = path; tab.body.replaceChildren();
      const toolbar = this.node('div', 'wb-toolbar');
      const up = this.button('‹', () => this.directory(session, tab, path.split('/').slice(0, -1).join('/')), BridgeI18n.t('上一级目录')); up.disabled = !path;
      const crumb = this.node('div', 'wb-path', path || BridgeI18n.t('项目文件')); crumb.title = path || BridgeI18n.t('当前聊天的项目目录');
      toolbar.append(up, crumb, this.button('↻', () => this.directory(session, tab, path), BridgeI18n.t('刷新目录')), this.button(BridgeI18n.t('上传'), () => this.uploadDialog(session, tab, path)));
      const filters = this.node('div', 'wb-filters');
      const search = this.node('input'); search.type = 'search'; search.setAttribute("data-i18n-placeholder",'搜索当前目录');search.placeholder = BridgeI18n.t('搜索当前目录'); search.setAttribute("data-i18n-aria-label",'搜索当前目录');search.setAttribute('aria-label', BridgeI18n.t('搜索当前目录')); search.value = session.search;
      const form = this.node('form', 'wb-search'); form.append(search, this.button(BridgeI18n.t('搜索'), () => { session.search = search.value; this.directory(session, tab, path); }));
      form.onsubmit = e => { e.preventDefault(); session.search = search.value; this.directory(session, tab, path); };
      const label = this.node('label'), hidden = this.node('input'); hidden.type = 'checkbox'; hidden.checked = session.hidden;
      hidden.onchange = () => { session.hidden = hidden.checked; this.directory(session, tab, path); }; label.append(hidden, document.createTextNode(BridgeI18n.t('隐藏文件')));
      filters.append(form, label); tab.list = this.node('div', 'wb-file-list'); tab.status = this.node('p', 'wb-status', BridgeI18n.t('正在读取文件…')); tab.status.setAttribute('role', 'status');
      tab.body.append(toolbar, filters, tab.status, tab.list);
    }
    try {
      const url = this.url(session, '', path) + '&hidden=' + session.hidden + '&search=' + encodeURIComponent(session.search) + '&offset=' + (append ? tab.next : 0);
      const result = await this.request(url, undefined, controller.signal);
      if (controller.signal.aborted || !this.sessions.has(session.key)) return;
      session.project = result.project; tab.body.querySelector('.wb-path').textContent = result.project + (path ? ' / ' + path : '');
      tab.status.textContent = result.total ? result.total + BridgeI18n.t(' 项 · ') + (session.host === 'local' ? BridgeI18n.t('电脑上的项目') : BridgeI18n.t('SSH 项目')) : BridgeI18n.t('当前目录没有匹配的文件');
      tab.body.querySelector('.wb-load-more')?.remove();
      for (const entry of result.entries) {
        const row = this.button('', () => { if (entry.kind === 'directory') { session.search = ''; this.directory(session, tab, entry.path); } else this.preview(session, entry); }, entry.name);
        row.className = 'wb-file'; row.disabled = entry.kind === 'blocked';
        row.append(this.fileIcon(entry), this.raw(this.node('span', 'wb-file-name', entry.name)), this.node('span', 'wb-file-meta', entry.kind === 'directory' ? BridgeI18n.t('文件夹') : entry.kind === 'blocked' ? BridgeI18n.t('链接或特殊文件') : this.size(entry.size)));
        tab.list.append(row);
      }
      tab.next = result.nextOffset;
      if (tab.next !== null) { const more = this.button(BridgeI18n.t('加载更多'), () => this.directory(session, tab, path, true)); more.className = 'wb-load-more'; tab.body.append(more); }
      if (session === this.current) this.refreshThumbnails();
    } catch (error) { if (error.name !== 'AbortError') { tab.status.textContent=BridgeI18n.t(error.message); tab.status.classList.add('error'); } }
  }
  async preview(session, entry) {
    if (session !== this.current) return;
    const id = 'file:' + entry.path;
    if (session.files.some(t => t.id === id)) { this.select(id); return; }
    if (session.files.filter(t => t.id.startsWith('file:')).length >= 8) { this.notify(BridgeI18n.t('最多同时打开 8 个文件，请先关闭一个标签')); return; }
    const tab = {id, name: entry.name, body: this.node('div', 'wb-preview'), controller: new AbortController()}; session.files.push(tab);
    tab.body.append(this.node('p', 'wb-status', BridgeI18n.t('正在读取文件…'))); this.select(id);
    try {
      const result = await this.request(this.url(session, 'preview', entry.path), undefined, tab.controller.signal);
      if (tab.controller.signal.aborted || !this.sessions.has(session.key)) return;
      const head = this.node('div', 'wb-preview-head'), title = this.node('div');
      title.append(this.raw(this.node('strong', '', result.name)), this.node('small', '', this.size(result.size) + ' · ' + entry.path));
      head.append(title);
      if (result.size <= 20 * 1024 * 1024) { const download = this.node('a', 'wb-download', BridgeI18n.t('下载')); download.href = this.url(session, 'download', entry.path); download.download = result.name; head.append(download); }
      const content = this.node('div', 'wb-preview-content');
      if (result.kind === 'image') { const image = this.node('img'); image.src = 'data:' + result.mime + ';base64,' + result.data; image.alt = result.name; content.append(image); }
      else if (result.kind === 'text') {
        if (/\.md$/i.test(result.name)) renderMarkdown(content, result.text);
        else { const pre = this.node('pre'); pre.append(this.node('code', '', result.text)); content.append(pre); }
      } else content.append(this.node('p', 'wb-empty', result.size > 20 * 1024 * 1024 ? BridgeI18n.t('文件超过 20 MB，请在电脑上处理。') : BridgeI18n.t('此文件暂不支持在线预览，可下载后打开。')));
      tab.body.replaceChildren(head, content);
    } catch (error) { if (error.name !== 'AbortError') tab.body.replaceChildren(this.node('p', 'wb-status error', error.message), this.button(BridgeI18n.t('重试'), () => { this.close(id); this.preview(session, entry); })); }
  }
  uploadDialog(session, tab, directory) {
    const dialog = this.node('dialog', 'picker wb-upload'), form = this.node('form');
    const file = this.node('input'); file.type = 'file'; file.required = true; file.setAttribute("data-i18n-aria-label",'选择上传文件');file.setAttribute('aria-label', BridgeI18n.t('选择上传文件'));
    const name = this.node('input'); name.required = true; name.setAttribute("data-i18n-aria-label",'保存文件名');name.setAttribute('aria-label', BridgeI18n.t('保存文件名')); name.setAttribute("data-i18n-placeholder",'保存文件名');name.placeholder = BridgeI18n.t('保存文件名');
    file.onchange = () => { name.value = file.files[0]?.name || ''; };
    const error = this.node('p', 'error'); error.setAttribute('role', 'alert'); const progress = this.node('progress'); progress.max = 100; progress.value = 0; progress.hidden = true;
    const status = this.node('p', 'wb-status'); status.setAttribute('role', 'status');
    const submit = this.node('button', 'primary', BridgeI18n.t('上传到此目录')); submit.type = 'submit';
    const cancel = this.button(BridgeI18n.t('取消'), () => dialog.close()); cancel.className = 'wb-upload-cancel'; const actions = this.node('div', 'wb-upload-actions'); actions.append(cancel, submit);
    form.append(this.node('h2', '', BridgeI18n.t('上传文件')), this.node('p', 'wb-upload-target', BridgeI18n.t('保存到：') + (session.project || BridgeI18n.t('项目')) + '/' + directory), file, name, this.node('p', 'muted', BridgeI18n.t('单个文件最多 20 MB。同名文件不会被覆盖。')), progress, status, error, actions);
    dialog.append(form); document.body.append(dialog);
    dialog.onclose = () => { this.uploadController?.abort(); this.uploadController = null; dialog.remove(); };
    form.onsubmit = async event => {
      event.preventDefault(); const selected = file.files[0]; if (!selected || submit.disabled) return;
      if (selected.size > 20 * 1024 * 1024 || !name.value || /[\\/:\x00-\x1f]/.test(name.value) || ['.', '..'].includes(name.value)) { error.textContent = BridgeI18n.t('请选择不超过 20 MB 的文件，并填写不含路径的文件名'); return; }
      error.textContent = ''; progress.hidden = false; progress.value = 0; submit.disabled = true; file.disabled = name.disabled = true;
      const path = (directory ? directory + '/' : '') + name.value;
      try {
        const result = await this.upload(this.url(session, 'upload', path), selected, value => { progress.value = value; status.textContent = value === 100 ? BridgeI18n.t('正在确认保存结果…') : BridgeI18n.t('正在上传 ') + value + '%'; });
        dialog.close(); this.notify(result.existing ? BridgeI18n.t('同名文件内容一致，已核对保存结果') : BridgeI18n.t('文件已上传到项目目录'));
        if (this.sessions.has(session.key) && session.files.includes(tab) && session.directory === directory) this.directory(session, tab, directory);
      } catch (failure) { error.textContent=BridgeI18n.t(failure.message); status.textContent = ''; }
      finally { submit.disabled = false; file.disabled = name.disabled = false; }
    };
    dialog.showModal();
  }
  upload(url, file, progress) {
    return new Promise((resolve, reject) => {
      const xhr = this.uploadController = new XMLHttpRequest(); xhr.open('POST', url); xhr.timeout = 120000;
      xhr.setRequestHeader('X-CSRF-Token', this.csrf()); xhr.setRequestHeader('Content-Type', 'application/octet-stream');
      xhr.upload.onprogress = e => { if (e.lengthComputable) progress(Math.round(e.loaded / e.total * 100)); };
      const uncertain = () => reject(Error(BridgeI18n.t('上传结果未确认，请刷新目录核对，或重试同一文件；不会覆盖已有文件。')));
      xhr.onerror = xhr.ontimeout = xhr.onabort = uncertain;
      xhr.onload = () => { try { const result = JSON.parse(xhr.responseText); if (xhr.status === 401) this.onUnauthorized(); if (xhr.status < 200 || xhr.status >= 300) throw Error(result.error || BridgeI18n.t('上传失败')); resolve(result); } catch (e) { reject(e); } };
      xhr.send(file);
    });
  }
}
