'use strict';
const {test, expect} = require('@playwright/test');
const path = require('node:path');
const storeSource = path.resolve(__dirname, '../../web/features/chat/offline-reader/composer-store.js');
const route = {provider:'codex', host:'local', session:'019fe103-126e-763d-8234-789136720eae'};
const owner = {authenticated:true, sessionSync:{version:1,gatewayId:'a'.repeat(32),identity:'b'.repeat(64)}, draftIdentity:'fixture-gateway'};
const account = 'c'.repeat(64);
const archiveId = JSON.stringify([route.provider,route.host,route.session]);

function backend(context) {
  const posted = [], receipts = new Set();
  return context.route('**/delivery?**', async request => {
    const url = new URL(request.request().url()), id = url.searchParams.get('id');
    if (request.request().method() === 'GET') return request.fulfill({json:{deliveryVersion:1,account,ready:true,status:receipts.has(id)?'accepted':'notFound'}});
    const message = request.request().postDataJSON();
    expect(message.id).toBe(id); expect(message.sessionId).toBe(route.session);
    expect(url.searchParams.get('account')).toBe(account);
    expect(url.searchParams.get('gatewayId')).toBe(owner.sessionSync.gatewayId);
    posted.push(message); receipts.add(id);
    return request.fulfill({json:{id,status:'accepted'}});
  }).then(() => posted);
}
async function bindPage(page) {
  await page.evaluate(owner => {
    BridgeOfflineComposer.bind(owner,async (path,body) => {
      const response = await fetch(path,body === undefined ? {} : {method:'POST',body:JSON.stringify(body)});
      return response.json();
    });
  },owner);
}
test('浏览器重开后同一 IndexedDB 草稿保留，两页同时恢复只投递 A 一次',async ({context,page}) => {
  await context.route('**/__fixture_health', r=>r.fulfill({contentType:'text/html',body:'<html></html>'}));
  await context.route('**/api/auth',r=>r.fulfill({json:owner}));
  const posted = await backend(context);
  async function openStore(tab,offline) {
    await tab.goto('/__fixture_health');
    await tab.evaluate(offline => { window.OfflineChatReader = {cold:false,enabled:offline}; },offline);
    await tab.addScriptTag({path:storeSource}); await bindPage(tab);
  }
  await openStore(page,true);
  await page.evaluate(async ({route,account,owner}) => {
    await BridgeOfflineComposer.observe(route,{account,gateway:owner.draftIdentity,canSend:true});
    await BridgeOfflineComposer.saveDraft(route,{text:'A'});
    await Promise.all([BridgeOfflineComposer.enqueue(route,{text:'A',mode:'send'}),BridgeOfflineComposer.saveDraft(route,{text:'B'})]);
  },{route,account,owner});
  expect(posted).toHaveLength(0);
  const other = await context.newPage(); await openStore(other,true); await openStore(page,true);
  const before = await other.evaluate(route=>BridgeOfflineComposer.get(route),route);
  expect(before.draft.text).toBe('B'); expect(before.outbox[0].body.text).toBe('A');
  await Promise.all([page,other].map(tab=>tab.evaluate(async () => {OfflineChatReader.enabled=false; await BridgeOfflineComposer.flush();})));
  expect(posted.map(req=>req.text)).toEqual(['A']);
  const after = await page.evaluate(route=>BridgeOfflineComposer.get(route),route);
  expect(after.draft.text).toBe('B'); expect(after.outbox).toHaveLength(0);
});

test('手机离线页面输入 A 并待发、B 留作草稿，冷重开与重连均保留 B',async ({page,context}) => {
  const table = new Map(), posted = await backend(context);
  await context.route('**/api/activity',r=>r.fulfill({status:503,json:{error:'Activity temporarily unavailable'}}));
  await context.exposeFunction('composerArchive', event => {
    const {sessionId} = event, action = event.type || event.action;
    const old = table.get(sessionId) || {revision:0,value:null};
    if (action === 'composerRead') return old;
    if (action === 'composerWrite') {
      if (old.revision !== event.revision) return {...old,applied:false};
      const row = {revision:old.revision+1,value:event.value}; table.set(sessionId,row); return {...row,applied:true};
    }
    if (action === 'composerPending') return {sessions:[...table].filter(([,row])=>row.value.outbox?.length).map(([id])=>id)};
    if (action === 'list') return {sessions:[{id:archiveId,title:'离线投递测试',syncedAt:1795200000000}],lang:'zh',composerWritable:true};
    if (action === 'session') return {id:archiveId,title:'离线投递测试',turns:[],before:null,gaps:false};
    return {};
  });
  await context.addInitScript(()=>{
    window.OfflineChatHost={postMessage(raw){const event=JSON.parse(raw);void window.composerArchive(event).then(row=>OfflineChatReader.receive(event.id,row));}};
  });
  await page.goto('/?case=session-menu');
  await expect(page.locator('#app')).toBeVisible();
  async function nativeOnline() {
    await page.evaluate(owner => {
      window.CodexBridgeNativeOffline={request:window.composerArchive};
      window.BridgeChatSyncCache.owner = owner.sessionSync;
    },owner);
    await bindPage(page);
  }
  await nativeOnline();
  await page.evaluate(({route,account,owner})=>BridgeOfflineComposer.observe(route,{account,gateway:owner.draftIdentity,title:'离线投递测试',canSend:true}),{route,account,owner});
  await context.route(url=>url.searchParams.get('offline-composer-shell')==='1',async r=>{
    const reply=await r.fetch(); return r.fulfill({response:reply,body:(await reply.text()).replace('<html ','<html data-bridge-offline ')});
  });
  let cold = true; const unexpected = [];
  await context.route('**/api/**',r=>{
    if (cold) { unexpected.push(r.request().url());return r.abort(); }
    if (new URL(r.request().url()).pathname === '/api/auth') return r.fulfill({json:owner});
    return r.fallback();
  });
  const openCold = async () => {
    await page.goto('/?case=session-menu&offline-composer-shell=1');
    await page.locator('#sessions .session').filter({hasText:'离线投递测试'}).click();
    await expect(page.locator('#composer textarea')).toBeEditable();
    await expect(page.locator('#notice')).toContainText('可编辑草稿');
  };
  await openCold();
  const input=page.locator('#composer textarea');
  await input.fill('离线消息 A');
  await page.getByRole('button',{name:'存入待发'}).click();
  await expect(page.locator('.offline-outbox:visible')).toContainText('离线消息 A');
  await input.fill('未发送草稿 B');
  await expect.poll(()=>table.get(archiveId)?.value.draft?.text).toBe('未发送草稿 B');
  await openCold();
  await expect(input).toHaveValue('未发送草稿 B');
  await expect(page.locator('.offline-outbox:visible')).toContainText('离线消息 A');
  expect(unexpected).toEqual([]); expect(posted).toHaveLength(0);
  cold=false;
  await page.goto('/?case=session-menu'); await expect(page.locator('#app')).toBeVisible();
  await nativeOnline();
  const stillConnected = await page.evaluate(async () => {
    let status;
    try { await api('/api/activity',{hosts:['local']}); } catch (error) { status=error.status; }
    return {status,offline:OfflineChatReader.enabled};
  });
  expect(stillConnected).toEqual({status:503,offline:false});
  await page.evaluate(()=>BridgeOfflineComposer.flush());
  await expect.poll(()=>posted.map(item=>item.text)).toEqual(['离线消息 A']);
  await expect.poll(()=>table.get(archiveId).value.outbox.length).toBe(0);
  expect(table.get(archiveId).value.draft.text).toBe('未发送草稿 B');
});
