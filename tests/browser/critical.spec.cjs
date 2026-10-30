'use strict';
const {test, expect} = require('@playwright/test');
const {readFileSync} = require('node:fs');
const path = require('node:path');

test.beforeEach(async ({context, request, baseURL}) => {
  // Even a test failure must never fall through to a real client or upstream API.
  await context.route('**/*', route => new URL(route.request().url()).origin === baseURL
    ? route.continue() : route.abort());
  expect(await (await request.get('/__fixture_health')).json()).toEqual({fixture: 'cmb-browser-regression'});
});

async function ready(page, scenario = 'success') {
  await page.goto('/?case=' + scenario);
  await page.waitForFunction(() => {
    const status = document.getElementById('bootstrap-status');
    return status && (status.hidden || status.dataset.state === 'failed');
  });
}

test('Markdown, literal code and math rendering', async ({page}, testInfo) => {
  await ready(page);
  await expect(page.locator('#bootstrap-status')).toBeHidden();
  await page.evaluate(async () => {
    await BridgeFeatures.load('chat');
    await BridgeFeatures.load('math');
    await document.fonts.ready;
  });
  for (const [file, count] of [['markdown', 15], ['math', 13]]) {
    const result = await page.evaluate(readFileSync(path.join(__dirname, file + '.browser.js'), 'utf8'));
    expect(result.passed).toBe(count);
    await testInfo.attach(file + '-checks', {body: JSON.stringify(result), contentType: 'application/json'});
  }
});

test('code syntax labels, safe highlighting, exact copy and shared lazy loading', async ({page}, testInfo) => {
  await ready(page);
  const requests=[];page.on('request',request=>{if(request.url().includes('/vendor/prism/'))requests.push(request.url());});
  const before=await page.evaluate(async()=>{
    await BridgeFeatures.load('chat');
    const host=document.createElement('div');host.id='syntax-test';host.style.width='300px';document.body.append(host);
    const plain=document.createElement('div');host.append(plain);renderMarkdown(plain,'```\nabc\n```\n\n```unknown\n<script>literal</script>\n```');
    return [...plain.querySelectorAll('.code-language')].map(node=>node.textContent);
  });
  expect(before).toEqual(['纯文本','纯文本']);expect(requests).toHaveLength(0);
  const result=await page.evaluate(async()=>{
    const host=document.getElementById('syntax-test'),node=document.createElement('div');host.append(node);
    const source='from copy import deepcopy\na = "abc"\nprint(deepcopy(a))\n';
    const start=performance.now();renderMarkdown(node,'```py title=test.py\n'+source+'```\n\n```html\n<img src=x onerror=alert(1)>\n```');
    await BridgeFeatures.load('syntax');await Promise.resolve();
    const firstMs=performance.now()-start,code=node.querySelector('pre code'),header=node.querySelector('.code-header');
    const clipboard=window.BridgeClipboard,copied=[];
    try {window.BridgeClipboard={copy:getSource=>copied.push(getSource())};node.querySelector('.code-copy').click();}
    finally {window.BridgeClipboard=clipboard;}
    const light=getComputedStyle(code.querySelector('.token.keyword')).color;
    document.documentElement.dataset.theme='dark';
    const dark=getComputedStyle(code.querySelector('.token.keyword')).color;
    document.documentElement.dataset.theme='light';
    await BridgeFeatures.load('files');
    const preview=BridgeSourceView.create({name:'same.py',text:source});host.append(preview);
    return {firstMs,source,text:code.textContent,copied,labels:[...node.querySelectorAll('.code-language')].map(n=>n.textContent),
      tokens:code.querySelectorAll('.token').length,hostileElements:node.querySelectorAll('img,script').length,
      ordered:header.firstElementChild.className==='code-language' && header.lastElementChild.classList.contains('code-copy'),
      fits:host.scrollWidth<=301,light,dark,previewText:preview.querySelector('code').textContent,previewTokens:preview.querySelectorAll('.token').length};
  });
  expect(result.labels).toEqual(['Python','HTML']);expect(result.tokens).toBeGreaterThan(0);
  expect(result.text).toBe(result.source);expect(result.copied).toEqual([result.source]);expect(result.hostileElements).toBe(0);
  expect(result.ordered).toBe(true);expect(result.fits).toBe(true);expect(result.light).not.toBe(result.dark);
  expect(result.previewText).toBe(result.source);expect(result.previewTokens).toBeGreaterThan(0);expect(requests).toHaveLength(1);
  await testInfo.attach('syntax-result',{body:JSON.stringify(result),contentType:'application/json'});
  await testInfo.attach('syntax-preview',{body:await page.locator('#syntax-test').screenshot(),contentType:'image/png'});
});

test('code syntax leaves large, incomplete and unavailable code readable without replaying loads',async({page})=>{
  await ready(page);await page.evaluate(()=>BridgeFeatures.load('chat'));
  const requests=[];page.on('request',request=>{if(request.url().includes('/vendor/prism/'))requests.push(request.url());});
  const large=await page.evaluate(()=>{
    const node=document.createElement('div');document.body.append(node);
    const text='print("x")\n'.repeat(6500);renderMarkdown(node,'```python\n'+text+'```');
    return {same:node.querySelector('code').textContent===text,tokens:node.querySelectorAll('.token').length,label:node.querySelector('.code-language').textContent};
  });
  expect(large).toEqual({same:true,tokens:0,label:'Python'});expect(requests).toHaveLength(0);
  await page.route('**/vendor/prism/**',route=>route.abort());
  const failed=await page.evaluate(async()=>{
    const node=document.createElement('div');document.body.append(node);
    renderMarkdown(node,'```js\nconst first = 1;');
    node.replaceChildren();
    renderMarkdown(node,'```js\nconst current = 2;');
    try {await BridgeFeatures.load('syntax');}catch(_){}
    return {text:node.querySelector('code').textContent,label:node.querySelector('.code-language').textContent};
  });
  expect(failed).toEqual({text:'const current = 2;',label:'JavaScript'});expect(requests).toHaveLength(1);
});

test('answering one pending card leaves the other independently answerable', async ({page}) => {
  await ready(page);
  const result = await page.evaluate(async () => {
    await BridgeFeatures.load('chat');
    const originalApi = api, originalId = currentId, originalHost = currentHost;
    const sent = [], cards = ['first', 'second'].map(id => ({id: 'async:questions:' + id,
      method: 'bridge/requestUserInputAsync', supported: true,
      params: {questions: [{id, question: id, options: []}]}}));
    try {
      currentId = 'synthetic-cards';currentHost = 'local';
      api = async (url, body) => {sent.push(body);return {status: 'accepted'};};
      renderApprovals(cards);
      const answer = async (card, value) => {
        card.querySelector('textarea').value = value;
        card.querySelector('form').dispatchEvent(new Event('submit', {cancelable: true}));
        await Promise.resolve();
      };
      await answer($('approvals').children[0], 'Yes');
      const beforeSnapshot = {count: $('approvals').children.length,
        secondEnabled: !$('approvals').children[1].querySelector('button').disabled};
      // The normalized live snapshot retains only the unanswered request.
      renderApprovals([cards[1]]);
      const remaining = $('approvals').firstElementChild;
      const afterSnapshot = {count: $('approvals').children.length,
        id: remaining.dataset.request, enabled: !remaining.querySelector('button').disabled};
      await answer(remaining, 'No');
      return {beforeSnapshot, afterSnapshot, sent};
    } finally {
      api = originalApi;currentId = originalId;currentHost = originalHost;
      cards.forEach(card => submittedRequestIds.delete(card.id));renderApprovals([]);
    }
  });
  expect(result.beforeSnapshot).toEqual({count: 2, secondEnabled: true});
  expect(result.afterSnapshot).toEqual({count: 1, id: 'async:questions:second', enabled: true});
  expect(result.sent).toEqual([
    {requestId: 'async:questions:first', response: {answers: {first: ['Yes']}}},
    {requestId: 'async:questions:second', response: {answers: {second: ['No']}}},
  ]);
});

for (const scenario of ['success', 'css-once', 'js-twice', 'js-fatal', 'runtime',
  'opaque-error', 'external-runtime', 'external-rejection', 'app-cached-failure']) {
  test('startup and login: ' + scenario, async ({page}, testInfo) => {
    await ready(page, scenario);
    const evidence = await page.evaluate(async () => ({
      state: await (await fetch('/__state')).json(), probe: window.__startupTest,
      status: document.getElementById('bootstrap-status').dataset.state,
      diagnostic: document.getElementById('bootstrap-details').textContent,
    }));
    await testInfo.attach('startup-evidence', {body: JSON.stringify(evidence), contentType: 'application/json'});
    expect(evidence.state.writes).toEqual([]);
    await page.addScriptTag({url: '/__checks.js'});
    const result = await page.evaluate(() => runBootstrapBrowserChecks());
    expect(result.passed).toBe(true);
    expect(result.writes).toEqual([]);
    if (scenario === 'success') await expect(page.locator('#login')).toBeVisible();
  });
}

for (const [name, file, invoke] of [
  ['paperclip host reference and native attachment actions', 'host-file-reference', 'runHostFileReferenceChecks'],
  ['agent manual model and reasoning settings', 'agent-model-settings', 'runAgentModelSettingsChecks'],
  ['chat history, list entry and scroll position', 'desktop-history', 'runDesktopHistoryBrowserChecks'],
  ['shared chat reading state across refreshes', 'chat-reading-state', 'runChatReadingStateChecks'],
  ['account-scoped drafts and late send acknowledgement', 'persistent-drafts', 'runPersistentDraftTests'],
  ['API form labels, layout and upstream model selection', 'api-form', 'runApiFormBrowserChecks'],
  ['shared session menu on desktop and mobile', 'session-menu', 'runSessionMenuBrowserChecks'],
  ['composer quotes retain draft and layout', 'composer-quotes', 'runComposerQuoteLayoutChecks'],
]) {
  test(name, async ({page}, testInfo) => {
    await ready(page, file==='agent-model-settings'?'session-menu':'success');
    await expect(page.locator('#bootstrap-status')).toBeHidden();
    await page.addScriptTag({url: `/__browser/${file}.browser.js`});
    const result = await page.evaluate(name => globalThis[name](), invoke);
    expect(Array.isArray(result) ? result.length : result.passed).toBeGreaterThan(0);
    await testInfo.attach('checks', {body: JSON.stringify(result), contentType: 'application/json'});
  });
}

for (const provider of ['claude', 'deepseek']) {
  test(provider + ' project dialog and directory isolation', async ({page}) => {
    await ready(page);
    await page.addScriptTag({url: '/__browser/project-creation.browser.js'});
    const result = await page.evaluate(provider => runProjectCreationBrowserChecks({provider, language: 'zh'}), provider);
    expect(result.passed).toBeGreaterThan(0);
  });
}

test('Android app theme synchronizes list and chat without replacing drafts or other appearance settings',async({page})=>{
 await ready(page);
 const result=await page.evaluate(()=>{
  const input=document.getElementById('message'),root=document.documentElement,theme=document.getElementById('appearance-theme');
  input.value='keep this draft';const original=input,changes=[];
  document.addEventListener('bridge-theme-change',event=>changes.push(event.detail.theme));
  const accent=document.getElementById('appearance-accent');accent.value='#6655cc';accent.oninput();
  const native=(choice,dark)=>{window.BridgeNativeTheme={theme:choice,dark};document.dispatchEvent(new Event('bridge-native-theme'));return root.dataset.theme;};
  const states=[native('system',true),native('system',false),native('dark',false),native('light',true)];
  theme.value='dark';theme.onchange();states.push(root.dataset.theme);
  const saved=JSON.parse(localStorage.getItem('bridge-appearance'));
  return {states,changes,draft:input.value,sameInput:original===document.getElementById('message'),accent:saved.accent,choice:saved.theme};
 });
 expect(result).toEqual({states:['dark','light','dark','light','dark'],changes:['dark'],draft:'keep this draft',sameInput:true,accent:'#6655cc',choice:'dark'});
});

for(const provider of ['Codex','Claude','DSH']){
 test(provider+' list menu entry, themes and equal archive confirmation',async({page},testInfo)=>{
  await ready(page,'session-menu');
  await expect(page.locator('#login')).toBeHidden();
  await page.locator('#client-switch').getByRole('button',{name:provider,exact:true}).click();
  const row=page.locator('.session:visible').filter({hasText:provider+' · 页面交互验证'}).first();
  await expect(row).toBeVisible();
  const listIdentity=await row.getAttribute('data-id');
  const openMenu=async()=>{
   if(page.viewportSize().width<=640){
    await row.dispatchEvent('pointerdown',{pointerType:'touch',button:0,clientX:80,clientY:150});
    await expect(page.locator('dialog.session-menu')).toBeVisible();
    await row.dispatchEvent('pointerup',{pointerType:'touch',button:0});
    await row.dispatchEvent('click');
   }else await row.click({button:'right'});
  };
  for(const theme of ['light','dark']){
   await page.evaluate(theme=>{document.documentElement.dataset.theme=theme;},theme);
   await openMenu();
   const box=page.locator('dialog.session-menu');
   await expect(box).toBeVisible();
   await expect(page.locator('#app')).not.toHaveClass(/chat-open/);
   await expect(row).toHaveAttribute('data-id',listIdentity);
   await expect(box.locator('.sm-name strong')).toHaveText(provider+' · 页面交互验证');
   await expect(box.getByRole('button',{name:'聊天通知设置',exact:true})).toBeVisible();
   await expect(box.getByRole('button',{name:'项目',exact:true})).toHaveCount(1);
   await expect(box.getByRole('button',{name:'分区',exact:true})).toHaveCount(1);
   await box.evaluate(node=>Promise.all(node.getAnimations().map(a=>a.finished)));
   await page.screenshot({path:testInfo.outputPath(provider+'-'+theme+'-list.png')});
   await box.getByRole('button',{name:'会话详情',exact:true}).click();
   await page.keyboard.press('Escape');
   await expect(box.locator('.sm-name strong')).toHaveText(provider+' · 页面交互验证');
   await expect(page.locator('#app')).not.toHaveClass(/chat-open/);
   await page.keyboard.press('Escape');await expect(box).toHaveCount(0);
   await openMenu();
   await box.getByRole('button',{name:'修改聊天名称',exact:true}).click();
   await expect(box.getByRole('textbox',{name:'聊天名称',exact:true})).toHaveValue(provider+' · 页面交互验证');
   await box.getByRole('textbox',{name:'聊天名称',exact:true}).fill('返回不保存这个名称');
   await box.dispatchEvent('cancel',{cancelable:true});
   await expect(box.locator('.sm-name strong')).toHaveText(provider+' · 页面交互验证');
   for(const label of ['项目','分区']){
    await box.getByRole('button',{name:label,exact:true}).click();
    await expect(box.getByRole('button',{name:'示例'+label,exact:true})).toBeVisible();
    const local=provider==='Claude'||provider==='DSH'&&label==='分区';
    await expect(box.getByText('仅在 Bridge 中生效',{exact:true})).toHaveCount(local?1:0);
    await expect(box.getByRole('textbox',{name:label==='项目'?'新项目名称':'新分区名称',exact:true})).toHaveCount(local?1:0);
    await box.getByRole('button',{name:'返回会话菜单',exact:true}).click();
    await expect(box.locator('.sm-name strong')).toHaveText(provider+' · 页面交互验证');
   }
   await box.getByRole('button',{name:'停止任务并归档…',exact:true}).click();
   const confirm=box.getByRole('button',{name:'确认归档',exact:true}),cancel=box.getByRole('button',{name:'取消',exact:true});
   await expect(cancel).toBeFocused();
   const left=await confirm.boundingBox(),right=await cancel.boundingBox();
   expect(left.x).toBeLessThan(right.x);expect(Math.abs(left.width-right.width)).toBeLessThan(1);expect(left.height).toBe(right.height);
   const styles=await box.locator('.sm-confirm-actions button').evaluateAll(buttons=>buttons.map(b=>{const s=getComputedStyle(b);return [s.backgroundColor,s.color,s.borderRadius,s.fontSize];}));
   expect(styles[0]).toEqual(styles[1]);
   await page.screenshot({path:testInfo.outputPath(provider+'-'+theme+'-list-confirm.png')});
   await cancel.click();
   await expect(box.locator('.sm-name strong')).toHaveText(provider+' · 页面交互验证');
   await box.getByRole('button',{name:'停止任务并归档…',exact:true}).click();
   await box.dispatchEvent('cancel',{cancelable:true});
   await expect(box.locator('.sm-name strong')).toHaveText(provider+' · 页面交互验证');
   if(page.viewportSize().width<=640){
    const handle=box.locator('.sm-handle');
    await handle.dispatchEvent('pointerdown',{pointerType:'touch',pointerId:8,button:0,clientX:100,clientY:100});
    await handle.dispatchEvent('pointermove',{pointerType:'touch',pointerId:8,clientX:100,clientY:125});
    await handle.dispatchEvent('pointerup',{pointerType:'touch',pointerId:8,clientX:100,clientY:125});
    await expect(box).not.toHaveClass(/sm-returning/);
    await expect(box).toBeVisible();
    await handle.dispatchEvent('pointerdown',{pointerType:'touch',pointerId:8,button:0,clientX:100,clientY:100});
    await handle.dispatchEvent('pointermove',{pointerType:'touch',pointerId:8,clientX:100,clientY:260});
    await handle.dispatchEvent('pointerup',{pointerType:'touch',pointerId:8,clientX:100,clientY:260});
   }else await page.keyboard.press('Escape');
   await expect(box).toHaveCount(0);
  }
  const evidence=await page.evaluate(async()=>await(await fetch('/__state')).json());
  expect(evidence.writes.filter(path=>!path.endsWith('/activity'))).toEqual([]);
  expect(Object.keys(evidence.requests).filter(path=>/\/(detail|activate|resume)$/.test(path))).toEqual([]);
  expect(await page.evaluate(()=>window.__startupTest.errors)).toEqual([]);
 });
}

test('SSH retry lives in accounts and preserves other controls',async({page},testInfo)=>{
  await ready(page);
  await page.evaluate(()=>{
    const value={accounts:[],reminders:{},current:{status:'ready',name:'Local account'},switch:{phase:'idle'},
      sshHosts:[{host:'remote:fixture',label:'Offline SSH',state:'offline',nextRetryAt:Date.now()/1000+60}]};
    window.__sshRetryCalls=[];
    accountsPanel.read=async()=>value;
    accountsPanel.request=async request=>{
      window.__sshRetryCalls.push(request);
      value.sshHosts[0].state='checking';return value;
    };
    accountsPanel.accept(value);document.getElementById('accounts-dialog').showModal();
  });
  const dialog=page.locator('#accounts-dialog');
  const retry=dialog.getByRole('button',{name:'重试 SSH 连接',exact:true});
  await expect(retry).toBeEnabled();
  await expect(dialog.getByText('Offline SSH',{exact:true})).toBeVisible();
  await expect(dialog.getByText(/下次自动重试/)).toBeVisible();
  await retry.scrollIntoViewIfNeeded();
  await page.screenshot({path:testInfo.outputPath('ssh-accounts-retry.png')});
  await retry.click();await expect(retry).toBeDisabled();
  expect(await page.evaluate(()=>window.__sshRetryCalls)).toEqual([{action:'retrySsh',host:'remote:fixture'}]);
  await expect(dialog.getByRole('button',{name:'刷新',exact:true})).toBeEnabled();
});

test('reset outlook is opt-in and fits both themes',async({page},testInfo)=>{
  await ready(page);
  await page.evaluate(()=>{
    const now=Date.now()/1000;
    const value={accounts:[],resetDynamicEnabled:true,reminders:{},globalReset:{state:'ready',updatedAt:now,checkedAt:now,lastResetAt:now-172800,probabilities:{h24:24,h48:48},confidence:'low'}};
    accountsPanel.read=async()=>value;
    accountsPanel.request=async request=>{if(request.action==='reminders')value.reminders={...value.reminders,...request.preferences};return value;};
    accountsPanel.accept(value);
    document.getElementById('accounts-dialog').showModal();
  });
  await expect(page.locator('label:has(#appearance-reset-dynamic)')).toHaveText('Codex官方重置动态');
  const card=page.locator('.accounts-global-reset');
  for(const theme of ['light','dark']){
    await page.evaluate(theme=>document.documentElement.dataset.theme=theme,theme);
    await expect(card).toBeVisible();
    await expect(card).toContainText('第三方统计与预测');
    await expect(card).toContainText('24%');
    await expect(card).toContainText('48%');
    await expect(card.getByRole('button',{name:'开启重置动态提醒'})).toHaveAttribute('aria-pressed','false');
    await expect(card.locator('.accounts-global-reminder')).toHaveText('提醒已关闭');
    await card.getByRole('button',{name:'开启重置动态提醒'}).click();
    await expect(card.locator('.accounts-global-reminder')).toHaveText('提醒已开启');
    await expect(card.getByRole('button',{name:'关闭重置动态提醒'})).toHaveAttribute('aria-pressed','true');
    await card.getByRole('button',{name:'关闭重置动态提醒'}).click();
    await expect(card.locator('.accounts-global-reminder')).toHaveText('提醒已关闭');
    const bounds=await card.boundingBox();
    expect(bounds.x).toBeGreaterThanOrEqual(0);
    expect(bounds.x+bounds.width).toBeLessThanOrEqual(page.viewportSize().width);
    await page.screenshot({path:testInfo.outputPath('reset-outlook-'+theme+'.png')});
  }
  const alignment=await page.evaluate(()=>{
    accountsPanel.tools.open=true;
    return [...accountsPanel.reminders.querySelectorAll('.account-reminder')].map(row=>{
      const measure=()=>{const a=row.querySelector('input').getBoundingClientRect(),b=row.querySelector('span').getBoundingClientRect();return Math.abs(a.y+a.height/2-b.y-b.height/2);};
      const normal=measure();row.style.width='160px';const wrapped=measure();row.style.width='';return {normal,wrapped};
    });
  });
  expect(alignment.length).toBeGreaterThan(0);
  for(const row of alignment){expect(row.normal).toBeLessThanOrEqual(1);expect(row.wrapped).toBeLessThanOrEqual(1);}
  await page.evaluate(()=>accountsPanel.accept({...accountsPanel.value,globalReset:{...accountsPanel.value.globalReset,state:'error',stale:true}}));
  await expect(card).toContainText('数据延迟');
  await expect(card.locator('.accounts-global-odds')).toHaveCount(0);
  await page.evaluate(()=>accountsPanel.accept({...accountsPanel.value,resetDynamicEnabled:false,globalReset:null}));
  await expect(card).toBeHidden();
});

for(const surface of ['code','cowork','deepseek']){
 test(surface+' model settings on actual chat surface',async({page},testInfo)=>{
  await ready(page,'session-menu');await expect(page.locator('#login')).toBeHidden();
  await page.addScriptTag({url:'/__browser/agent-model-settings.browser.js'});
  await page.evaluate(surface=>runAgentModelSettingsChecks({provider:surface==='deepseek'?'deepseek':'claude',surface,leaveOpen:true}),surface);
  await expect(page.locator('.ds-model-dialog')).toBeVisible();await expect(page.locator('#app')).toHaveClass(/chat-open/);
  for(const theme of ['light','dark']){await page.evaluate(theme=>document.documentElement.dataset.theme=theme,theme);await page.screenshot({path:testInfo.outputPath(surface+'-'+theme+'-model-settings.png')});}
 });
}

test('Codex project trust cancellation prevents creation and confirmation stays scoped',async({page})=>{
 await ready(page);
 const result=await page.evaluate(async()=>{
  const oldApi=api,calls=[],dialog=$('new-chat-dialog'),form=$('new-chat-form');
  let confirmed=false;
  api=async(url,body)=>{calls.push({url,body});if(url==='/api/projects/trust'){
   if(body.confirmed){confirmed=true;return {trusted:true};}
   return {required:true,path:'/synthetic/project',environment:'SSH fixture · Codex App',confirmation:'bound-to-selection'};
  }if(url.startsWith('/api/sessions'))throw Error('synthetic creation boundary');throw Error('unexpected request '+url);};
  try{
   $('new-chat-type').replaceChildren(new Option('Codex App','desktop'));$('new-chat-project').replaceChildren(new Option('Project','synthetic-key'));$('new-chat-title').value='Synthetic';dialog.showModal();
   const tick=()=>new Promise(r=>setTimeout(r,0));
   let pending=form.onsubmit({preventDefault(){}});await tick();
   let trust=[...document.querySelectorAll('dialog[open]')].find(d=>d!==dialog);
   if(!trust?.textContent.includes('/synthetic/project')||!trust.textContent.includes('SSH fixture'))throw Error('Missing exact consent target');
   [...trust.querySelectorAll('button')].find(b=>b.textContent==='取消').click();await pending;
   const cancelled=calls.length===1&&!confirmed&&$('new-chat-title').value==='Synthetic';
   calls.length=0;pending=form.onsubmit({preventDefault(){}});await tick();trust=[...document.querySelectorAll('dialog[open]')].find(d=>d!==dialog);
   [...trust.querySelectorAll('button')].find(b=>b.textContent==='确认信任').click();await pending;
   return {cancelled,calls,confirmed};
  }finally{api=oldApi;dialog.close();sessionStorage.removeItem('new-chat-attempt');}
 });
 expect(result.cancelled).toBe(true);expect(result.confirmed).toBe(true);
 expect(result.calls.map(c=>c.url)).toEqual(['/api/projects/trust','/api/projects/trust','/api/sessions']);
 expect(result.calls[1].body).toEqual({key:'synthetic-key',confirmed:true,confirmation:'bound-to-selection'});
});

test('lost send response reconciles once and preserves newer composer edits', async ({page}) => {
  await ready(page);
  const results = await page.evaluate(async () => {
    await BridgeFeatures.load('chat');
    const originalFetch = window.fetch, results = [];
    try {
      draftGateway = 'receipt-fixture-' + crypto.randomUUID();currentHost = 'local';
      for (const edit of [false, true]) {
        currentId = crypto.randomUUID();codexDraftScope = null;codexDraftTouched = false;$('message').value = '';
        bindCodexDraft({draftAccountIdentity:'receipt-account'});
        state = {status:'idle',access:{source:'desktop',canSend:true}};
        let writes = 0, reads = 0, submitted;
        window.fetch = async (url, options) => {
          if (String(url).includes('/api/sessions/' + currentId + '/send?')) {
            writes++;submitted = JSON.parse(options.body);
            if (edit) {$('message').value = 'newer draft';$('message').dispatchEvent(new Event('input',{bubbles:true}));}
            throw new TypeError('Failed to fetch');
          }
          if (String(url).includes('/api/sessions/' + currentId + '/message-management?')) {
            reads++;return {ok:true,status:200,json:async()=>({draftAccountIdentity:'receipt-account',receipts:[{id:submitted.id,status:'accepted'}],queue:[],unknown:[]})};
          }
          return originalFetch.call(window,url,options);
        };
        $('message').value = 'submitted once';$('message').dispatchEvent(new Event('input',{bubbles:true}));
        await $('composer').onsubmit({preventDefault(){}});
        results.push({edit,writes,reads,text:$('message').value,draft:composerDrafts.load(codexDraftScope)?.text||'',pending:composerDrafts.pendingSend(codexDraftScope),error:$('send-error').textContent});
      }
      return results;
    } finally {window.fetch = originalFetch;}
  });
  for (const row of results) {
    expect(row.writes).toBe(1);expect(row.reads).toBe(1);expect(row.pending).toBeNull();
    expect(row.text).toBe(row.edit?'newer draft':'');expect(row.draft).toBe(row.edit?'newer draft':'');
    expect(row.error).toBe('');
  }
});
