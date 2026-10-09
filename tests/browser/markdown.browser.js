/* Run in a browser with web/vendor/markdown-it.min.js, web/shared/markdown.js and
 * web/shell/style.css loaded. The result reports each checked rendering behavior. */
(() => {
  const checks = [];
  function check(name, condition) {
    if (!condition) throw new Error(name);
    checks.push(name);
  }
  function render(text, files, fileUrl) {
    const node = document.createElement('div');
    node.className = 'message assistant';
    renderMarkdown(node, text, files, fileUrl);
    return node;
  }
  const sample = '# 标题\n\n## 小标题\n\n**重点**、*强调*、~~删除~~与 `inline()`。\n下一行\n\n- 第一项\n  - 子项\n- 第二项\n\n3. 第三步\n4. 第四步\n\n> 引用内容\n\n---\n\n| 名称 | 状态 |\n| :--- | ---: |\n| 会话 | 可用 |\n\n```js\nconst html = "<script>not executable</script>";\n```';
  const node = render(sample);
  check('headings and inline formatting', node.querySelector('h1')?.textContent === '标题' && node.querySelector('h2') && node.querySelector('strong')?.textContent === '重点' && node.querySelector('em') && node.querySelector('s'));
  check('nested lists and ordered start', node.querySelector('ul ul li')?.textContent === '子项' && node.querySelector('ol')?.start === 3);
  check('blockquote, rule and line breaks', node.querySelector('blockquote p') && node.querySelector('hr') && node.querySelector('p br'));
  check('table cells and alignment', node.querySelectorAll('table th').length === 2 && node.querySelector('td.align-right')?.textContent === '可用');
  check('literal code', node.querySelector('p code')?.textContent === 'inline()' && node.querySelector('pre code')?.textContent === 'const html = "<script>not executable</script>";\n' && !node.querySelector('script'));

  const unsafe = render('<script>window.markdownInjected=true</script>\n\n<img src=x onerror=alert(1)>\n\n[bad](javascript:alert%281%29) [encoded](jav&#x61;script:alert%281%29) [data](data:text/html,bad) [relative](/api/logout) [protocol](//example.com)\n\n![remote](https://example.com/tracker.png)\n\n[site](https://example.com/path?q=1 "说明") https://example.com/plain');
  check('HTML stays text', !unsafe.querySelector('script,img,iframe,svg,style') && unsafe.textContent.includes('<img src=x onerror=alert(1)>'));
  check('safe external links only', unsafe.querySelectorAll('a').length === 3 && [...unsafe.querySelectorAll('a')].every(a => a.protocol === 'https:' && a.target === '_blank' && a.rel === 'noopener noreferrer'));
  check('no inline handlers or styles', [...unsafe.querySelectorAll('*')].every(el => [...el.attributes].every(a => !/^on|^style$/i.test(a.name))));

  const files = [{id:'report', reference:'/workspace/报告 v1.md:12', name:'报告 v1.md', image:false}, {id:'image', reference:'/workspace/图 表.png', name:'图 表.png', image:true}];
  const host = 'remote-ssh-discovered:example';
  const artifacts = render('[报告](</workspace/报告 v1.md:12>)\n\n![图](</workspace/图 表.png>)', files, file => '/api/sessions/test/files/' + file.id + '?host=' + encodeURIComponent(host));
  check('workspace links keep authenticated host route', artifacts.querySelector('a')?.getAttribute('href') === '/api/sessions/test/files/report?host=' + encodeURIComponent(host));
  check('local images keep authenticated host route', artifacts.querySelector('img')?.getAttribute('src') === '/api/sessions/test/files/image?host=' + encodeURIComponent(host));

  for (let i = 0; i <= sample.length; i++) render(sample.slice(0, i));
  check('partial streaming Markdown does not throw', true);

  const layout = render('| 列一 | 列二 | 列三 | 列四 |\n| --- | --- | --- | --- |\n| 长内容 | 长内容 | 长内容 | 长内容 |\n\n```\n' + 'abcdefgh '.repeat(60) + '\n```\n\n' + 'longword'.repeat(60));
  const fixture = document.createElement('div');
  fixture.style.width = '300px';
  fixture.append(layout);
  document.body.append(fixture);
  const table = layout.querySelector('.markdown-table'), pre = layout.querySelector('pre');
  const fits = fixture.scrollWidth <= 301 && layout.scrollWidth <= 301;
  const scrolls = table.scrollWidth > table.clientWidth && pre.scrollWidth > pre.clientWidth;
  fixture.remove();
  check('tables and code scroll without page overflow', fits && scrolls);
  return {passed:checks.length, checks};
})();
