'use strict';
(() => {
  const markdown = window.markdownit({html:false, linkify:true, breaks:true});
  // Math delimiters must be consumed before Markdown removes backslash escapes.
  markdown.use(texmath, {engine:window.katex, delimiters:['brackets','dollars']});
  window.BridgeOutputDirectives?.install(markdown);
  const tags = new Set(['p','h1','h2','h3','h4','h5','h6','ul','ol','li','strong','em','s','blockquote','hr','br','table','thead','tbody','tr','th','td']);

  // Build nodes from parsed tokens; model output never becomes executable HTML.
  window.renderMarkdown = function(node, text, files = [], fileUrl = () => '', options = {}) {
    const body = document.createElement('div');
    body.className = 'markdown-body';
    const artifacts = new Map(files.flatMap(file =>
      [file.reference, ...(file.references || [])].flatMap(reference =>
        [reference, markdown.utils.unescapeAll(reference)].map(value => [markdown.normalizeLink(value), file]))));
    function parse(source) {
      const validate = markdown.validateLink;
      // Only server-authorized file URLs may pass the parser's local-URL filter.
      // Rendering still replaces them with authenticated gateway routes below.
      markdown.validateLink = reference => validate(reference) || /^file:\/\//i.test(reference) && artifacts.has(reference);
      try { return markdown.parse(source, {}); }
      finally { markdown.validateLink = validate; }
    }
    function target(reference) {
      const file = artifacts.get(reference);
      if (file) return {url:fileUrl(file), file};
      if (window.BridgeOutputDirectives?.isReviewTarget(reference)) return {url:reference, review:true};
      if (/^https?:\/\//i.test(reference)) return {url:reference};
      return null;
    }
    function link(url, title) {
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.target = '_blank';
      anchor.rel = 'noopener noreferrer';
      if (window.BridgeOutputDirectives?.isReviewTarget(url)) anchor.onclick=event=>{event.preventDefault();window.BridgeOutputDirectives?.review(url);};
      if (title) anchor.title = title;
      return anchor;
    }
    let codeIndex=0;
    const lines=String(text || '').split(/\r?\n/);
    function completeFence(token) {
      if (!token.map || !token.markup) return false;
      const last=(lines[token.map[1]-1] || '').replace(/^[ \t]*(?:>[ \t]*)+/, '');
      const marker=token.markup[0], count=token.markup.length;
      return new RegExp('^[ \\t]*'+(marker==='`'?'`':'~')+'{'+count+',}[ \\t]*$').test(last);
    }
    function appendTokens(tokens, root) {
      const parents = [root];
      const media = new Map();
      for (const token of tokens) {
        if (token.hidden) continue;
        const parent = parents[parents.length - 1];
        if (token.type === 'inline') {
          appendTokens(token.children || [], parent);
        } else if (token.type.startsWith('math_')) {
          const display = token.block || token.type === 'math_inline_double';
          const formula = document.createElement(token.block ? 'div' : 'span');
          formula.className = display ? 'math-display' : 'math-inline';
          const expression = document.createElement('span');
          formula.append(expression);
          const source = () => { const code=document.createElement('code');code.textContent=token.content;expression.replaceChildren(code); };
          const render = () => {
            try { window.katex.render(token.content, expression, {
              displayMode:display, throwOnError:true, trust:false,
              maxExpand:1000, maxSize:20, strict:'ignore'
            }); } catch { source(); }
          };
          if(window.katex)render();
          else {
            source();
            // Only replace this formula, never rerender the message or composer.
            const load = () => window.BridgeFeatures?.load('math').then(() => {
              if(formula.isConnected)render();
            }).catch(() => {
              if(!formula.isConnected)return;
              const retry=document.createElement('button');retry.type='button';retry.className='plain';
              retry.textContent=typeof BridgeI18n==='undefined'?'重试':BridgeI18n.t('重试');
              retry.onclick=()=>{retry.remove();load();};expression.append(retry);
            });
            load();
          }
          if (token.type.endsWith('_eqno')) {
            const number = document.createElement('span');
            number.className = 'math-number';
            number.textContent = '(' + token.info + ')';
            formula.append(number);
          }
          parent.append(formula);
        } else if (token.type === 'output_directive') {
          parent.append(window.BridgeOutputDirectives.create(token.meta, reference => {
            const file=artifacts.get(markdown.normalizeLink(reference));
            return file ? {url:fileUrl(file),file} : null;
          }));
        } else if (token.type === 'text') {
          parent.append(document.createTextNode(token.content));
        } else if (token.type === 'softbreak' || token.type === 'hardbreak') {
          parent.append(document.createElement('br'));
        } else if (token.type === 'code_inline' || token.type === 'code_block' || token.type === 'fence') {
          const code = document.createElement('code');
          code.textContent = token.content;
          if (token.type === 'code_inline') parent.append(code);
          else {
            const index=codeIndex++;
            const getSource=()=>{
              if(!options.fullText)return token.content;
              const extract=complete=>{
                const blocks=parse(complete).filter(t=>t.type==='fence'||t.type==='code_block');
                if(!blocks[index])throw Error('读取完整正文失败，请重试');
                return blocks[index].content;
              };
              const complete=options.fullText();
              return typeof complete==='string'?extract(complete):Promise.resolve(complete).then(extract);
            };
            if(token.type==='fence' && /^mermaid(?:\s|$)/i.test(token.info.trim()) && completeFence(token) && window.BridgeMermaidCards){
              parent.append(window.BridgeMermaidCards.create(token.content,{owner:node,index,getSource}));
              continue;
            }
            const pre = document.createElement('pre');
            pre.append(code);
            const wrapper=document.createElement('div');wrapper.className='code-block';
            const syntax=window.BridgeCodeSyntax, language=syntax.languageFor(token.info);
            const header=document.createElement('div');header.className='code-header';
            const label=document.createElement('span');label.className='code-language';label.dataset.language=language || '';label.textContent=syntax.label(language);
            const button=document.createElement('button');button.className='plain code-copy';button.type='button';button.textContent=typeof BridgeI18n==='undefined'?'复制代码':BridgeI18n.t('复制代码');
            button.onclick=()=>window.BridgeClipboard?.copy(getSource,button);
            header.append(label,button);wrapper.append(header,pre);parent.append(wrapper);
            if(language && syntax.eligible(token.content)){
              const highlight=()=>{code.replaceChildren();syntax.append(code,syntax.tokenize(token.content,language));};
              if(window.Prism)highlight();
              else window.BridgeFeatures?.load('syntax').then(()=>{if(code.isConnected)highlight();}).catch(()=>{});
            }
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
            image.dataset.imagePreview = destination.url; image.dataset.imageName = label;
            image.tabIndex = 0; image.setAttribute('role', 'button');
            parent.append(image);
          } else if (destination) {
            // Remote images remain links instead of making background requests.
            const anchor = link(destination.url, token.attrGet('title'));
            anchor.textContent = label;
            parent.append(anchor);
            const player=destination.file && window.BridgeChatMedia?.create(destination.file.name,destination.url);
            if(player)parent.append(player);
          } else parent.append(document.createTextNode(label));
        } else if (token.nesting === -1) {
          const closed=parents.pop(),player=media.get(closed);
          if(player)parents[parents.length-1].append(player);
        } else {
          let element;
          if (token.type === 'link_open') {
            const destination = target(token.attrGet('href') || '');
            element = destination ? link(destination.url, token.attrGet('title')) : document.createElement('span');
            if (destination?.file?.image) { element.dataset.imagePreview = destination.url; element.dataset.imageName = destination.file.name; }
            const player=destination?.file && window.BridgeChatMedia?.create(destination.file.name,destination.url);
            if(player)media.set(element,player);
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
    appendTokens(parse(String(text || '')), body);
    node.append(body);
  };
})();
