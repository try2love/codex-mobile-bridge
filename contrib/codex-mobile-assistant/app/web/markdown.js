'use strict';
(() => {
  const markdown = window.markdownit({html:false, linkify:true, breaks:true});
  // Math delimiters must be consumed before Markdown removes backslash escapes.
  markdown.use(texmath, {engine:window.katex, delimiters:['brackets','dollars']});
  // Desktop visualization references are block content, not printable Markdown.
  markdown.block.ruler.before('paragraph', 'visualization', (state, startLine, endLine, silent) => {
    const start = state.bMarks[startLine] + state.tShift[startLine];
    const marker = '\ue200visualize\ue202';
    if (!state.src.startsWith(marker, start)) return false;
    if (silent) return true;
    const limit = endLine < state.bMarks.length ? state.bMarks[endLine] : state.src.length;
    const end = state.src.indexOf('\ue201', start + marker.length);
    const complete = end >= 0 && end < limit;
    let nextLine = startLine + 1;
    while (nextLine < endLine && state.bMarks[nextLine] <= (complete ? end : limit - 1)) nextLine++;
    const token = state.push('visualization', '', 0);
    token.block = true;token.map = [startLine, nextLine];token.meta = {complete};
    if (complete) {
      try { token.meta.descriptor = JSON.parse(state.src.slice(start + marker.length, end)); } catch {}
    }
    state.line = nextLine;
    return true;
  }, {alt:['paragraph']});
  const tags = new Set(['p','h1','h2','h3','h4','h5','h6','ul','ol','li','strong','em','s','blockquote','hr','br','table','thead','tbody','tr','th','td']);

  // Build nodes from parsed tokens; model output never becomes executable HTML.
  window.renderMarkdown = function(node, text, files = [], fileUrl = () => '') {
    const body = document.createElement('div');
    body.className = 'markdown-body';
    const artifacts = new Map(files.map(file => [markdown.normalizeLink(file.reference), file]));
    function target(reference) {
      const file = artifacts.get(reference);
      if (file) return {url:fileUrl(file), file};
      if (/^https?:\/\//i.test(reference)) return {url:reference};
      return null;
    }
    function link(url, title, file) {
      const anchor = document.createElement(file ? 'button' : 'a');
      if (file) {
        anchor.type = 'button';
        anchor.dataset.artifactUrl = url;
        anchor.dataset.filePath = file.absolutePath || file.reference.replace(/:\d+$/, '');
        anchor.className = 'artifact-location';
        anchor.setAttribute('aria-haspopup', 'dialog');
        anchor.title = '打开文件列表，选择文件后下载；长按查看路径';
      } else {
        anchor.href = url;
        anchor.target = '_blank';
        anchor.rel = 'noopener noreferrer';
      }
      if (title) anchor.title = title;
      return anchor;
    }
    function appendTokens(tokens, root) {
      const parents = [root];
      for (const token of tokens) {
        if (token.hidden) continue;
        const parent = parents[parents.length - 1];
        if (token.type === 'visualization') {
          const descriptor = token.meta.descriptor;
          const destination = typeof descriptor?.path === 'string' ? target(markdown.normalizeLink(descriptor.path)) : null;
          const preview = document.createElement('section');preview.className = 'visualization-preview';
          if (destination?.file?.visualization) {
            const heading = document.createElement('div');heading.className = 'visualization-heading';
            const title = document.createElement('strong');title.textContent = descriptor.title || '可视化预览';
            const file = link(destination.url, '查看预览文件', destination.file);file.textContent = '文件';
            heading.append(title, file);
            const frame = document.createElement('iframe');
            frame.title = title.textContent;frame.loading = 'lazy';frame.referrerPolicy = 'no-referrer';
            frame.setAttribute('sandbox', 'allow-scripts');
            frame.src = destination.url.replace(/\/files\/([a-f0-9]{64})(?=\?|$)/, '/visualizations/$1');
            preview.append(heading, frame);
          } else {
            preview.textContent = token.meta.complete ? '可视化文件暂不可用，请展开完整内容或重新加载对话。' : '可视化正在生成…';
          }
          parent.append(preview);
        } else if (token.type === 'inline') {
          appendTokens(token.children || [], parent);
        } else if (token.type.startsWith('math_')) {
          const display = token.block || token.type === 'math_inline_double';
          const formula = document.createElement(token.block ? 'div' : 'span');
          formula.className = display ? 'math-display' : 'math-inline';
          try {
            window.katex.render(token.content, formula, {
              displayMode:display, throwOnError:true, trust:false,
              maxExpand:1000, maxSize:20, strict:'ignore'
            });
          } catch {
            // Incomplete or unsupported TeX stays readable during streaming.
            const source = document.createElement('code');
            source.textContent = token.content;
            formula.append(source);
          }
          if (token.type.endsWith('_eqno')) {
            const number = document.createElement('span');
            number.className = 'math-number';
            number.textContent = '(' + token.info + ')';
            formula.append(number);
          }
          parent.append(formula);
        } else if (token.type === 'text') {
          parent.append(document.createTextNode(token.content));
        } else if (token.type === 'softbreak' || token.type === 'hardbreak') {
          parent.append(document.createElement('br'));
        } else if (token.type === 'code_inline' || token.type === 'code_block' || token.type === 'fence') {
          const code = document.createElement('code');
          code.textContent = token.content;
          if (token.type === 'code_inline') parent.append(code);
          else {
            const pre = document.createElement('pre');
            pre.append(code);
            const block=document.createElement('div');block.className='code-block';
            const tools=document.createElement('div');tools.className='code-block-tools';
            const language=document.createElement('span');language.textContent=(token.info||'').trim().split(/\s+/)[0]||'代码';
            const copy=document.createElement('button');copy.type='button';copy.className='plain';copy.textContent='复制代码';
            copy.onclick=()=>copyChatText(token.content);tools.append(language,copy);
            block.append(tools,pre);parent.append(block);
          }
        } else if (token.type === 'image') {
          const destination = target(token.attrGet('src') || '');
          const label = token.content || destination?.file?.name || '图片';
          if (destination?.file?.image) {
            const image = document.createElement('img');
            image.className = 'artifact-image';
            image.src = destination.url;
            image.alt = label;
            image.loading = 'lazy';
            image.dataset.filePath = destination.file.absolutePath || destination.file.reference.replace(/:\d+$/, '');
            enableImagePreview(image);
            parent.append(image);
          } else if (destination) {
            // Remote images remain links instead of making background requests.
            const anchor = link(destination.url, token.attrGet('title'), destination.file);
            anchor.textContent = label;
            parent.append(anchor);
          } else parent.append(document.createTextNode(label));
        } else if (token.nesting === -1) {
          parents.pop();
        } else {
          let element;
          if (token.type === 'link_open') {
            const destination = target(token.attrGet('href') || '');
            element = destination ? link(destination.url, token.attrGet('title'), destination.file) : document.createElement('span');
          } else if (tags.has(token.tag)) {
            element = document.createElement(token.tag);
            if (token.tag === 'ol' && token.attrGet('start')) element.start = Number(token.attrGet('start'));
            const alignment = /^text-align:(left|center|right)$/.exec(token.attrGet('style') || '');
            if (alignment) element.className = 'align-' + alignment[1];
          } else continue;
          if (token.tag === 'table') {
            const scroll = document.createElement('div');
            scroll.className = 'markdown-table';
            scroll.tabIndex = 0;
            scroll.setAttribute('role', 'region');
            scroll.setAttribute('aria-label', '表格，可横向滚动');
            scroll.append(element);
            parent.append(scroll);
          } else parent.append(element);
          if (token.nesting === 1) parents.push(element);
        }
      }
    }
    appendTokens(markdown.parse(String(text || ''), {}), body);
    node.append(body);
  };
})();
