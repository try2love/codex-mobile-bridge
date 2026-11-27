'use strict';
const {test,expect}=require('@playwright/test');
const {spawn}=require('node:child_process');
const {createInterface}=require('node:readline');
const path=require('node:path');

async function mappedBytes(pages){
  const bytes={};
  for(const mode of ['recent','older','process','body']){
    const responses=pages.filter(page=>page.mode===mode);
    bytes[mode]=await Promise.all(responses.map(async({response})=>(await response.body()).length));
  }
  return bytes;
}

const syncResponse=(page,mode)=>page.waitForResponse(response=>{
  const url=new URL(response.url());
  return url.pathname.endsWith('/sync')&&url.searchParams.get('mode')===mode;
});

async function readCommittedSession(page,provider,id,deterministic=false){
  return page.evaluate(async({provider,id,deterministic})=>{
    const cache=BridgeChatSyncCache,scope=cache.scope(provider,'local',id);
    await cache.disk.open();
    const recent=await cache.read(scope,'recent',{}),process=[];
    for(const manifest of await cache.disk.list('page',scope)){
      const [mode,...pairs]=JSON.parse(manifest.key);
      if(mode==='process'){
        const saved=await cache.read(scope,mode,Object.fromEntries(pairs));
        if(!saved)throw Error('Committed process cache is unavailable');
        process.push(...saved.rows);
      }
    }
    if(deterministic){
      // Verify real IndexedDB durability first, then isolate byte-reuse checks
      // from OS storage latency using the same production volatile backend.
      const original=cache.disk,db=await original.open();
      if(!db)throw Error('Committed IndexedDB is unavailable');
      const records=await new Promise((resolve,reject)=>{
        const request=db.transaction('records').objectStore('records').getAll();
        request.onsuccess=()=>resolve(request.result);request.onerror=()=>reject(request.error);
      });
      const memory=new original.constructor(null);
      for(const record of records)memory.volatile.set(record.id,record);
      window.transferPersistedDisk=original;cache.disk=memory;
    }
    return {recent:recent?.rows||[],process:[...new Map(process.map(row=>[row.key,row])).values()]};
  },{provider,id,deterministic});
}

test('a slow IndexedDB lookup falls back twice before storage resumes and preserves committed bytes',async({page,context},info)=>{
  const root=path.resolve(__dirname,'../..');
  const server=spawn(process.platform==='win32'?'python':'python3',['-B','tests/browser/session-transfer-http-fixture.py'],{cwd:root});
  const lines=createInterface({input:server.stdout});
  try{
    const origin=await new Promise((resolve,reject)=>{lines.once('line',resolve);server.once('error',reject);});
    await context.route('**/*',route=>new URL(route.request().url()).origin===origin?route.continue():route.abort());
    await page.goto(origin);await page.locator('#username').fill('fixture');await page.locator('#password').fill('session-transfer-test-only');await page.locator('#login-button').click();
    const entry=page.locator('#sessions .session').filter({hasText:'Transfer HTTP fixture'});
    await entry.click();await expect(page.locator('#messages')).toContainText('Answer 89');
    const id=await entry.getAttribute('data-id');
    await page.evaluate(async()=>{await Promise.all(BridgeChatSyncCache.pending.values());});
    await page.goto(origin+'/?storage=slow');await expect(entry).toBeVisible();
    const saved=await readCommittedSession(page,'codex',id);
    expect(saved.recent.some(row=>row.text==='Answer 89')).toBe(true);
    const evidence=await page.evaluate(async id=>{
      const cache=BridgeChatSyncCache,scope=cache.scope('codex','local',id),disk=cache.disk;
      const prewarmed=await cache.known(scope,'recent',{}),before=await cache.read(scope,'recent',{});
      let release,released=false;const gate=new Promise(resolve=>{release=()=>{released=true;resolve();};});
      const readRefs=disk.readRefs,methods={},stages=[],requests=[];
      disk.readRefs=async function(...args){await gate;return Reflect.apply(readRefs,this,args);};
      for(const name of ['known','afterProbe','hydrateOnline']){
        methods[name]=cache[name];
        cache[name]=async function(...args){
          const stage={name,complete:false};stages.push(stage);
          const value=await Reflect.apply(methods[name],this,args);stage.complete=true;return value;
        };
      }
      const target={scope,url:sessionUrl(id,'sync','local'),authorized:()=>cache.active(scope),request:async(url,...args)=>{
        const known=new URL(url,location.href).searchParams.getAll('known').length;
        const wire=await api(url,...args);
        requests.push({known,rows:wire.rows.length,released,pendingStages:stages.filter(row=>!row.complete).map(row=>row.name)});
        return wire;
      }};
      try{
        for(let attempt=0;attempt<2;attempt++){
          cache.resident.clear();
          const value=await BridgeChatSync.read(target,'recent',{},'manual',()=>true);
          if(!value.rows.some(row=>row.text==='Answer 89'))throw Error('Authenticated network content was not returned');
        }
      }finally{
        release();disk.readRefs=readRefs;Object.assign(cache,methods);
        await Promise.all(cache.pending.values());
      }
      const after=await cache.read(scope,'recent',{});
      return {prewarmKnown:prewarmed.getAll('known').length,requests,
        committedRowsUnchanged:JSON.stringify(before.rows)===JSON.stringify(after.rows)};
    },id);
    expect(evidence.prewarmKnown).toBeGreaterThan(0);
    expect(evidence.requests).toHaveLength(2);
    for(const request of evidence.requests){
      expect(request.known).toBe(0);expect(request.rows).toBe(21);expect(request.released).toBe(false);
      expect(request.pendingStages.length).toBeGreaterThan(0);expect(request.pendingStages.every(name=>name==='known')).toBe(true);
    }
    expect(evidence.committedRowsUnchanged).toBe(true);
    await info.attach('slow-indexeddb-budget',{contentType:'application/json',body:JSON.stringify(evidence)});
  }finally{lines.close();server.kill('SIGINT');if(server.exitCode===null&&server.signalCode===null)await new Promise(resolve=>server.once('exit',resolve));}
});

test('an Android foreground chat recovers a changed process cursor without reloading the page',async({page,context})=>{
  const root=path.resolve(__dirname,'../..');
  const server=spawn(process.platform==='win32'?'python':'python3',['-B','tests/browser/session-transfer-http-fixture.py'],{cwd:root});
  const lines=createInterface({input:server.stdout});
  try{
    const origin=await new Promise((resolve,reject)=>{lines.once('line',resolve);server.once('error',reject);server.once('exit',code=>reject(Error('Fixture exited '+code)));});
    await context.route('**/*',route=>new URL(route.request().url()).origin===origin?route.continue():route.abort());
    await page.addInitScript(()=>{
      Object.defineProperty(navigator,'userAgent',{value:navigator.userAgent+' BridgeMobile/0.1-Android'});
      window.CodexBridgeNativeOffline={request:async()=>({recordPages:1,foreground:true,allowTransfer:true,clearRevision:0,turns:[]})};
    });
    let processReads=0,recentReads=0;
    await page.route(url=>url.pathname.endsWith('/sync'),async route=>{
      const mode=new URL(route.request().url()).searchParams.get('mode');
      if(mode==='recent')recentReads++;
      if(mode==='process'&&++processReads===1)return route.fulfill({status:409,contentType:'application/json',body:JSON.stringify({error:'过程记录已变化，正在更新',code:'sync_cursor_changed'})});
      return route.continue();
    });
    await page.goto(origin);
    await page.locator('#username').fill('fixture');await page.locator('#password').fill('session-transfer-test-only');await page.locator('#login-button').click();
    await page.locator('#sessions .session').filter({hasText:'Transfer HTTP fixture'}).click();
    await expect(page.locator('#messages')).toContainText('Answer 89');
    await page.evaluate(()=>window.recoveryDocumentMarker='same-document');
    const baseline=recentReads;
    await page.locator('#messages').getByRole('button',{name:'已完成',exact:true}).last().click();
    await expect(page.locator('#messages .activity:not(.chat-process-collapsed)')).toHaveCount(24);
    await expect(page.locator('#messages').getByRole('button',{name:/读取失败，点击重试|加载后续过程/})).toHaveCount(0);
    expect(recentReads).toBeGreaterThan(baseline);expect(processReads).toBeGreaterThan(1);
    expect(await page.evaluate(()=>window.recoveryDocumentMarker)).toBe('same-document');
    // Android can publish repeated foreground=true storage/network facts. They
    // must not continually restart recent reads or abort the current watch.
    const before=recentReads;
    await page.evaluate(()=>{for(let i=0;i<5;i++)window.dispatchEvent(new CustomEvent('bridge-chat-foreground',{detail:{foreground:true}}));});
    await page.waitForTimeout(600);expect(recentReads).toBe(before);
  }finally{lines.close();server.kill();}
});

test('process metadata remains visible and native read-only status stays below chat tabs',async({page,context},info)=>{
  const root=path.resolve(__dirname,'../..');
  const server=spawn(process.platform==='win32'?'python':'python3',['-B','tests/browser/session-transfer-http-fixture.py'],{cwd:root});
  const lines=createInterface({input:server.stdout});
  try{
    const origin=await new Promise((resolve,reject)=>{lines.once('line',resolve);server.once('error',reject);});
    await context.route('**/*',route=>new URL(route.request().url()).origin===origin?route.continue():route.abort());
    await page.addInitScript(()=>{
      Object.defineProperty(navigator,'userAgent',{value:navigator.userAgent+' BridgeMobile/0.1-Android'});
      window.CodexBridgeNativeOffline={request:async event=>{
        if(event.type==='list')return {sessions:[{id:JSON.stringify(['codex','local',currentId]),title:'Saved fixture',syncedAt:1700000000000}]};
        if(event.type==='session')return {recordPage:1,id:event.sessionId,rows:[],turns:[],hasMore:false,syncedAt:1700000000000};
        return {recordPages:1,foreground:true,clearRevision:'0',turns:[]};
      }};
    });
    await page.goto(origin);await page.locator('#username').fill('fixture');await page.locator('#password').fill('session-transfer-test-only');await page.locator('#login-button').click();
    await page.locator('#sessions .session').filter({hasText:'Transfer HTTP fixture'}).click();await expect(page.locator('#messages')).toContainText('Answer 89');
    await page.evaluate(()=>{
      const rows=[{role:'assistant',phase:'commentary'},{role:'user',interaction:'steer',originalQuestion:false},{role:'reasoning',kind:'reasoning'},{role:'activity',kind:'commandExecution'}];
      const holder=document.createElement('div');holder.id='metadata-fixture';
      for(const [index,row] of rows.entries())holder.append(chatTimeline.renderRow({...row,key:'metadata-'+index,model:'recorded-model',effort:'high',timestamp:1700000000000,text:'Intermediate '+index,turnStatus:'completed'}));
      document.getElementById('messages').append(holder);
    });
    const fixture=page.locator('#metadata-fixture'),assistant=fixture.locator('.message.assistant');
    await expect(assistant.locator('.who')).toHaveText('CODEX · recorded-model high');
    await expect(assistant.locator('.who time')).toHaveCount(0);
    const time=assistant.locator('.message-actions time');await expect(time).toHaveAttribute('datetime',new Date(1700000000000).toISOString());
    expect(await time.evaluate(node=>node.previousElementSibling?.textContent)).toBe('引用');
    await expect(fixture.locator('.message.user .who')).toBeHidden();
    for(const activity of await fixture.locator('details.activity').all()){
      await expect(activity.locator('summary')).not.toContainText('recorded-model');await expect(activity.locator('time')).toHaveCount(0);
    }
    await expect(fixture).not.toContainText('未记录');
    await page.evaluate(()=>window.dispatchEvent(new Event('offline')));
    const notice=page.locator('#notice');await expect(notice).toContainText('只读');await expect(notice).toHaveClass(/offline-readonly-notice/);
    await expect(page.locator('#composer #notice')).toHaveCount(0);await expect(page.locator('#composer')).toBeVisible();await expect(page.locator('#message')).toHaveCSS('visibility','visible');
    const bounds=await page.evaluate(()=>({tabs:document.getElementById('timeline').closest('.wb-layout').previousElementSibling.getBoundingClientRect().bottom,
      notice:document.getElementById('notice').getBoundingClientRect().toJSON(),timeline:document.getElementById('timeline').getBoundingClientRect().top}));
    expect(bounds.notice.y).toBeGreaterThanOrEqual(bounds.tabs-1);expect(bounds.notice.bottom).toBeLessThanOrEqual(bounds.timeline+1);
    const screenshot=info.outputPath('readonly-banner.png');await page.screenshot({path:screenshot});await info.attach('readonly-banner',{path:screenshot,contentType:'image/png'});
    await notice.getByRole('button',{name:'关闭提示',exact:true}).click();await expect(notice).toBeHidden();
    await page.evaluate(()=>{const text=document.getElementById('notice-text');text.textContent=text.textContent;document.getElementById('notice').hidden=false;});
    await expect(notice).toBeHidden();expect(await page.evaluate(()=>OfflineChatReader.enabled)).toBe(true);
    await page.evaluate(()=>{document.getElementById('notice-text').textContent='连接状态已变化';document.getElementById('notice').hidden=false;});
    await expect(notice).toBeVisible();
  }finally{lines.close();server.kill();}
});

test('opening a native chat with an earlier cache cleanup loads without returning to latest',async({page,context},info)=>{
  const root=path.resolve(__dirname,'../..');
  const server=spawn(process.platform==='win32'?'python':'python3',['-B','tests/browser/session-transfer-http-fixture.py'],{cwd:root});
  const lines=createInterface({input:server.stdout});
  try{
    const origin=await new Promise((resolve,reject)=>{
      lines.once('line',resolve);server.once('error',reject);
      server.once('exit',code=>reject(Error('Fixture exited '+code)));
    });
    await context.route('**/*',route=>new URL(route.request().url()).origin===origin?route.continue():route.abort());
    await page.goto(origin);
    await page.locator('#username').fill('fixture');await page.locator('#password').fill('session-transfer-test-only');
    await page.locator('#login-button').click();
    const entry=page.locator('#sessions .session').filter({hasText:'Transfer HTTP fixture'});
    await expect(entry).toBeVisible();
    await page.evaluate(()=>{
      window.nativeCleanupEvents=[];
      window.addEventListener('bridge-cache-cleared',event=>nativeCleanupEvents.push(event.detail));
      window.CodexBridgeNativeOffline={request:async event=>event.type==='state'?{
        allowPrefetch:false,allowTransfer:true,clearRevision:1,
        tombstones:event.sessionId?{[event.sessionId]:['turn-0']}:{},
      }:null};
      // Match the native bridge's completed auth binding before opening a chat.
      BridgeChatSyncCache.nativeState={clearRevision:1};
      BridgeChatSyncCache.nativeReady=Promise.resolve(BridgeChatSyncCache.nativeState);
    });
    await entry.click();
    await expect.poll(()=>page.evaluate(()=>BridgeChatSyncCache.nativeState?.clearRevision)).toBe(1);
    expect(await page.evaluate(()=>nativeCleanupEvents)).toEqual([]);
    await expect(page.locator('#messages')).toContainText('Answer 89');
    await info.attach('native-opening-state',{contentType:'application/json',body:JSON.stringify(await page.evaluate(()=>({
      status:document.getElementById('status').textContent,rows:chatTimeline.rows.size,
      needsRefill:chatTimeline.sync.needsRefill,removedTurns:chatTimeline.sync.initial?.removedTurns,
    })))});
    await expect(page.locator('#messages')).toContainText('Answer 89');
    await expect(page.locator('#status')).toHaveText(/已连接|历史记录/);
    await page.locator('#chat-details-button').click();
    await expect(page.locator('dialog.session-menu')).toBeVisible();
  }finally{lines.close();server.kill();}
});

for(const name of ['codex','claude','deepseek','zcode','antigravity','legacy-codex','legacy-claude']){
  const legacy=name.startsWith('legacy-'),provider=name.replace(/^legacy-/,'');
  const title=legacy?'normal '+provider+' history opens when the gateway does not advertise sync':
    'production HTTP reuses '+provider+' history from committed IndexedDB records with deterministic cache latency';
  test(title,async({page,context},info)=>{
    const root=path.resolve(__dirname,'../..');
    const args=['-B','tests/browser/session-transfer-http-fixture.py','--provider',provider];
    if(legacy)args.push('--legacy');
    const env={...process.env};
    if(legacy&&env.CMB_LEGACY_GATEWAY_SOURCE)env.CMB_FIXTURE_GATEWAY_SOURCE=env.CMB_LEGACY_GATEWAY_SOURCE;
    const server=spawn(process.platform==='win32'?'python':'python3',args,{cwd:root,env});
    let error='';
    server.stderr.setEncoding('utf8');server.stderr.on('data',chunk=>{error+=chunk;});
    const lines=createInterface({input:server.stdout});
    try{
      const origin=await new Promise((resolve,reject)=>{
        lines.once('line',resolve);
        server.once('error',reject);
        server.once('exit',code=>reject(Error('Gateway fixture exited '+code+' '+error)));
      });
      await context.route('**/*',route=>new URL(route.request().url()).origin===origin?route.continue():route.abort());
      const pages=[];
      page.on('response',response=>{
        const url=new URL(response.url());
        if(url.pathname.endsWith('/sync'))pages.push({mode:url.searchParams.get('mode'),response});
      });
      await page.goto(origin);
      await page.locator('#username').fill('fixture');
      await page.locator('#password').fill('session-transfer-test-only');
      await page.locator('#login-button').click();
      const codex=provider==='codex';
      const listSelector=codex?'#sessions':'.ds-list .sessions';
      const chatSelector=codex?'#messages':'.ds-conversation .ds-messages';
      const entry=page.locator(listSelector+' .session').filter({hasText:'Transfer HTTP fixture'});
      const selectProvider=async()=>{
        if(codex)return;
        await page.waitForFunction(provider=>typeof clientNavigation!=='undefined'&&clientNavigation.managementClients().some(row=>row.id===provider),provider);
        if(!await entry.isVisible())await page.evaluate(provider=>clientNavigation.choose(provider,{activate:false}),provider);
      };
      await selectProvider();
      await expect(entry).toBeVisible();
      const chat=page.locator(chatSelector);
      if(legacy){
        const legacyRoute=response=>new URL(response.url()).pathname.endsWith(codex?'/timeline':'/detail');
        const response=page.waitForResponse(legacyRoute);
        await entry.click();
        expect((await response).status()).toBe(200);
        await expect(chat.locator('.message.assistant').last()).toContainText('Answer 89');
        expect(pages).toHaveLength(0);
        return;
      }
      const loadedSteps=chat.locator('.activity:not(.chat-process-collapsed)');
      const contentTime=()=>page.evaluate(({listSelector,chatSelector})=>{
        const list=document.querySelector(listSelector);
        const messages=document.querySelector(chatSelector);
        let started;
        window.transferContentTime=new Promise(resolve=>{
          list.addEventListener('click',()=>started=performance.now(),{capture:true,once:true});
          const observer=new MutationObserver(()=>{
            if(started!==undefined&&[...messages.querySelectorAll('.message-body')].some(node=>node.textContent==='Answer 89')){
              observer.disconnect();resolve(performance.now()-started);
            }
          });
          observer.observe(messages,{childList:true,subtree:true,characterData:true});
        });
      },{listSelector,chatSelector});
      const payload=async response=>{expect(response.status(),await response.text()).toBe(200);return response.json();};
      async function recent(){
        await contentTime();
        const response=syncResponse(page,'recent');await entry.click();
        await expect(chat.locator('.message.assistant').last()).toContainText('Answer 89');
        return {page:await payload(await response),visibleMs:await page.evaluate(()=>window.transferContentTime)};
      }
      async function older(){
        const response=syncResponse(page,'older');await page.locator(codex?'#older':'.ds-history-older').evaluate(button=>button.click());
        await expect(chat.locator('.message.user').first()).toContainText('Question 30');
        return payload(await response);
      }
      async function processPages(){
        const first=syncResponse(page,'process');
        const next=page.waitForResponse(response=>{
          const url=new URL(response.url());
          return url.pathname.endsWith('/sync')&&url.searchParams.get('mode')==='process'&&url.searchParams.has('after');
        });
        await chat.getByRole('button',{name:'已完成',exact:true}).last().click();
        await expect(loadedSteps).toHaveCount(24);
        await expect(chat.getByRole('button',{name:'加载后续过程'})).toHaveCount(0);
        return [await payload(await first),await payload(await next)];
      }
      async function body(){
        const tool=loadedSteps.last();
        await tool.locator('summary').click();
        await expect(tool.locator('.activity-body')).toContainText('Long detail marker');
        await tool.getByRole('button',{name:'继续加载正文'}).click();
        await expect(tool.locator('.activity-body')).toContainText('SECOND CHUNK');
      }
      async function fileActions(){
        const anchor=chat.locator('.message-body a').filter({hasText:'report.md'});
        await expect(anchor).toBeVisible();
        await anchor.click();
        const dialog=page.locator('dialog[open]');
        await expect(dialog.getByRole('button',{name:'定位并打开所在文件夹',exact:true})).toBeEnabled();
        await dialog.getByRole('button',{name:'预览文件',exact:true}).click();
        await expect(page.getByText('Fixture file preview',{exact:true})).toBeVisible();
        await page.evaluate(codex=>(codex?workbench:desktopSessions.workbench).select('chat'),codex);
        await anchor.click();
        await page.locator('dialog[open]').getByRole('button',{name:'定位并打开所在文件夹',exact:true}).click();
        await expect(page.locator('.wb-locate-highlight').filter({hasText:'report.md'})).toBeVisible();
        await page.evaluate(codex=>(codex?workbench:desktopSessions.workbench).select('chat'),codex);
      }
      async function longBody(){
        const row=chat.locator('.message.assistant').filter({hasText:'Answer 87'});
        await row.getByRole('button',{name:'展开完整内容',exact:true}).click();
        await expect(row.getByRole('button',{name:'继续加载正文',exact:true})).toBeVisible();
        await row.getByRole('button',{name:'继续加载正文',exact:true}).click();
        await expect(row.locator('.message-body a').filter({hasText:'late.md'})).toBeVisible();
        await expect(row.locator('.message-body > .markdown-body')).toHaveCount(1);
      }
      const cold=await recent();
      expect(cold.page.rows.filter(row=>['user','assistant'].includes(row.role))).toHaveLength(20);
      await fileActions();
      const coldOlder=await older();
      expect(coldOlder.rows.filter(row=>['user','assistant'].includes(row.role))).toHaveLength(100);
      const coldProcesses=await processPages();
      expect(coldProcesses.map(result=>result.rows.length)).toEqual([20,4]);
      await body();
      expect(pages.filter(value=>value.mode==='body')).toHaveLength(2);

      await longBody();
      const coldBytes=await mappedBytes(pages);
      // Zero retransmission applies to committed records. Rendering is allowed
      // to finish before the independent persistence lane.
      await page.evaluate(async()=>{await Promise.all(BridgeChatSyncCache.pending.values());});
      await page.goto(origin+'/?round=warm');
      await selectProvider();
      await expect(entry).toBeVisible();
      const saved=await readCommittedSession(page,provider,await entry.getAttribute('data-id'),true);
      expect(saved.recent.some(row=>row.text==='Answer 89')).toBe(true);
      expect(saved.process).toHaveLength(24);
      pages.length=0;
      const watchReply=syncResponse(page,'watch');
      const warm=await recent();
      expect(warm.page.rows).toHaveLength(0);
      for(const [index,effort] of [[87,''],[88,''],[89,' high']]){
        const row=chat.locator('.message.assistant').filter({has:page.locator('.message-body').getByText('Answer '+index,{exact:true})});
        await expect(row.locator('.who')).toHaveText(new RegExp('历史模型 '+index+effort+'$'));
      }
      await fileActions();
      const warmOlder=await older();
      expect(warmOlder.rows).toHaveLength(0);
      const warmProcesses=await processPages();
      expect(warmProcesses.map(result=>result.rows.length)).toEqual([0,0]);
      await body();
      await longBody();
      expect(pages.filter(value=>value.mode==='body')).toHaveLength(0);
      const watch=await payload(await watchReply);
      expect(watch.waitSupported).toBe(true);
      expect(watch.rows).toBeUndefined();
      await expect.poll(()=>page.evaluate(codex=>{
        const timeline=codex?chatTimeline:desktopSessions.syncTimeline;
        return !!(timeline?.receivedFreshPage&&timeline.syncEnabled);
      },codex)).toBe(true);
      const warmBytes=await mappedBytes(pages);
      await info.attach('session-transfer-evidence',{contentType:'application/json',body:JSON.stringify({
        provider,coldBytes,warmBytes,coldVisibleMs:cold.visibleMs,warmVisibleMs:warm.visibleMs,
        recent:{cold:cold.page.rows.length,warm:warm.page.rows.length},
        older:{cold:coldOlder.rows.length,warm:warmOlder.rows.length},
        process:{cold:coldProcesses.map(result=>result.rows.length),warm:warmProcesses.map(result=>result.rows.length)},
        bodyRequests:{cold:4,warm:0},
      })});
      await page.evaluate(async()=>{
        await Promise.all(BridgeChatSyncCache.pending.values());
        BridgeChatSyncCache.disk=window.transferPersistedDisk;delete window.transferPersistedDisk;
      });
      if(codex){
        await page.evaluate(async()=>{
          const [group]=await BridgeChatSyncCache.inventory();
          await BridgeChatSyncCache.erase(new Set([group.id]),null);
        });
        await expect(chat.locator('.message.assistant')).toHaveCount(0);
        await expect(page.locator('#newer')).toBeVisible();
        await page.locator('#newer').click();
        await expect(chat.locator('.message.assistant').last()).toContainText('Answer 89');
      }
    }finally{
      lines.close();
      server.kill('SIGINT');
      if(server.exitCode===null&&server.signalCode===null)await new Promise(resolve=>server.once('exit',resolve));
    }
  });
}

test('device usage is requested on open and signed-out users can clear selected local turns',async({page,context})=>{
  const root=path.resolve(__dirname,'../..');
  const server=spawn(process.platform==='win32'?'python':'python3',['-B','tests/browser/session-transfer-http-fixture.py'],{cwd:root});
  const lines=createInterface({input:server.stdout});
  try{
    const origin=await new Promise((resolve,reject)=>{lines.once('line',resolve);server.once('error',reject);server.once('exit',c=>reject(Error('fixture exit '+c)));});
    await context.route('**/*',route=>new URL(route.request().url()).origin===origin?route.continue():route.abort());
    let summaries=0;page.on('request',request=>{if(new URL(request.url()).pathname==='/api/session-transfers')summaries++;});
    await page.goto(origin);
    await page.locator('#username').fill('fixture');await page.locator('#password').fill('session-transfer-test-only');
    await page.locator('#login-button').click();
    await page.locator('#sessions .session').filter({hasText:'Transfer HTTP fixture'}).first().click();
    await expect(page.locator('#messages')).toContainText('Answer 89');
    expect(summaries).toBe(0);
    await page.locator('[data-open-appearance]:visible').first().click();
    await page.locator('#transfer-usage').click();
    await expect(page.locator('#transfer-usage-dialog [data-usage]')).toContainText(/\d[\d,.]* [KMGT]?B/);
    expect(summaries).toBe(1);
    await page.locator('[data-close="transfer-usage-dialog"]').click();
    const logout=page.waitForResponse(response=>new URL(response.url()).pathname==='/api/logout');
    await page.locator('#transfer-logout').click();
    expect((await logout).status()).toBe(200);
    await expect(page.locator('#app')).toBeHidden();await page.locator('#logged-out-cache').click();
    const dialog=page.locator('#transfer-clear-dialog');
    await expect(dialog).toBeVisible();await expect(dialog.locator('[data-confirm]')).toBeDisabled();
    await dialog.locator('input').first().check();
    const all=dialog.getByLabel(/全部日期|All dates/);await expect(all).not.toBeChecked();await all.check();
    await dialog.locator('[data-confirm]').click();
    await expect(dialog.locator('[data-clear]')).toContainText(/全部日期|All dates/);
    await dialog.locator('[data-confirm]').click();
    await expect(dialog).not.toBeVisible();await page.locator('#logged-out-cache').click();
    await expect(dialog.locator('[data-clear]')).toContainText(/暂无|No turns/);
    expect(summaries).toBe(1);
  }finally{lines.close();server.kill();}
});

test('the foreground session watches external rollout turns without watching other sessions',async({page,context},info)=>{
  const root=path.resolve(__dirname,'../..');
  const {appendFile}=require('node:fs/promises'),{pathToFileURL}=require('node:url');
  const server=spawn(process.platform==='win32'?'python':'python3',['-B','tests/browser/session-transfer-http-fixture.py','--live-watch'],{cwd:root});
  const lines=createInterface({input:server.stdout});
  const main='12345678-1234-4234-9234-123456789012',other='22345678-1234-4234-9234-123456789012';
  try{
    const [origin,source]=await new Promise((resolve,reject)=>{
      const output=[];
      lines.on('line',line=>{output.push(line);if(output.length===2)resolve([output[0],JSON.parse(output[1])]);});
      server.once('error',reject);server.once('exit',code=>reject(Error('Fixture exited '+code)));
    });
    await context.route('**/*',route=>new URL(route.request().url()).origin===origin?route.continue():route.abort());
    const watches=[],aborted=[];
    function watchId(request){
      const url=new URL(request.url());
      return url.searchParams.get('mode')==='watch'&&url.searchParams.get('waitMs')==='8000'
        ?url.pathname.split('/')[3]:null;
    }
    page.on('request',request=>{const id=watchId(request);if(id)watches.push(id);});
    page.on('requestfailed',request=>{const id=watchId(request);if(id)aborted.push(id);});
    const listening=id=>page.waitForRequest(request=>watchId(request)===id,{timeout:10000});
    async function append(file,...records){await appendFile(file,records.map(record=>JSON.stringify(record)+'\n').join(''));}
    const event=(type,turn_id)=>({type:'event_msg',payload:{type,turn_id}});
    const message=(role,text)=>({type:'response_item',payload:{type:'message',role,content:[{type:'text',text}]}});
    await page.goto(origin);
    await page.locator('#username').fill('fixture');await page.locator('#password').fill('session-transfer-test-only');
    await page.locator('#login-button').click();
    const session=id=>page.locator('#sessions .session[data-id="'+id+'"]');
    const firstWatch=listening(main);await session(main).click();await firstWatch;
    await expect(page.locator('#messages')).toContainText('Answer 89');
    expect(watches.every(id=>id===main)).toBe(true);
    const samples=[];
    for(let index=1;index<=3;index++){
      const label='Outside question '+index,asset='outside image '+index,turn='outside-'+index;
      // This write happens in the Node test process, outside the browser or gateway.
      const start=Date.now();
      await append(source.rollout,event('task_started',turn),message('user',label),
        message('assistant','Outside answer '+index+'\n\n!['+asset+']('+pathToFileURL(source.image).href+')'));
      const at=await page.waitForFunction(({label,asset})=>{
        const messages=document.getElementById('messages');
        const image=messages.querySelector('img[alt="'+asset+'"]');
        const question=[...messages.querySelectorAll('.message.user')].find(node=>node.textContent.includes(label));
        const viewport=document.getElementById('timeline').getBoundingClientRect();
        const visible=node=>{
          const rect=node?.getBoundingClientRect();
          return rect?.width&&rect.height&&rect.bottom>viewport.top&&rect.top<viewport.bottom;
        };
        return visible(question)&&visible(image)&&image.naturalWidth&&
          document.getElementById('status').textContent==='运行中'&&Date.now();
      },{label,asset}).then(value=>value.jsonValue());
      samples.push(at-start);
      expect(at-start).toBeLessThan(1800);
      const nextWatch=listening(main);
      await append(source.rollout,event('task_complete',turn));
      await expect(page.locator('#status')).toHaveText('已连接');
      await nextWatch;
    }
    expect(watches.every(id=>id===main)).toBe(true);
    if(!await session(other).isVisible())await page.locator('#back').click();
    const secondWatch=listening(other);await session(other).click();await secondWatch;
    await expect.poll(()=>aborted.includes(main)).toBe(true);
    await expect(page.locator('#messages')).toContainText('Other session answer');
    const offset=watches.length;
    await append(source.rollout,event('task_started','unselected'),message('user','Unselected session update'));
    await page.evaluate(()=>{
      window.BridgeChatForeground=false;
      window.dispatchEvent(new CustomEvent('bridge-chat-foreground',{detail:{foreground:false}}));
    });
    await expect.poll(()=>aborted.includes(other)).toBe(true);
    await append(source.other,event('task_started','paused'),message('user','Paused other update'));
    await page.waitForTimeout(450);
    expect(watches.slice(offset)).toEqual([]);
    await expect(page.locator('#messages')).not.toContainText('Paused other update');
    await page.evaluate(()=>{
      window.BridgeChatForeground=true;
      window.dispatchEvent(new CustomEvent('bridge-chat-foreground',{detail:{foreground:true}}));
    });
    await expect(page.locator('#messages')).toContainText('Paused other update');
    await expect(page.locator('#messages')).not.toContainText('Unselected session update');
    expect(watches.slice(offset).every(id=>id===other)).toBe(true);

    // Reload during a pending watch. Browser navigation cancels the old fetch,
    // but this is still the same authenticated tab and selected session.
    await append(source.other,event('task_complete','paused'));
    await expect(page.locator('#status')).toHaveText('已连接');
    await expect.poll(()=>page.evaluate(()=>Boolean(chatTimeline.sync?.watch&&!chatTimeline.sync.watch.signal.aborted))).toBe(true);
    await page.evaluate(async()=>{await BridgeChatSyncCache.pending.get(chatTimeline.syncTarget.scope);});
    const epoch=await page.evaluate(()=>BridgeChatSyncCache.epoch);
    await page.goto(origin+'/?phase=watch-reload');
    await page.waitForFunction(()=>!!window.BridgeChatSyncCache?.owner);
    const saved=await readCommittedSession(page,'codex',other);
    expect(saved.recent.some(row=>row.text==='Other session answer')).toBe(true);
    expect(await page.evaluate(()=>BridgeChatSyncCache.epoch)).toBe(epoch);
    let release;
    const hold=new Promise(resolve=>{release=resolve;});
    const recent=url=>url.pathname.endsWith('/'+other+'/sync')&&url.searchParams.get('mode')==='recent';
    await page.route(recent,async route=>{await hold;await route.continue();});
    try{
      await session(other).click();
      await expect(page.locator('#messages')).toContainText('Other session answer');
      expect(await page.evaluate(()=>chatTimeline.cached)).toBe(true);
      const attachment=await page.evaluate(()=>{
        const row=chatTimeline.renderRow({key:'cached-image',role:'user',text:'',attachments:[
          {type:'localImage',desktopId:'a'.repeat(64),name:'cached.png'}
        ]});
        return {label:row.querySelector('[data-image-preview]')?.textContent,images:row.querySelectorAll('img').length};
      });
      expect(attachment).toEqual({label:'cached.png',images:0});
      release();
      await expect.poll(()=>page.evaluate(()=>chatTimeline?.receivedFreshPage)).toBe(true);
    }finally{
      release();
      await page.unroute(recent);
    }
    await page.evaluate(()=>window.dispatchEvent(new Event('offline')));
    // The cache invalidates offline access and the hot reader starts a separate
    // validation generation before retaining the current screen.
    expect(await page.evaluate(()=>BridgeChatSyncCache.epoch)).toBe(epoch+2);
    expect(await page.evaluate(()=>OfflineChatReader.enabled)).toBe(true);

    await info.attach('external-write-to-readable-ms',{contentType:'application/json',body:JSON.stringify(samples)});
  }finally{
    lines.close();
    server.kill('SIGINT');
    if(server.exitCode===null&&server.signalCode===null)await new Promise(resolve=>server.once('exit',resolve));
  }
});

test('a reopened running turn reuses 100 committed steps with deterministic cache latency across a larger gap',async({page,context},info)=>{
  const root=path.resolve(__dirname,'../..'),{appendFile}=require('node:fs/promises');
  const server=spawn(process.platform==='win32'?'python':'python3',['-B','tests/browser/session-transfer-http-fixture.py','--live-watch'],{cwd:root});
  const lines=createInterface({input:server.stdout}),payloads=[];
  const step=number=>'Step '+String(number).padStart(3,'0');
  const steps=(first,last)=>Array.from({length:last-first+1},(_,offset)=>step(first+offset));
  const turn='step-transfer';
  const turnStart=Date.now()-3799000,stamp=offset=>new Date(turnStart+offset).toISOString();
  const event=(type,offset,id=turn)=>({type:'event_msg',...(offset===undefined?{}:{timestamp:stamp(offset)}),payload:{type,turn_id:id}});
  const usage=used=>({type:'event_msg',payload:{type:'token_count',info:{last_token_usage:{total_tokens:used},model_context_window:2048}}});
  const message=(role,text,phase,offset)=>({type:'response_item',...(offset===undefined?{}:{timestamp:stamp(offset)}),payload:{type:'message',role,phase,content:[{type:'text',text}]}});

  try{
    const [origin,source]=await new Promise((resolve,reject)=>{
      const output=[];
      lines.on('line',line=>{output.push(line);if(output.length===2)resolve([output[0],JSON.parse(output[1])]);});
      server.once('error',reject);server.once('exit',code=>reject(Error('Fixture exited '+code)));
    });
    const append=records=>appendFile(source.rollout,records.map(record=>JSON.stringify(record)+'\n').join(''));
    const appendSteps=(first,last)=>steps(first,last).map(text=>message('assistant',text,'commentary'));
    await append([event('task_started',0),message('user','Read this process'),usage(512),...appendSteps(1,100)]);
    await context.route('**/*',route=>new URL(route.request().url()).origin===origin?route.continue():route.abort());
    page.on('response',response=>{
      const url=new URL(response.url());
      if(url.pathname.endsWith('/sync')&&['process','body'].includes(url.searchParams.get('mode'))&&response.ok())
        payloads.push(response.json().then(body=>({mode:url.searchParams.get('mode'),url:url.searchParams,body})));
    });
    await page.goto(origin);
    await page.locator('#username').fill('fixture');await page.locator('#password').fill('session-transfer-test-only');
    await page.locator('#login-button').click();
    const entry=title=>page.locator('#sessions .session').filter({hasText:title});
    const open=async title=>{
      const button=entry(title);
      if(!await button.isVisible()&&await page.locator('#back').isVisible())await page.locator('#back').click();
      await button.click();
    };
    const messages=page.locator('#messages');
    const contents=messages.locator('.message-body').filter({hasText:/^Step \d{3}$/});
    await open('Transfer HTTP fixture');
    const transferId=await entry('Transfer HTTP fixture').getAttribute('data-id');
    await expect(contents).toHaveText(steps(1,100));
    const contextBadge=page.locator('#composer .context-usage');
    await expect(contextBadge).toHaveAttribute('aria-label',/25%$/);
    await contextBadge.click({timeout:5000});
    const usageDialog=page.getByRole('dialog',{name:'上下文使用量'});
    await expect(usageDialog).toContainText('25.0%');
    await append([usage(768)]);
    await expect(contextBadge).toHaveAttribute('aria-label',/38%$/);
    await expect(usageDialog).toContainText('37.5%');
    await expect(usageDialog).not.toContainText('连接恢复后更新');
    await usageDialog.getByRole('button',{name:'关闭',exact:true}).click();
    await expect(messages.locator('.sync-process')).toHaveCount(0);
    // Read the actual IndexedDB records, not merely the rendered rows.
    const diskCount=await page.evaluate(async()=>{
      const {scope}=chatTimeline.syncTarget,cache=BridgeChatSyncCache;
      if(!await cache.disk.open())return -1;
      await cache.pending.get(scope);
      const key=[...chatTimeline.sync.processes.values()].at(-1).cursor;
      const manifests=(await cache.disk.list('page',scope)).filter(record=>{
        const [mode,...params]=JSON.parse(record.key);
        return mode==='process'&&Object.fromEntries(params).key===key;
      });
      const keys=[...new Set(manifests.flatMap(record=>record.value.index.map(row=>row.key)))];
      const rows=await cache.disk.get(scope,'row',keys);
      return rows.filter(row=>/^Step \d{3}$/.test(row?.text||'')).length;
    });
    expect(diskCount).toBe(100);
    await open('Other transfer fixture');await expect(page.locator('#messages')).toContainText('Other session answer');
    let offset=payloads.length;
    await append(appendSteps(101,120));
    await page.goto(origin+'/?process=reopen');
    await page.waitForFunction(()=>!!window.BridgeChatSyncCache?.owner);
    const saved=await readCommittedSession(page,'codex',transferId,true);
    expect(saved.process.filter(row=>/^Step \d{3}$/.test(row.text||''))).toHaveLength(100);
    await open('Transfer HTTP fixture');
    await expect(contents).toHaveText(steps(1,120));
    const onePage=await Promise.all(payloads.slice(offset));
    const bodyText=onePage.flatMap(({mode,body})=>mode==='body'?[body.text]:body.rows.map(row=>row.text));
    expect(bodyText).toEqual(steps(101,120));
    expect(onePage).toHaveLength(1);
    expect(onePage[0].url.has('before')).toBe(false);
    expect(onePage[0].body.unchanged).toHaveLength(100);
    const oldBodyBytes=bodyText.filter(text=>steps(1,100).includes(text)).reduce((bytes,text)=>bytes+Buffer.byteLength(text),0);
    expect(oldBodyBytes).toBe(0);

    await open('Other transfer fixture');await expect(page.locator('#messages')).toContainText('Other session answer');
    offset=payloads.length;
    await append(appendSteps(121,165));
    let releaseRecent;
    const holdRecent=new Promise(resolve=>{releaseRecent=resolve;});
    const recent=url=>url.pathname.endsWith('/'+transferId+'/sync')&&url.searchParams.get('mode')==='recent';
    const delayRecent=async route=>{await holdRecent;await route.continue();};
    await page.route(recent,delayRecent,{times:1});
    try{
      await open('Transfer HTTP fixture');
      await expect(contextBadge).toHaveAttribute('aria-label',/38%$/);
      await expect.poll(()=>page.evaluate(()=>chatTimeline.cached)).toBe(true);
      await contextBadge.click({timeout:5000});
      await expect(usageDialog).toContainText('显示最近一次可用的上下文读数。');
      await expect(usageDialog.getByRole('button',{name:'压缩上下文',exact:true})).toBeHidden();
      await usageDialog.getByRole('button',{name:'关闭',exact:true}).click();
      await page.evaluate(()=>{window.reopeningTimeline=chatTimeline;});
      await page.locator('#back').evaluate(button=>button.click());
      await open('Transfer HTTP fixture');
      expect(await page.evaluate(()=>chatTimeline===window.reopeningTimeline)).toBe(true);
      await expect(contextBadge).toHaveAttribute('aria-label',/38%$/);
      await contextBadge.click({timeout:5000});
      await expect(usageDialog).toContainText('显示最近一次可用的上下文读数。');
      await expect(usageDialog.getByRole('button',{name:'压缩上下文',exact:true})).toBeHidden();
      await usageDialog.getByRole('button',{name:'关闭',exact:true}).click();
    }finally{
      releaseRecent();
    }
    await expect(contents).toHaveText(steps(1,165));
    const acrossGap=(await Promise.all(payloads.slice(offset))).flatMap(({mode,body})=>mode==='body'?[body.text]:body.rows.map(row=>row.text));
    expect([...acrossGap].sort()).toEqual(steps(121,165));

    const anchor=contents.getByText(step(35),{exact:true});
    await page.waitForFunction(()=>!chatTimeline.suppress);
    await anchor.evaluate(node=>{
      const viewport=document.getElementById('timeline');
      viewport.scrollTop+=node.getBoundingClientRect().top-viewport.getBoundingClientRect().top-50;
    });
    await expect.poll(()=>page.evaluate(()=>chatTimeline.following)).toBe(false);
    const y=await anchor.evaluate(node=>node.getBoundingClientRect().top);
    const finalText='Final answer after the process';
    const doneLabel='已完成，用时 1 小时 3 分钟 19 秒';
    await page.evaluate(({finalText})=>{
      const messages=document.getElementById('messages');
      window.firstAnswerFrame=null;
      const observer=new MutationObserver(()=>{
        if(!messages.textContent.includes(finalText))return;
        observer.disconnect();
        requestAnimationFrame(()=>{
          const answer=[...messages.querySelectorAll('.message.assistant')].find(node=>node.textContent.includes(finalText));
          const samples=[...messages.querySelectorAll('.chat-turn-completion')].filter(node=>/^用时 /.test(node.getAttribute('aria-label')||''));
          const control=samples[0],firstStep=control?.nextElementSibling;
          window.firstAnswerFrame={running:control?.__chatTurn?.status==='inProgress',count:samples.length,
            beforeSteps:firstStep?.classList.contains('chat-process-page')&&firstStep.dataset.key.startsWith('process-earlier:'),
            clickable:!!answer&&!!control&&!control.disabled};
          window.answerDurationControl=control;
        });
      });
      observer.observe(messages,{childList:true,subtree:true,characterData:true});
    },{finalText});
    await append([message('assistant',finalText,'final_answer',3797000)]);
    const final=page.locator('#messages .message-body').getByText(finalText,{exact:true});
    await expect(final).toHaveCount(1);
    await expect.poll(()=>page.evaluate(()=>window.firstAnswerFrame)).toEqual({running:true,count:1,beforeSteps:true,clickable:true});
    const control=page.getByRole('button',{name:/^用时 /});
    await expect(control).toBeEnabled();
    await expect(contents.last()).toBeVisible();
    await expect(page.locator('#status')).toHaveText('运行中');
    // A genuine separate rollout event arrives after at least 1.2 seconds.
    await new Promise(resolve=>setTimeout(resolve,1200));
    const completionReceipt=page.waitForResponse(async response=>{
      const url=new URL(response.url());
      return url.pathname.endsWith('/sync')&&url.searchParams.get('mode')==='recent'&&response.ok()&&
        (await response.json()).turns?.some(row=>row.id===turn&&row.status==='completed');
    });
    await append([event('task_complete',3799000)]);
    const completionWire=await (await completionReceipt).json();
    const answerKey=completionWire.index.find(row=>row.turnId===turn&&row.phase==='final_answer').key;
    expect(completionWire.unchanged).toContain(answerKey);
    expect(completionWire.rows.some(row=>row.key===answerKey)).toBe(false);
    const finished=page.getByRole('button',{name:doneLabel,exact:true});
    await expect(finished).toHaveCount(1);
    expect(await finished.evaluate(node=>node===window.answerDurationControl)).toBe(true);
    await expect(contents).toHaveText(steps(1,165));
    const anchorDeltaPx=Math.abs(await anchor.evaluate(node=>node.getBoundingClientRect().top)-y);
    expect(anchorDeltaPx).toBeLessThan(3);
    const jump=page.getByRole('button',{name:'查看最终答复'});
    await expect(jump).toBeInViewport();
    await jump.click();
    await expect(final).toBeInViewport();
    await expect(finished).toHaveAttribute('aria-expanded','false');
    await expect(contents).toHaveCount(0);
    // Durability is asynchronous; zero retransmission applies to committed cache.
    await page.evaluate(async()=>{await BridgeChatSyncCache.pending.get(chatTimeline.syncTarget.scope);});
    offset=payloads.length;
    await finished.click();
    await expect(contents).toHaveText(steps(1,165));
    await expect(messages.getByRole('button',{name:'加载后续过程'})).toHaveCount(0);
    const completed=await Promise.all(payloads.slice(offset));
    expect(completed.flatMap(({body})=>body.rows)).toHaveLength(0);

    const follow='follow-current',followSteps=Array.from({length:18},(_,index)=>'Following step '+(index+1));
    const latest=async()=>{
      const backToLatest=page.getByRole('button',{name:'回到最新',exact:true});
      if(await backToLatest.isVisible())await backToLatest.click();
      else await page.locator('#timeline').evaluate(node=>{node.scrollTop=node.scrollHeight;});
      await expect.poll(()=>page.evaluate(()=>chatTimeline.following)).toBe(true);
    };
    await latest();
    await append([event('task_started',3800000,follow),message('user','Follow this process'),
      ...followSteps.slice(0,9).map(text=>message('assistant',text,'commentary')),
      message('user','Supplement without ending the turn'),
      ...followSteps.slice(9).map(text=>message('assistant',text,'commentary'))]);
    const followContents=messages.locator('.message-body').filter({hasText:/^Following step \d+$/});
    await expect(followContents).toHaveText(followSteps);
    await expect(page.getByRole('button',{name:'回到本轮输入'})).toHaveCount(0);
    await append([message('assistant','Final while following','final_answer',3807000)]);
    const followLabel=page.getByRole('button',{name:/^用时 /});
    await expect(followLabel).toBeInViewport();
    await expect(messages.getByText('Final while following',{exact:true})).toBeInViewport();
    const firstAnswerImage=info.outputPath('22bce-first-answer.png');
    await page.screenshot({path:firstAnswerImage});
    await info.attach('22bce-first-answer',{path:firstAnswerImage,contentType:'image/png'});
    await followLabel.click();
    await expect(followContents.first()).toBeInViewport();
    const openedImage=info.outputPath('22bce-open-process.png');
    await page.screenshot({path:openedImage});
    await info.attach('22bce-open-process',{path:openedImage,contentType:'image/png'});
    await latest();
    await append([event('task_complete',3809000,follow)]);
    const followDone=page.getByRole('button',{name:'已完成，用时 9 秒',exact:true});
    await expect(followDone).toBeInViewport();
    await expect(followDone).toHaveAttribute('aria-expanded','false');
    await followDone.click();
    await expect(followContents).toHaveText(followSteps);
    await latest();

    const stopped='interrupted-turn';
    await append([event('task_started',3810000,stopped),message('user','Stop this process'),
      message('assistant','Last interrupted step','commentary')]);
    const lastStep=messages.getByText('Last interrupted step',{exact:true});
    await expect(lastStep).toBeVisible();
    await append([event('turn_aborted',3811000,stopped)]);
    const stopControl=messages.getByRole('button',{name:'已停止',exact:true}).last();
    await expect(stopControl).toHaveAttribute('aria-expanded','true');
    const outcome=messages.locator('.chat-turn-outcome').last();
    await expect(outcome).toHaveText('已停止');
    await expect(outcome).toBeVisible();
    expect(await outcome.evaluate(node=>node.previousElementSibling?.querySelector('.message-body')?.textContent)).toBe('Last interrupted step');
    await expect(lastStep).toBeVisible();
    await stopControl.click();
    await expect(lastStep).toBeHidden();
    await expect(outcome).toBeHidden();
    await stopControl.click();
    await expect(lastStep).toBeVisible();
    await expect(outcome).toBeVisible();
    await page.evaluate(async()=>{
      await Promise.all(BridgeChatSyncCache.pending.values());
      BridgeChatSyncCache.disk=window.transferPersistedDisk;delete window.transferPersistedDisk;
    });
    await info.attach('process-reopen-receipts',{contentType:'application/json',body:JSON.stringify({
      cachedSteps:diskCount,adjacentNewBodies:bodyText.length,oldBodyBytes,largeGapNewBodies:acrossGap.length,
      anchorDeltaPx
    })});
  }finally{
    lines.close();server.kill('SIGINT');
    if(server.exitCode===null&&server.signalCode===null)await new Promise(resolve=>server.once('exit',resolve));
  }
});
