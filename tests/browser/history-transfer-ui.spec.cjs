'use strict';
const {test, expect} = require('@playwright/test');
const {spawn} = require('node:child_process');
const {createInterface} = require('node:readline');
const path = require('node:path');
const {readFileSync}=require('node:fs');

test('phone selection retains choices through a space warning and resumes verified missing content', async ({page, context}, info) => {
  const root = path.resolve(__dirname, '../..');
  const server = spawn('python3', ['-B', 'tests/browser/session-transfer-http-fixture.py', '--provider', 'codex'], {cwd: root});
  let stderr = '';
  server.stderr.on('data', chunk => { stderr += chunk; });
  const lines = createInterface({input: server.stdout});
  let prepared=false,manifestPosts=0;
  try {
    const origin = await new Promise((resolve, reject) => {
      lines.once('line', resolve); server.once('error', reject);
      server.once('exit', code => reject(Error('fixture exited ' + code + ' ' + stderr)));
    });
    await context.addInitScript(() => {
      const state = window.transferFixture = {freeBytes: 1000, calls: [], chunks: ['file:0'], committed: ['text']};
      window.CodexBridgeNativeOffline = {async request(event) {
        state.calls.push(event);
        const network = {foreground: true, allowPrefetch: false, allowTransfer: true, networkAvailable: true,
          freeBytes: state.freeBytes, clearRevision: '0', tombstones: {}, recordBatch:1};
        if (event.type === 'state' || event.type === 'auth' || event.type === 'transferStats') return network;
        if (event.type === 'read') return {turns: []};
        if (event.type === 'transferTaskLoad') return {task: null};
        if (event.type === 'transferCached') return {present: state.committed.includes(event.version)};
        if (event.type === 'transferChunkHas') return {present: state.chunks.includes(event.version + ':' + event.index)};
        if (event.type === 'transferChunkFetch') { state.chunks.push(event.version + ':' + event.index); return {saved: true}; }
        if (event.type.endsWith('Commit')) state.committed.push(event.version);
        return {saved: true};
      }};
    });
    await page.route('**/api/history-transfer/**', async route => {
      const url = new URL(route.request().url());
      if (url.pathname.endsWith('/catalog')) return route.fulfill({json: url.searchParams.has('cursor') ? {sessions: [
        {id:'claude-selected',sessionId:'claude-native',title:'Claude portable history',client:'claude',host:'local'},
      ], unavailable:[{client:'zcode',entry:'zcode'}]} : {sessions: [
        {id: 'selected', sessionId: 'native', title: 'Portable history', client: 'codex', host: 'local'},
      ],nextCursor:'100',sessionCount:2,recordBatch:1}});

      if (route.request().method() === 'POST') {
        manifestPosts++;expect(route.request().postDataJSON().batchRecords).toBe(true);
        return route.fulfill({json:{id:'manifest',capturedAt:100,sessionCount:2}});
      }
      if(!prepared)return route.fulfill({json:{id:'manifest',status:'preparing',sessionCount:2,summary:{processed:1,currentTitle:'Portable history'}}});
      return route.fulfill({json: {id: 'manifest', status: 'ready', capturedAt: 100,
        summary: {sessions: 1, records: 1, attachments: 1, textBytes: 8, attachmentBytes: 900000},
        items: [
          {id: 'text', kind: 'record', version: 'text', bytes: 8, key: 'text', recordVersion: 'original', cacheSessionId: 'native', turnId: 'turn', questionTime: 1, session: {title: 'Portable history'}},
          {id: 'file', kind: 'attachment', version: 'file', bytes: 900000, chunkBytes: 524288, key: 'image', cacheSessionId: 'native', turnId: 'turn', questionTime: 1, session: {title: 'Portable history'}},
        ]}});
    });
    await page.goto(origin);
    await page.locator('#username').fill('fixture'); await page.locator('#password').fill('session-transfer-test-only');
    await page.locator('#login-button').click();
    await expect(page.locator('#app')).toBeVisible();
    await page.locator('#sidebar [data-open-appearance]').click();
    for(const id of ['cli-connections-button','history-transfer-open','transfer-usage','open-message-management']){
      expect(await page.locator('#'+id).evaluate(node=>node.parentElement.classList.contains('settings-action-row'))).toBe(true);
    }
    await page.locator('#history-transfer-open').click();
    const dialog = page.locator('.local-transfer-dialog');
    await expect(dialog.getByText('Portable history', {exact: true})).toBeVisible();
    await dialog.locator('input[type=checkbox]').check();
    await expect(dialog.getByRole('tab',{name:'Claude (1)',exact:true})).toBeVisible();
    await expect(dialog.locator('.local-transfer-count')).toHaveText('已选择 1 个');
    const alignment=await dialog.locator('.local-transfer-choice').evaluate(row=>{
      const input=row.querySelector('input').getBoundingClientRect(),title=row.querySelector('strong').getBoundingClientRect();
      return {inputTop:input.top,titleTop:title.top,titleBottom:title.bottom};
    });
    expect(alignment.inputTop).toBeLessThan(alignment.titleBottom+10);
    await dialog.getByRole('tab',{name:'Claude (1)',exact:true}).click();
    await dialog.locator('input[type=checkbox]').check();
    await expect(dialog.locator('.local-transfer-count')).toHaveText('已选择 2 个');
    await dialog.getByRole('tab',{name:'ZCode (0)',exact:true}).click();
    await expect(dialog.getByRole('status')).toContainText('部分接入读取失败');
    await dialog.getByRole('tab',{name:'Codex (1)',exact:true}).click();
    await expect(dialog.locator('input[type=checkbox]')).toBeChecked();
    await page.screenshot({path:info.outputPath('transfer-selection.png')});

    await dialog.getByRole('button', {name: '查看下载量', exact: true}).click();
    const preparing=page.locator('.local-transfer-progress');
    await expect(preparing).toContainText('正在准备会话');
    await dialog.getByRole('button',{name:'最小化',exact:true}).click();
    await expect(page.locator('.local-transfer-dialog')).toHaveCount(0);
    await expect(preparing).toBeVisible();
    expect(await preparing.evaluate(node=>node.classList.contains('download-panel'))).toBe(true);
    await page.screenshot({path:info.outputPath('transfer-preparing.png')});
    const beforeDrag=await preparing.boundingBox();
    const handle=preparing.locator('.download-summary'),handleBox=await handle.boundingBox();
    await page.mouse.move(handleBox.x+30,handleBox.y+15);await page.mouse.down();
    await page.mouse.move(handleBox.x+10,handleBox.y-100,{steps:8});await page.mouse.up();
    const afterDrag=await preparing.boundingBox();expect(afterDrag.y).toBeLessThan(beforeDrag.y-80);
    await expect(preparing.locator('.download-details')).toBeVisible();
    await preparing.getByRole('button',{name:'查看任务',exact:true}).click();
    await expect(page.locator('.local-transfer-dialog')).toContainText('正在准备会话');
    prepared=true;
    const restoredDialog=page.locator('.local-transfer-dialog');
    await expect(restoredDialog.getByText('预计最多下载', {exact: true})).toBeVisible();
    expect(manifestPosts).toBe(1);
    await restoredDialog.getByRole('button',{name:'最小化',exact:true}).click();
    await expect(preparing).toContainText('准备完成，等待开始');
    await preparing.getByRole('button',{name:'查看任务',exact:true}).click();
    await expect(page.locator('.local-transfer-dialog').getByText('空间还差', {exact: false})).toBeVisible();
    await page.locator('.local-transfer-dialog').getByRole('button', {name: '调整选择', exact: true}).click();
    await expect(page.locator('.local-transfer-dialog input[type=checkbox]')).toBeChecked();
    await page.locator('.local-transfer-dialog').getByRole('button', {name: '查看下载量', exact: true}).click();
    await page.locator('.local-transfer-dialog').getByRole('button', {name: '先传一部分', exact: true}).click();
    const panel = page.locator('.local-transfer-progress');
    await expect(panel).toContainText('空间不足，已暂停');
    await expect(panel.locator('.download-summary')).toContainText('58%');
    const fill=await panel.locator('.download-summary').evaluate(node=>({progress:node.style.getPropertyValue('--transfer-progress'),paint:getComputedStyle(node).backgroundImage}));
    expect(parseFloat(fill.progress)).toBeGreaterThan(58);expect(parseFloat(fill.progress)).toBeLessThan(59);
    expect(fill.paint).toContain('linear-gradient');
    await page.screenshot({path:info.outputPath('transfer-partial.png')});
    await page.evaluate(() => { transferFixture.freeBytes = 4e6; });
    await panel.getByRole('button', {name: '继续', exact: true}).click();
    await expect(panel).toContainText('传输完成');
    const downloads = await page.evaluate(() => transferFixture.calls.filter(event => event.type === 'transferChunkFetch'));
    expect(downloads).toHaveLength(1); expect(downloads[0].version).toBe('file');
    expect(downloads[0].offset).toBe(524288); expect(downloads[0].length).toBe(375712);
    await page.screenshot({path: info.outputPath('transfer-completed.png')});
    await page.evaluate(async () => {
      BridgeChatSyncCache.owner = {gatewayId: 'f'.repeat(32), identity: 'e'.repeat(64)};
      await BridgeHistoryTransfer.connect(async () => ({}));
    });
    await expect(panel).toBeHidden();
  } finally { lines.close(); server.kill('SIGTERM'); }
});

test('complete transferred history remains readable offline with every intermediate step',async({page},info)=>{
  await page.goto('/?case=session-menu');
  await page.waitForFunction(()=>document.getElementById('app')?.hidden===false);
  await page.evaluate(()=>BridgeFeatures.load('chat'));
  await page.evaluate(async()=>{
    const archiveId=JSON.stringify(['codex','local','offline-native']),turn='offline-turn';
    const message=(key,role,order,fields={})=>({key,version:key+'-v',role,order,text:key,row:{key,version:key+'-v',role,order,turnId:turn,turnStatus:'completed',turnCompletionKnown:true,turnStartedAt:1700000000000,turnCompletedAt:1700000002000,...fields}});
    const entries=[message('offline-question','user',0,{text:'Read my saved history'}),
      message('offline-process','activity',.5,{kind:'processPage',processCursor:'capture-epoch.offline-process'}),
      {...message('offline-reasoning','reasoning',1,{kind:'reasoning',truncated:true}),bodyParts:{0:{offset:0,text:'Saved reasoning '.repeat(2000)+'Reasoning end sentinel',next:null}}},
      message('offline-commentary','assistant',2,{phase:'commentary',text:'Saved commentary sentinel'}),
      message('offline-choice','user',3,{interaction:'response',text:'Saved choice sentinel'}),
      message('offline-tool','activity',4,{kind:'commandExecution',title:'Saved command',text:'Saved tool output sentinel'}),
      message('offline-final','assistant',5,{forkable:true,text:'Offline final answer sentinel'})];
    for(const entry of entries)entry.text=entry.row.text;
    window.offlineNativeCalls=[];
    OfflineChatReader.enableHot(async(action,fields)=>{
      offlineNativeCalls.push(action);
      if(action==='list')return {sessions:[{id:archiveId,title:'Complete transferred offline chat',syncedAt:1700000002000}]};
      if(action==='session')return {id:archiveId,title:'Complete transferred offline chat',turns:[{id:turn,content:{messages:Object.fromEntries(entries.map(row=>[row.key,row]))}}],before:null,gaps:false};
      throw Error('Unexpected offline action');
    });
    await loadList(true);
  });
  const network=[];
  await page.route('**/api/**',route=>{network.push(new URL(route.request().url()).pathname);return route.abort();});
  await page.locator('#sessions .session').filter({hasText:'Complete transferred offline chat'}).click();
  const messages=page.locator('#messages');
  await expect(messages.getByText('Offline final answer sentinel',{exact:true})).toBeVisible();
  const fold=messages.locator('.chat-turn-completion');await expect(fold).toHaveAttribute('aria-expanded','false');
  await fold.click();
  await expect(messages.getByText('Saved commentary sentinel',{exact:true})).toBeVisible();
  await expect(messages.getByText('Saved choice sentinel',{exact:true})).toBeVisible();
  const tool=messages.locator('details.activity').filter({hasText:'Saved tool output sentinel'});
  await tool.locator('summary').click();await expect(tool.locator('.activity-body')).toContainText('Saved tool output sentinel');
  const reasoning=messages.locator('details.activity').filter({hasText:'Reasoning end sentinel'});
  await reasoning.locator('summary').click();await expect(reasoning.locator('.activity-body')).toContainText('Reasoning end sentinel');
  expect(await messages.getByRole('button',{name:'加载后续过程',exact:true}).count()).toBe(0);
  await page.screenshot({path:info.outputPath('offline-complete-process.png')});
  expect(network).toEqual([]);
});


test('versioned Android injection opens a real chat through the native port without prompt dialogs',async({page,context},info)=>{
 const root=path.resolve(__dirname,'../..'),server=spawn('python3',['-B','tests/browser/session-transfer-http-fixture.py','--provider','codex'],{cwd:root});
 const lines=createInterface({input:server.stdout});
 try{
  const origin=await new Promise((resolve,reject)=>{lines.once('line',resolve);server.once('error',reject);server.once('exit',code=>reject(Error('fixture exited '+code)));});
  const source=readFileSync(path.join(root,'mobile/shared/web/mobile-offline.js'),'utf8');
  await context.addInitScript({content:`
    Object.defineProperty(navigator,'userAgent',{get:()=> 'Mozilla/5.0 BridgeMobile/2.2.2-preview.2026112307-Android BridgeModel/Google%20Pixel%207 BridgeOS/16 BridgeBuild/81'});
    window.nativePrompts=[];window.prompt=(...args)=>{nativePrompts.push(args);return '';};
    window.nativePortCalls=[];
    ${source}
    document.addEventListener('DOMContentLoaded',()=>{
      const channel=new MessageChannel(),state={foreground:true,allowPrefetch:false,allowTransfer:true,networkAvailable:true,clearRevision:'0',freeBytes:1e9};
      channel.port1.onmessage=event=>{
        if(event.data==='ready'){channel.port1.postMessage(JSON.stringify({state}));return;}
        const call=JSON.parse(event.data);nativePortCalls.push(call.type);
        const value=['state','auth','transferStats'].includes(call.type)?state:call.type==='read'?{turns:[]}:call.type==='body'?null:call.type==='transferTaskLoad'?{task:null}:{saved:true};
        channel.port1.postMessage(JSON.stringify({id:call.requestId,ok:true,value}));
      };
      window.postMessage('__BRIDGE_CLIPBOARD_TOKEN__',location.origin,[channel.port2]);
    },{once:true});`});
  await context.route('**/*',route=>new URL(route.request().url()).origin===origin?route.continue():route.abort());
  await page.goto(origin);
  await page.locator('#username').fill('fixture');await page.locator('#password').fill('session-transfer-test-only');await page.locator('#login-button').click();
  await page.locator('#sessions .session').filter({hasText:'Transfer HTTP fixture'}).click();
  await expect(page.locator('#messages').getByText('Answer 89',{exact:true})).toBeVisible();
  const native=await page.evaluate(()=>({prompts:nativePrompts,calls:nativePortCalls,foreground:BridgeChatForeground}));
  expect(native.prompts).toEqual([]);expect(native.calls).toContain('auth');expect(native.calls).toContain('read');expect(native.foreground).toBe(true);
  await page.screenshot({path:info.outputPath('android-versioned-native-chat.png')});
 }finally{lines.close();server.kill('SIGTERM');}
});
