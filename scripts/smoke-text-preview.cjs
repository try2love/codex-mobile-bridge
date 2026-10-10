'use strict';
const fs = require('node:fs'), path = require('node:path'), http = require('node:http'), assert = require('node:assert/strict');
const root = path.resolve(__dirname, '..'), out = path.join(root, '.tmp/text-preview-check');
fs.mkdirSync(out, {recursive:true});
const read = file => fs.readFileSync(path.join(root, file), 'utf8');
const session = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee';
const route = id => `/api/sessions/${session}/files/${id.repeat(64)}?host=local`;
const names = {a:'plan.md', b:'settings.json', c:'page.html', d:'binary.txt', e:'large.txt', f:'broken.txt',
  1:'notes.txt', 2:'empty.txt', 3:'delayed.md', 4:'script.py', 5:'chunked.txt', 6:'retry.txt', 7:'limit.txt'};
const files = Object.entries(names).map(([id, name]) => ({id:id.repeat(64), reference:name, name, image:false}));
const markdown = '# Preview title\n\n**Readable document**\n\n| Name | Value |\n| --- | --- |\n| Preview | Works |\n\n```js\nconst answer = 42;\n```\n\n<script>window.injected=true</script>';
const sources = {a:markdown, b:'{\n  "name": "preview",\n  "html": "<img src=x onerror=alert(1)>"\n}',
  c:'<!doctype html><h1>HTML download</h1>', d:Buffer.from([0xff, 0xfe, 0]),
  e:'x'.repeat(1024 * 1024 + 1), f:'failed', 1:'中文文本\n\t缩进保留', 2:'',
  3:'# Delayed result', 4:'print("<script>source only</script>")', 5:'x'.repeat(1024 * 1024 + 1), 6:'Retry succeeded', 7:'x'.repeat(1024 * 1024)};
const assets = ['web/vendor/markdown-it.min.js', 'web/vendor/katex/katex.min.js', 'web/vendor/texmath.js',
  'web/markdown.js', 'web/i18n.js', 'web/downloads.js', 'web/text-viewer.js'];
const styles = ['web/style.css', 'web/presentation.css', 'web/downloads.css', 'web/text-viewer.css'];
const html = '<!doctype html><html lang="zh"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/fixture.css">' +
  '<body><div id="app" class="chat-open"><div id="timeline"><div class="spacer"></div><div id="files"></div></div>' +
  '<textarea id="draft">Keep draft</textarea></div>' + assets.map(file => `<script src="/${file}"></script>`).join('') +
  `<script>renderMarkdown(document.getElementById('files'),${JSON.stringify(files.map(f => `[${f.name}](${f.reference})`).join('\n'))},${JSON.stringify(files)},f=>'/api/sessions/${session}/files/'+f.id+'?host=local');document.getElementById('timeline').scrollTop=600;</script></body></html>`;
const requests = [];
let retries = 0;
const server = http.createServer((req, res) => {
  const url = new URL(req.url, 'http://localhost');
  if (url.pathname === '/fixture.css') {
    res.writeHead(200, {'Content-Type':'text/css; charset=utf-8'});
    res.end(styles.map(read).join('\n') + '#timeline{height:220px;overflow:auto}.spacer{height:600px}'); return;
  }
  if (url.pathname.startsWith('/web/')) {
    const file = url.pathname.slice(1);
    if (!assets.includes(file)) { res.writeHead(404); res.end(); return; }
    res.writeHead(200, {'Content-Type':'text/javascript; charset=utf-8'}); res.end(read(file)); return;
  }
  const match = /\/files\/([a-f0-9]{64})$/.exec(url.pathname);
  if (!match) {
    if (url.pathname === '/api/auth') { res.end(JSON.stringify({authenticated:true,csrf:'fixture'})); return; }
    res.writeHead(200, {'Content-Type':'text/html', 'Set-Cookie':'preview=ok; SameSite=Strict'}); res.end(html); return;
  }
  requests.push({id:match[1][0], range:req.headers.range, cookie:req.headers.cookie, host:url.searchParams.get('host')});
  const id = match[1][0];
  if (id === 'f' || (id === '6' && retries++ === 0)) { res.writeHead(503); res.end(); return; }
  const data = Buffer.from(sources[id]);
  if (id === '2') { res.writeHead(416, {'Content-Range':'bytes */0'}); res.end(); return; }
  if (id === '3') { setTimeout(() => { res.writeHead(200); res.end(data); }, 300); return; }
  if (id === '5') { res.writeHead(200); res.write(data.subarray(0, 100)); res.end(data.subarray(100)); return; }
  const headers = {'Content-Type':'application/octet-stream', 'Content-Disposition':`attachment; filename="${names[id]}"`,
    'Accept-Ranges':'bytes', ETag:'"fixture"', 'Content-Length':data.length};
  if (req.headers.range && data.length) { headers['Content-Range'] = `bytes 0-${data.length - 1}/${data.length}`; res.writeHead(206, headers); }
  else res.writeHead(200, headers);
  res.end(data);
});

if (process.argv.includes('--serve-only')) {
  server.listen(Number(process.env.TEXT_PREVIEW_PORT || 0), '127.0.0.1', () => fs.writeFileSync(path.join(out, 'server.json'), JSON.stringify({port:server.address().port})));
} else {
  const {app, BrowserWindow} = require('electron');
  app.disableHardwareAcceleration();
  app.setPath('userData', path.join(out, 'electron-data'));
  app.whenReady().then(async () => {
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    const base = 'http://127.0.0.1:' + server.address().port;
    const window = new BrowserWindow({show:false, width:390, height:844});
    const run = async code => { try { return await window.webContents.executeJavaScript(code); } catch (error) { throw new Error(code + ': ' + error); } }, checks = [];
    const check = (ok, name) => { assert.ok(ok, name); checks.push(name); };
    const until = async code => {
      const end = Date.now() + 5000;
      while (Date.now() < end) { if (await run(code)) return; await new Promise(resolve => setTimeout(resolve, 20)); }
      throw Error('Timed out: ' + code + ' status=' + await run("document.querySelector('.text-preview-status')?.textContent"));
    };
    const click = name => run(`document.querySelector('a[href*="${name.repeat(64)}"]').click()`);
    const close = async () => { await run("document.querySelector('#text-preview-dialog').close()"); await until("!document.querySelector('#text-preview-dialog').open"); await run('new Promise(resolve=>setTimeout(resolve,30))'); };
    await window.loadURL(base);
    check(await run("document.querySelectorAll('[data-text-preview]').length===12"), 'Text whitelist includes MD/JSON/code and leaves HTML as download');
    const before = await run("document.querySelector('#timeline').scrollTop");
    await click('a'); await until("!!document.querySelector('#text-preview-dialog h1')");
    check(await run("document.querySelector('#text-preview-dialog h1').textContent==='Preview title' && !!document.querySelector('#text-preview-dialog table') && !!document.querySelector('#text-preview-dialog .code-copy')"), 'Markdown uses existing table and code renderer');
    check(await run("!window.injected && !document.querySelector('#text-preview-dialog script') && !document.querySelector('.download-panel:not([hidden])')"), 'Preview is safe and does not start a download');
    check(requests[0].cookie === 'preview=ok' && requests[0].host === 'local' && requests[0].range === 'bytes=0-1048576', 'Preview keeps authentication, host selection and bounded range');
    fs.writeFileSync(path.join(out, 'mobile.png'), (await window.webContents.capturePage()).toPNG());
    await run("document.documentElement.dataset.theme='dark'"); window.setSize(1100, 800);
    await run('new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))');
    fs.writeFileSync(path.join(out, 'desktop-dark.png'), (await window.webContents.capturePage()).toPNG());
    await close(); check(await run("document.querySelector('#timeline').scrollTop") === before && await run("document.querySelector('#draft').value==='Keep draft'"), 'Closing keeps chat scroll and draft');
    await click('b'); await until("!!document.querySelector('.text-preview-source')");
    check(await run("document.querySelector('.text-preview-source').textContent.includes('  \"name\"') && !document.querySelector('.text-preview-content img')"), 'JSON keeps indentation and HTML remains literal text'); await close();
    await click('1'); await until("!!document.querySelector('.text-preview-source')");
    check(await run("document.querySelector('.text-preview-source').textContent==='中文文本\\n\\t缩进保留'"), 'UTF-8 text keeps tabs and line breaks'); await close();
    await click('7'); await until("document.querySelector('.text-preview-source')?.textContent.length===1048576");
    check(true, 'Exactly 1 MiB remains previewable'); await close();
    for (const [id, message] of [['d','编码'], ['e','1 MiB'], ['5','1 MiB'], ['f','重试'], ['2','为空']]) {
      await click(id); await until(`document.querySelector('.text-preview-status').textContent.includes('${message}')`);
      if (id === '2') {
        await run("BridgeI18n.setLanguage('en')");
        check(await run("document.querySelector('.text-preview-status').textContent==='This file is empty.' && document.querySelector('.text-preview-download').textContent==='Download'"), 'Preview controls and status follow language changes');
        await run("BridgeI18n.setLanguage('zh')");
      }
      check(true, 'Handles file ' + names[id]); await close();
    }
    await click('6'); await until("!document.querySelector('.text-preview-retry').hidden");
    await run("document.querySelector('.text-preview-retry').click()"); await until("document.querySelector('.text-preview-source')?.textContent==='Retry succeeded'");
    check(true, 'Retry reloads a failed file'); await close();
    await click('3'); await close(); await click('b');
    await until("!!document.querySelector('.text-preview-source')"); await run('new Promise(resolve=>setTimeout(resolve,400))');
    check(await run("document.querySelector('#text-preview-title').textContent==='settings.json' && !document.querySelector('.text-preview-content h1')"), 'Closed request never overwrites a newer preview');
    await run("document.querySelector('.text-preview-download').click()");
    await until("!document.querySelector('#text-preview-dialog').open");
    await until("document.querySelector('#download-panel')?.textContent.includes('下载完成')");
    check(requests.filter(r => r.id === 'b').length >= 3, 'Download action hands file to existing transfer flow');
    // Exercise the same DOM under the mobile user agent, where native owns downloads.
    await window.loadURL(base, {userAgent:'BridgeMobile/0.1-Android'});
    window.setSize(390, 844);
    await run("document.documentElement.classList.add('bridge-mobile')");
    await click('a'); await until("!!document.querySelector('#text-preview-dialog h1')");
    await run('new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))');
    check(await run("document.querySelector('.text-preview-toolbar button').getBoundingClientRect().right<=innerWidth-63.5"), 'Native menu has space beside the rightmost control');
    window.setSize(320, 640);
    await run("BridgeI18n.setLanguage('en');document.querySelector('#text-preview-title').textContent='A very long filename for a small phone screen.md'");
    await run('new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))');
    check(await run("(()=>{const toolbar=document.querySelector('.text-preview-toolbar'), buttons=[...toolbar.querySelectorAll('button,a')];return toolbar.scrollWidth<=toolbar.clientWidth+1 && buttons.every(b=>{const r=b.getBoundingClientRect();return r.height>=44 && r.left>=0 && r.right<=innerWidth-63.5;});})()"), 'English controls and a long filename fit a 320px phone');
    const java = read('mobile/android/src/io/github/try2love/codexbridge/MainActivity.java');
    const back = JSON.parse('"' + java.match(/web\.evaluateJavascript\("(\(\(\)=>\{const dialog=.*?)",result/)[1] + '"');
    check(await run(back), 'Existing Android back handler closes the text preview');
    await until("!document.querySelector('#text-preview-dialog').open");
    fs.writeFileSync(path.join(out, 'checks.json'), JSON.stringify({passed:checks.length, checks}, null, 2));
    server.close(); app.exit(0);
  }).catch(error => { fs.writeFileSync(path.join(out, 'checks.json'), JSON.stringify({error:String(error), stack:error.stack})); server.close(); app.exit(1); });
}
