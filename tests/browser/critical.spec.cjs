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

test('image preview has one header row and a bottom save icon', async ({page}, testInfo) => {
  for (const width of [320,390,844,1280]) {
    await page.setViewportSize({width,height:844});
    await ready(page);
    await page.evaluate(()=>BridgeFeatures.load('chat'));
    await page.evaluate(readFileSync(path.join(__dirname,'image-save.browser.js'),'utf8'));
    for (const language of ['zh','en']) {
      await page.evaluate(language=>BridgeI18n.setLanguage(language),language);
      expect((await page.evaluate(()=>runImageSaveChecks())).passed).toBe(47);
    }
  }
  await page.setViewportSize({width:390,height:844});
  await page.evaluate(()=>{
    BridgeI18n.setLanguage('zh');
    BridgeImageViewer.configureComposer({scope:()=> 'fixture',canReuse:()=>true,add:()=>true});
    const button=document.createElement('button');document.body.append(button);
    button.dataset.imagePreview='/api/sessions/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee/uploads/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee/preview';
    button.dataset.imageName='Screenshot_2026-11-01_很长的原始图片文件名.png';button.click();
  });
  await testInfo.attach('image-toolbar',{body:await page.screenshot(),contentType:'image/png'});
});

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
  ['CLI release single and all across local WSL SSH', 'cli-release', 'runCliReleaseChecks'],
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

test('independent Codex list and project picker stay in the selected configuration',async({page})=>{
 await ready(page);await page.evaluate(()=>BridgeFeatures.load('chat'));
 const result=await page.evaluate(async()=>{
  const originalApi=api,entry='codex:'+'a'.repeat(32),calls=[];
  try{
   api=async url=>{calls.push(url);if(url.startsWith('/api/projects'))return {projects:[{key:'cli|'+entry+'|fixture',name:'IDE project',cwd:'/fixture/project',host:entry,type:'cli'}],environments:[{key:'cli|'+entry,type:'cli',host:entry,name:'IDE fixture'}]};return {sessions:[],availableSources:['ide'],remoteHosts:['default-must-not-be-read'],complete:true};};
   currentHost=entry;selectedCodexEntry=entry;listRows=[];listHostIds=[];listFilterKey='';listSource='all';
   await loadList(true);const reads=[...calls];calls.length=0;
   await $('new-chat').onclick();
   return {reads,projects:[...calls],entry:newChatEntry,mode:$('new-chat-type').value,locked:$('new-chat-type').disabled,project:$('new-chat-project').value,host:newChatEnvironment().host};
  }finally{$('new-chat-dialog').close();api=originalApi;selectedCodexEntry=null;currentHost='local';}
 });
 expect(result.reads).toHaveLength(1);expect(new URL(result.reads[0],'http://fixture').searchParams.get('host')).toBe('codex:'+'a'.repeat(32));
 expect(result.projects).toEqual(['/api/projects?entry='+encodeURIComponent(result.entry)]);
 expect(result.mode).toBe('cli');expect(result.locked).toBe(true);expect(result.host).toBe(result.entry);expect(result.project).toContain(result.entry);
});

test('restored independent Codex selection creates in its own configuration',async({page})=>{
 const entry='codex:'+'a'.repeat(32),reads=[];
 await page.addInitScript(value=>localStorage.setItem('bridge-client',value),entry);
 await page.route('**/api/clients',route=>route.fulfill({json:{clients:[{id:'codex',name:'Codex',enabled:true},{id:entry,name:'工作 Codex',kind:'codex-local',enabled:true,capabilities:{create:true,accounts:true}}]}}));
 await page.route('**/api/projects*',route=>{reads.push(new URL(route.request().url()).searchParams.get('entry'));return route.fulfill({json:{projects:[{key:'cli|'+entry+'|fixture',name:'独立项目',cwd:'/fixture',host:entry,type:'cli'}],environments:[{key:'cli|'+entry,type:'cli',host:entry,name:'工作 Codex'}]}});});
 await ready(page,'session-menu');await expect(page.locator('.client-picker')).toContainText('工作 Codex');
 await page.locator('#new-chat').click();await expect(page.locator('#new-chat-submit')).toBeEnabled();
 expect(reads).toEqual([entry]);await expect(page.locator('#new-chat-type')).toHaveValue('cli');await expect(page.locator('#new-chat-type')).toBeDisabled();
 expect(await page.evaluate(()=>newChatEnvironment().host)).toBe(entry);
});

test('independent Codex clients have separate navigation and account scopes',async({page},info)=>{
 await ready(page);await page.evaluate(()=>BridgeFeatures.load('chat'));
 const result=await page.evaluate(async()=>{
  const entry='codex:'+'a'.repeat(32),original=api,calls=[];
  try{
   api=async(url,body)=>{calls.push({url,write:!!body});if(url.startsWith('/api/accounts'))return {scope:'profile',scopeLabel:'工作 Codex',current:{kind:'api',name:'独立 API'},accounts:[],switch:{phase:'idle'},management:{external:false},reminders:{},canSwitch:true,canManageDesktop:false};return {sessions:[],complete:true};};
   clientNavigation.applyClients({clients:[{id:'codex',name:'Codex',enabled:true,entries:[{id:'desktop',name:'Codex',enabled:true}]},{id:entry,name:'工作 Codex',kind:'codex-local',enabled:true,capabilities:{create:true,accounts:true}}]});
   await clientNavigation.choose(entry);await openCurrentAccounts();await accountsPanel.refresh();
   return {selected:clientNavigation.activeClient(),buttons:[...document.querySelectorAll('.client-switch button')].map(b=>({text:b.textContent,selected:b.classList.contains('selected')})),entry:codexAccountEntry,selectorHidden:!accountEntryControls||accountEntryControls.hidden,calls,refreshHidden:accountsPanel.refreshButton.hidden,updateHidden:accountsPanel.updates.hidden};
  }finally{api=original;}
 });
 expect(result.buttons).toEqual([{text:'Codex',selected:false},{text:'工作 Codex',selected:true}]);
 expect(result.selected).toBe(result.entry);expect(result.selectorHidden).toBe(true);expect(result.refreshHidden).toBe(false);expect(result.updateHidden).toBe(true);
 expect(result.calls.filter(x=>x.url.startsWith('/api/accounts')).every(x=>x.url.includes(encodeURIComponent(result.entry)))).toBe(true);
 await info.attach('independent-codex-accounts',{body:await page.screenshot(),contentType:'image/png'});
});

test('shared client shell preserves navigation scroll and switches exact instances from its heading',async({page},info)=>{
 const entry='codex:'+'a'.repeat(32),other='codex:'+'b'.repeat(32),reads=[];
 const clients=[{id:'codex',name:'Codex'}, {id:'claude',name:'Claude'}, {id:'deepseek',name:'DSH'}, {id:'zcode',name:'ZCode'}, {id:entry,name:'工作 Codex'}, {id:other,name:'个人 Codex'}].map(row=>({...row,enabled:true,configured:true,connected:true,capabilities:{accounts:true,create:true}}));
 await page.route('**/api/clients',route=>route.fulfill({json:{clients}}));
 await page.route('**/api/sessions?*',route=>{reads.push(new URL(route.request().url()).searchParams.get('host'));return route.fulfill({json:{sessions:[],remoteHosts:[],availableSources:[],complete:true}});});
 await ready(page,'session-menu');
 await expect(page.locator('.client-switch button')).toHaveCount(6);
 await expect(page.locator('.client-picker')).not.toContainText('聊天');
 const aligned=await page.locator('.client-picker').evaluate(node=>{const icon=node.querySelector('.client-icon').getBoundingClientRect(),name=node.querySelector('.client-badge-name').getBoundingClientRect();return Math.abs(icon.top+icon.height/2-name.top-name.height/2)<1;});expect(aligned).toBe(true);
 await info.attach('client-header',{body:await page.locator('.list-heading').screenshot(),contentType:'image/png'});
 // Font metrics differ by platform; test the middle, not an almost-reached edge.
 const scrollPosition=await page.locator('.client-switch').evaluate(node=>Math.floor((node.scrollWidth-node.clientWidth)/2));
 expect(scrollPosition).toBeGreaterThan(0);
 await page.locator('.client-switch').hover();await page.mouse.wheel(scrollPosition,0);
 await page.evaluate(()=>{window.__firstSwitch=document.querySelector('.client-switch button');});
 await expect.poll(()=>page.locator('.client-switch').evaluate(node=>Math.round(node.scrollLeft))).toBe(scrollPosition);
 await page.locator('.client-picker').click();await expect(page.locator('.client-picker-options button')).toHaveCount(6);
 await page.locator('.client-picker-options [data-client-id="'+entry+'"]').click();
 await expect.poll(()=>page.evaluate(()=>clientNavigation.activeClient())).toBe(entry);
 await expect.poll(()=>page.evaluate(()=>selectedCodexEntry)).toBe(entry);
 await expect.poll(()=>reads.at(-1)).toBe(entry);
 await page.evaluate(()=>clientNavigation.applyClients({clients:clientNavigation.clients.map(row=>({...row}))}));
 await expect.poll(()=>page.locator('.client-switch').evaluate(node=>Math.round(node.scrollLeft))).toBe(scrollPosition);
 expect(await page.evaluate(()=>window.__firstSwitch===document.querySelector('.client-switch button'))).toBe(true);
 await expect(page.locator('.client-switch-arrow.previous')).toBeVisible();await expect(page.locator('.client-switch-arrow.next')).toBeVisible();
 // Moving the same shell between provider lists must retain the user's position.
 await page.locator('.client-picker').click();await page.locator('.client-picker-options [data-client-id="claude"]').click();
 await expect.poll(()=>page.evaluate(()=>clientNavigation.activeClient())).toBe('claude');
 await expect.poll(()=>page.locator('.client-switch').evaluate(node=>Math.round(node.scrollLeft))).toBe(scrollPosition);
 await page.locator('.client-picker').click();await page.locator('.client-picker-options [data-client-id="'+other+'"]').click();
 await expect.poll(()=>page.evaluate(()=>selectedCodexEntry)).toBe(other);
 await expect.poll(()=>page.locator('.client-switch').evaluate(node=>Math.round(node.scrollLeft))).toBe(scrollPosition);
 await page.evaluate(()=>{clientNavigation.preference('switch:zcode',false);clientNavigation.paint();});
 await page.locator('.client-picker').click();await expect(page.locator('.client-picker-options [data-client-id="zcode"]')).toBeVisible();
 expect(await page.locator('.client-picker-options button').evaluateAll(nodes=>nodes.map(n=>n.dataset.clientId))).toEqual(clients.map(row=>row.id));
 await info.attach('client-picker',{body:await page.screenshot(),contentType:'image/png'});
 await page.locator('.client-picker-options [data-client-id="codex"]').click();await expect.poll(()=>page.evaluate(()=>selectedCodexEntry)).toBeNull();
 // A hidden chat/list transition should not overwrite the cached position with zero.
 const saved=await page.locator('.client-switch').evaluate(node=>node.scrollLeft);
 await page.evaluate(()=>{document.getElementById('sidebar').style.display='none';clientNavigation.paint();});
 await page.evaluate(()=>new Promise(requestAnimationFrame));
 await page.evaluate(()=>{document.getElementById('sidebar').style.display='';clientNavigation.paint();});
 await expect.poll(()=>page.locator('.client-switch').evaluate(node=>node.scrollLeft)).toBe(saved);
 expect(await page.evaluate(()=>__startupTest.errors)).toEqual([]);
});

test('every desktop client uses the shared next-level management shell',async({page},info)=>{
 const root=path.resolve(__dirname,'../..');
 const clients=[{id:'codex',name:'Codex'},{id:'claude',name:'Claude'},{id:'deepseek',name:'DSH'},{id:'zcode',name:'ZCode',configurationNotice:'原 App 账号',capabilities:{accounts:true}}, {id:'codex:'+'a'.repeat(32),name:'工作 Codex',kind:'codex-local'}, {id:'wsl:'+'b'.repeat(32),name:'Ubuntu Codex',kind:'wsl-cli'}].map(c=>({...c,enabled:true,configured:true,installed:true,connected:true}));
 const bootstrap=`window.testCalls=[];window.bridgeDesktop={};window.loadWslAccounts=async()=>{};
 window.testPanel=new DesktopConnectionsPanel({root:document.getElementById('desktop-connections'),feedback:message=>{throw Error(message)},api:{desktopSessions:async body=>{testCalls.push(body);return {accounts:[],activeIds:[],capabilities:{quota:false}}},cliAccess:async body=>{testCalls.push(body);return {provider:body.provider,entry:body.entry,available:true,accounts:[],capabilities:{}}}}});
 testPanel.clients=${JSON.stringify(clients)};testPanel.renderClients();document.querySelectorAll('[data-panel]').forEach(node=>node.hidden=!['accounts','account','desktop-sessions','harness'].includes(node.dataset.panel));GatewayLayout.tab('accounts');`;
 await page.route('**/desktop/**',async route=>{const filename=new URL(route.request().url()).pathname;
  if(filename==='/desktop/shell/renderer.js')return route.fulfill({contentType:'text/javascript',body:bootstrap});
  const file=path.resolve(root,'.'+filename);if(!file.startsWith(root+path.sep))return route.abort();
  try{let body=readFileSync(file);if(filename==='/desktop/index.html')body=Buffer.from(body.toString().replace('<script src="shell/layout.js">','<script src="../web/features/accounts/wsl-accounts.js"></script><script src="shell/layout.js">'));
  return route.fulfill({body,contentType:filename.endsWith('.html')?'text/html':filename.endsWith('.js')?'text/javascript':filename.endsWith('.css')?'text/css':'image/png'});}catch{return route.abort();}});
 await page.route('**/web/**',route=>{const filename=new URL(route.request().url()).pathname;try{return route.fulfill({body:readFileSync(path.resolve(root,'.'+filename)),contentType:filename.endsWith('.js')?'text/javascript':filename.endsWith('.css')?'text/css':'image/png'});}catch{return route.abort();}});
 const errors=[];page.on('pageerror',error=>errors.push(error.message));
 await page.setViewportSize({width:1200,height:950});await page.goto('/desktop/index.html');
 for(const client of clients){
  await page.locator('[data-client-id="'+client.id+'"] button').filter({hasText:/^管理$/}).click();
  await expect(page.locator('#clients-page')).toBeHidden();await expect(page.locator('.client-config-back')).toBeInViewport();
  const section=client.kind==='codex-local'?'codex':client.id;
  await expect(page.locator('[data-client-config="'+section+'"]:visible').first()).toBeVisible();
  expect((await page.locator('.client-config-back').boundingBox()).y).toBeLessThan((await page.locator('[data-client-config="'+section+'"]:visible').first().boundingBox()).y);await expect(page.locator('dialog[open]')).toHaveCount(0);
  if(client.id==='zcode')await info.attach('zcode-management-page',{body:await page.screenshot(),contentType:'image/png'});
  await page.locator('.client-config-back').click();await expect(page.locator('#clients-page')).toBeVisible();
 }
 expect(await page.evaluate(()=>testCalls.some(call=>call.provider==='zcode'&&call.action==='accounts'))).toBe(true);
 expect(await page.evaluate(()=>testCalls.some(call=>call.entry==='wsl:'+'b'.repeat(32)&&call.action==='wslAccounts'))).toBe(true);
 expect(errors).toEqual([]);
});

test('shared native API editors keep production form layout and synthetic model discovery',async({page},testInfo)=>{
  await ready(page);
  for(const provider of ['codex','claude','deepseek','zcode']){
    const result=await page.evaluate(async provider=>{
      const node=(tag,text)=>{const n=document.createElement(tag);if(text)n.textContent=text;return n;};
      const dialog=node('dialog');dialog.className='picker';dialog.id='native-api-layout';
      const heading=node('div');heading.className='picker-head';const title=node('h2',provider+' API');title.className='dialog-content-start';title.tabIndex=-1;heading.append(title);
      const root=node('div');dialog.append(heading,root);document.body.append(dialog);dialog.showModal();title.focus();
      const state=provider==='codex'?{codex:{accounts:[]}}:{accounts:[],activeIds:[],canSwitch:true,capabilities:{quota:false,addApi:true,...provider==='zcode'?{apiFormats:[{value:'openai-responses',label:'OpenAI Responses'}]}:{}}};
      const calls=[];const request=async value=>{calls.push({...value,apiKey:'[synthetic]'});return ['modelsDraft','models-draft'].includes(value.action)?{models:['fixture-model','fixture-alternate']}:state;};
      const panel=provider==='codex'?new CliAccessPanel({root,provider,formOnly:true,read:async()=>state,request}):new DesktopClientAccountsPanel({root,provider,desktop:true,read:async()=>state,request,visible:()=>false});
      if(provider==='codex')await panel.refresh();else{panel.accept(state);panel.addApiButton.click();}
      const editor=provider==='codex'?panel:panel.apiEditor;
      editor.name.input.value='独立测试 API';editor.baseUrl.input.value='https://synthetic.invalid/v1';editor.key.input.value='synthetic-key';
      editor.fetchModels.click();const end=performance.now()+1500;while(editor.draftLoading){if(performance.now()>end)throw Error('Synthetic discovery timed out');await new Promise(resolve=>setTimeout(resolve,10));}
      await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
      const controls=[...editor.form.querySelectorAll('input,select,textarea,button')].filter(n=>n.getClientRects().length),box=dialog.getBoundingClientRect();
      const result={provider,models:[...editor.modelSelect.options].map(n=>n.value).filter(Boolean),modelValue:editor.model.input.value,selectorVisible:!editor.modelSelect.hidden,
        labels:controls.filter(n=>['INPUT','TEXTAREA','SELECT'].includes(n.tagName)).every(n=>n.labels?.length||n.getAttribute('aria-label')),
        noOverflow:dialog.scrollWidth<=dialog.clientWidth+1&&controls.every(n=>{const r=n.getBoundingClientRect();return r.width>20&&r.left>=box.left-1&&r.right<=box.right+1;}),
        urlVisible:editor.baseUrl.input.getClientRects().length>0,protocolVisible:!!panel.editApiFormat?.getClientRects().length,
        actions:calls.map(x=>x.action),font:getComputedStyle(editor.name.input).fontSize};
      globalThis.__nativeApiLayout={dialog,panel};return result;
    },provider);
    expect(result.selectorVisible).toBe(true);expect(result.models).toEqual(['fixture-model','fixture-alternate']);expect(result.labels).toBe(true);expect(result.noOverflow).toBe(true);
    expect(result.urlVisible).toBe(provider!=='deepseek');expect(result.protocolVisible).toBe(provider==='zcode');expect(result.actions).toEqual([provider==='codex'?'modelsDraft':'models-draft']);
    if(provider!=='deepseek')expect(result.modelValue).toBe('fixture-model');
    await testInfo.attach(provider+'-api-layout',{body:await page.locator('#native-api-layout').screenshot(),contentType:'image/png'});
    await testInfo.attach(provider+'-api-checks',{body:JSON.stringify(result),contentType:'application/json'});
    await page.evaluate(()=>{__nativeApiLayout.panel.dispose();__nativeApiLayout.dialog.remove();delete globalThis.__nativeApiLayout;});
  }
});

test('native saved and connected composer keeps production control geometry and drafts',async({page},testInfo)=>{
  await ready(page);await page.evaluate(()=>BridgeFeatures.load('chat'));
  const before=await page.evaluate(async()=>{
    document.getElementById('login').hidden=true;
    const root=document.createElement('div');root.className='app';root.id='composer-layout-test';root.style.cssText='position:fixed;inset:0;background:var(--page);z-index:10';document.body.append(root);
    const select=document.createElement('select');for(const id of ['codex','zcode'])select.add(new Option(id,id));
    const session={id:'sess_fixture',title:'ZCode 连接状态布局验收',cwd:'/fixture',model:'account:zai-start-plan/GLM-5.3-Flash$low',modelDisplayName:'GLM-5.3-Flash',effort:'low',composerCapabilities:{models:true,skills:true,permissions:true,attachments:true,plan:true,goal:true}};
    const history={session:{...session,status:'unknown',readOnly:true,connectionState:'detached'},readOnly:true,capabilities:{send:false,resume:true,plan:false,goal:false},messages:Array.from({length:20},(_,i)=>({id:'reply-'+i,role:'assistant',text:'仅供布局验证的历史消息 '+i+'。保留阅读位置与上传附件。'}))};
    const live={session:{...session,status:'idle',runtimeKnown:true,connectionState:'ready',permissionMode:'build',permissionLabel:'变更前确认'},capabilities:{send:true,models:true,skills:true,permissions:true,attachments:true,uploadFiles:true,plan:true,goal:true},messages:history.messages};
    const view=new DesktopSessionsView({root,select,notify(){},onSelect(){},csrf:()=>'',onUnauthorized(){},request:async(url,body)=>{if(body)throw Error('Unexpected write');return url.includes('/detail')?history:url.includes('/workspace')?{entries:[],project:'/fixture'}:{sessions:[session]};}});
    view.setClientStates([{id:'zcode',enabled:true,connected:true,historyAvailable:true}]);await view.choose('zcode');await view.open(session.id);clearTimeout(view.timer);
    view.input.value='保留这份尚未发送的草稿';view.attachments.rows.push({id:'fixture-upload',name:'notes.txt',size:20,status:'ready'});view.attachments.render();view.messages.scrollTop=120;view.rememberReading();
    await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
    const items=[view.modelButton,view.reminderButton,view.skillsButton,view.permissionsButton,view.contextButton,view.composerToggle,view.attachButton,view.mode,view.workMode,view.send];
    const measure=()=>items.map(node=>{const b=node.getBoundingClientRect();return {left:b.left,top:b.top,width:b.width,height:b.height,hidden:node.hidden};});
    globalThis.__composerLayout={root,view,live,measure,first:view.messageList.firstElementChild,scroll:view.messages.scrollTop};
    return {rects:measure(),disabled:[view.modelButton,view.skillsButton,view.permissionsButton,view.workMode,view.send].every(n=>n.disabled),text:view.modelName.textContent,modelLabelWidth:view.modelName.clientWidth,scroll:view.messages.scrollTop};
  });
  expect(before.disabled).toBe(true);expect(before.text).toBe('GLM-5.3-Flash');expect(before.modelLabelWidth).toBeGreaterThanOrEqual(50);expect(before.rects.every(r=>!r.hidden&&r.width>10)).toBe(true);
  await testInfo.attach('composer-before',{body:await page.locator('#composer-layout-test .ds-composer').screenshot(),contentType:'image/png'});
  const after=await page.evaluate(async()=>{const f=__composerLayout;f.view.applyDetail(f.live);await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));return {rects:f.measure(),draft:f.view.input.value,attachments:f.view.attachments.rows.map(row=>row.id),sameMessage:f.view.messageList.firstElementChild===f.first,scroll:f.view.messages.scrollTop,canSend:!f.view.send.disabled,formFits:f.view.form.scrollWidth<=f.view.form.clientWidth+1};});
  expect(after.canSend).toBe(true);expect(after.draft).toBe('保留这份尚未发送的草稿');expect(after.attachments).toEqual(['fixture-upload']);expect(after.sameMessage).toBe(true);expect(Math.abs(after.scroll-before.scroll)).toBeLessThanOrEqual(1);
  for(let i=0;i<after.rects.length;i++)for(const key of ['left','top','width','height'])expect(Math.abs(after.rects[i][key]-before.rects[i][key]),'control '+i+' '+key).toBeLessThanOrEqual(1);
  const width=testInfo.project.use.viewport.width;expect(after.formFits).toBe(true);expect(after.rects.slice(6).every(r=>r.left>=0&&r.left+r.width<=width+1)).toBe(true);
  await testInfo.attach('composer-after',{body:await page.locator('#composer-layout-test .ds-composer').screenshot(),contentType:'image/png'});
  await testInfo.attach('composer-geometry',{body:JSON.stringify({before,after}),contentType:'application/json'});
  await page.evaluate(()=>{__composerLayout.view.clear();__composerLayout.root.remove();delete globalThis.__composerLayout;});
});

test('shared native API save actions match Codex and remain reachable by dialog scrolling',async({page},testInfo)=>{
  await ready(page);
  for(const provider of ['claude','deepseek','zcode']){
    const result=await page.evaluate(async provider=>{
      const dialog=document.createElement('dialog');dialog.className='picker';dialog.id='native-api-footer';const root=document.createElement('div');dialog.append(root);document.body.append(dialog);dialog.showModal();
      const value={accounts:[],activeIds:[],canSwitch:true,capabilities:{quota:false,addApi:true,...provider==='zcode'?{apiFormats:[{value:'openai-responses',label:'OpenAI Responses'}]}:{}}};
      const panel=new DesktopClientAccountsPanel({root,provider,desktop:true,visible:()=>false,read:async()=>value,request:async()=>{throw Error('No API request expected');}});
      panel.accept(value);panel.addApiButton.click();panel.editName.input.value='独立测试 API';panel.editUrl.input.value='https://synthetic.invalid/v1';panel.editKey.input.value='synthetic-key';panel.editModels.input.value='fixture-model';
      panel.editSubmit.scrollIntoView({block:'end'});await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
      const box=dialog.getBoundingClientRect(),save=panel.editSubmit.getBoundingClientRect(),cancel=panel.editCancel.getBoundingClientRect();
      const style=getComputedStyle(panel.editSubmit),baseline=getComputedStyle(codexCliAccessPanel.save);globalThis.__nativeApiFooter={dialog,panel};return {primaryMatches:['backgroundColor','borderTopColor','color','fontWeight'].every(key=>style[key]===baseline[key]),scrollable:dialog.scrollHeight>dialog.clientHeight,visible:save.width>30&&save.top>=box.top&&save.bottom<=box.bottom+1&&save.left>=box.left&&save.right<=box.right,oneRow:Math.abs(save.top-cancel.top)<1};
    },provider);
    expect(result.visible).toBe(true);expect(result.oneRow).toBe(true);expect(result.primaryMatches).toBe(true);
    await testInfo.attach(provider+'-api-footer',{body:await page.locator('#native-api-footer').screenshot(),contentType:'image/png'});
    await page.evaluate(()=>{__nativeApiFooter.panel.dispose();__nativeApiFooter.dialog.remove();delete globalThis.__nativeApiFooter;});
  }
});

test('inline account connection methods confirm switches and keep normal lists quiet',async({page},testInfo)=>{
 const modes=[{id:'private-pipe',label:'本机连接',description:'从网关启动 ZCode，沿用原 App 的账号和项目。',configured:true,available:true},{id:'official-remote',label:'官方远控',description:'使用原 App 的同一官方账号；会话经过官方中继。',configured:true,available:true}];
 let client={id:'zcode',name:'ZCode',enabled:true,configured:true,installed:true,connected:true,running:true,historyAvailable:true,connectionMode:'private-pipe',connectionModes:modes,capabilities:{accounts:true,create:true}},writes=[];
 const clients=()=>[{id:'codex',name:'Codex',enabled:true,configured:true,connected:true},client];
 await page.route('**/api/clients',route=>{if(route.request().method()==='POST'){const body=route.request().postDataJSON();writes.push(body);client={...client,connectionMode:body.connectionMode};}return route.fulfill({json:{clients:clients(),gatewayRunning:true}});});
 await page.route('**/api/desktop-sessions/zcode/list',route=>route.fulfill({json:{sessions:[{id:'sess_fixture',title:'独立布局验收聊天',cwd:'/fixture',backend:'desktop'}],connected:false,readOnly:true,sessionAccess:true}}));
 await page.route('**/api/desktop-sessions/zcode/accounts',route=>route.fulfill({json:{accounts:[],activeIds:[],current:{name:'官方账号'},capabilities:{quota:false,addApi:false}}}));
 await ready(page,'session-menu');await page.evaluate(()=>clientNavigation.choose('zcode'));
 await expect(page.locator('.ds-list-status')).toHaveText('');await expect(page.locator('.ds-list-status')).toBeHidden();
 await page.locator('#accounts-button').click();
 const options=page.locator('.accounts-management-action .client-connection-options'),local=options.locator('[data-connection-mode="private-pipe"]'),official=options.locator('[data-connection-mode="official-remote"]');
 await expect(local).toBeVisible();await expect(official).toBeVisible();await expect(official).toBeEnabled();
 const layout=await options.evaluate(node=>{const box=node.getBoundingClientRect(),buttons=[...node.children].map(button=>button.getBoundingClientRect());return {sameRow:Math.abs(buttons[0].top-buttons[1].top)<1,fits:buttons.every(b=>b.left>=box.left&&b.right<=box.right+1),labels:[...node.children].map(b=>b.textContent)};});
 expect(layout.sameRow).toBe(true);expect(layout.fits).toBe(true);expect(layout.labels).toEqual(['本机连接','官方远控']);
 await testInfo.attach('inline-account-methods',{body:await page.locator('dialog[open]').screenshot(),contentType:'image/png'});
 await official.click();const confirm=page.locator('.client-disable-dialog');await expect(confirm).toBeVisible();await expect(confirm).toContainText('切换应用接入方式');await expect(confirm).toContainText('不会退出或重启电脑 App');expect(writes).toHaveLength(0);
 await confirm.getByRole('button',{name:'取消',exact:true}).click();expect(writes).toHaveLength(0);await expect(local).toHaveAttribute('aria-pressed','true');
 await official.click();await expect(confirm).toBeVisible();await testInfo.attach('mode-switch-confirmation',{body:await confirm.screenshot(),contentType:'image/png'});
 await confirm.getByRole('button',{name:'确认切换',exact:true}).click();await expect(official).toHaveAttribute('aria-pressed','true');expect(writes).toEqual([{provider:'zcode',enabled:true,connectionMode:'official-remote'}]);await expect(page.locator('dialog[open]')).toHaveCount(1);
 await page.evaluate(()=>document.querySelector('dialog[open]').close());
 await page.evaluate(()=>clientNavigation.applyClients({clients:clientNavigation.clients.map(row=>row.id==='zcode'?{...row,connected:false,retryable:true,setupStatus:'needs-retry',reason:'测试断线提示'}:row)}));
 await expect(page.locator('.ds-list-status')).toContainText('测试断线提示');await expect(page.locator('.ds-list-status')).toBeVisible();
});

test('new-chat models use shared choices before the first input', async ({page}, testInfo) => {
  test.setTimeout(30000);page.setDefaultTimeout(7000);
  const codexRequests=[];
  const catalog={models:[{id:'expensive-first',efforts:['high']},{id:'preferred',efforts:['low','high'],defaultEffort:'low'},{id:'alternate',efforts:['low','high'],defaultEffort:'low'}],currentModel:'preferred',currentEffort:'low',cache:{identity:'fixture-account'},capabilities:{models:true,effort:true}};
  await page.route('**/api/projects**',async route=>{
    const url=new URL(route.request().url());
    const value=url.pathname.endsWith('/models')?catalog:url.pathname.endsWith('/trust')?{required:false}:{createModels:true,projects:[{key:'local|fixture',host:'local',cwd:'/fixture/project',name:'Project',type:'desktop'}],environments:[{key:'desktop|local',type:'desktop',surface:'desktop',host:'local',name:'Local'}]};
    if(url.pathname.endsWith('/models'))codexRequests.push(url.search);
    await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(value)});
  });
  await page.route('**/api/sessions',async route=>{
    if(route.request().method()!=='POST')return route.continue();
    codexRequests.push(route.request().postDataJSON());
    await route.fulfill({status:400,contentType:'application/json',body:JSON.stringify({error:'Fixture intentionally stops before creating a thread'})});
  });
  await ready(page,'session-menu');await page.evaluate(async()=>{await BridgeFeatures.load('chat');BridgeI18n.setLanguage('zh');await document.fonts.ready;});
  await page.locator('#new-chat').click();
  const dialog=page.locator('#new-chat-dialog');
  await expect(dialog.locator('[aria-label="模型"]')).toHaveValue('preferred');
  await expect(dialog.locator('[aria-label="思考强度"]')).toHaveValue('low');
  await dialog.getByRole('button',{name:'刷新模型',exact:true}).click();
  await expect(dialog.getByRole('button',{name:'刷新模型',exact:true})).toBeEnabled();
  expect(codexRequests.some(value=>typeof value==='string'&&value.includes('refresh=true'))).toBe(true);
  await dialog.locator('[aria-label="模型"]').selectOption('alternate');await dialog.locator('[aria-label="思考强度"]').selectOption('high');
  expect(await dialog.evaluate(node=>node.scrollWidth<=node.clientWidth+1)).toBe(true);
  await testInfo.attach('creation-models-codex',{body:await page.screenshot(),contentType:'image/png'});
  await dialog.locator('#new-chat-submit').click();await expect.poll(()=>codexRequests.some(value=>value?.model==='alternate')).toBe(true);
  const submitted=codexRequests.find(value=>value?.model==='alternate');expect(submitted.effort).toBe('high');expect(submitted.modelCatalogIdentity).toBe('fixture-account');
  await dialog.getByRole('button',{name:'关闭新建聊天',exact:true}).click();
  await page.evaluate(readFileSync(path.join(__dirname,'creation-models.browser.js'),'utf8'));
  for(const provider of ['claude','deepseek','zcode']){
    expect((await page.evaluate(provider=>runCreationModelsBrowserChecks(provider),provider)).passed).toBeGreaterThan(8);
    await testInfo.attach('creation-models-'+provider,{body:await page.screenshot(),contentType:'image/png'});
    expect((await page.evaluate(()=>window.__creationModelsPreview.submit())).passed).toBeGreaterThan(10);
  }
});
