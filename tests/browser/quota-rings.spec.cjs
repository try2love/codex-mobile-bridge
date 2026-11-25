'use strict';
const {test,expect}=require('@playwright/test');

async function checkComposerQuotaSize(composer,label){
  const quota=composer.locator('.remaining-quota'),context=composer.locator('.context-usage');
  await expect(context.locator('.toolbar-copy')).toBeVisible();
  const measure=(node,selectors)=>{
    const {width,height}=getComputedStyle(node.querySelector(selectors.ring));
    return {width,height,mask:getComputedStyle(node.querySelector(selectors.ring)).maskImage,font:getComputedStyle(node.querySelector(selectors.label)).fontSize};
  };
  const actual=await quota.evaluate(measure,{ring:'.quota-pie-ring',label:'.quota-window-percent'});
  const expected=await context.evaluate(measure,{ring:'.context-usage-ring',label:'.toolbar-copy'});
  await test.info().attach(label,{contentType:'application/json',body:JSON.stringify({quota:actual,context:expected})});
  expect(actual).toEqual(expected);
}

test.beforeEach(async({context,baseURL})=>{
  await context.route('**/*',route=>new URL(route.request().url()).origin===baseURL?route.continue():route.abort());
});

test('remaining quota is independent from model settings and shares the same account pool',async({page,context})=>{
  let reads=0,percent=66.6,offline=false;
  await context.route('**/api/account?**',route=>{reads++;if(offline)return route.fulfill({status:500,json:{error:'quota fixture offline'}});return route.fulfill({contentType:'application/json',body:JSON.stringify({visible:true,accountKey:'fixture-account',updatedAt:1795200000,limits:[{id:'codex',name:'Codex',windows:[{kind:'primary',remainingPercent:percent,windowDurationMins:300,resetsAt:2000000000},{kind:'secondary',remainingPercent:21,windowDurationMins:10080,resetsAt:2000600000}]}]})});});
  await page.goto('/?case=session-menu');await expect(page.locator('#app')).toBeVisible();
  await page.evaluate(async()=>{
    await BridgeFeatures.load('chat');BridgeI18n.setLanguage('zh');workbench.open('synthetic-quota','local');
    document.getElementById('welcome').hidden=true;document.getElementById('chat').hidden=false;
    document.getElementById('app').classList.add('chat-open');document.getElementById('model-button').disabled=false;
    contextUsageControl.update({id:'synthetic-quota',connected:true,contextUsage:{usedTokens:40,contextWindow:100}});
    composerToolbar.preferences.update({display:{...composerToolbar.display,context:'both'}});composerToolbar.sync();
  });
  const rings=page.locator('#quota-button');
  await expect(rings).toBeVisible();await expect(rings.locator('.quota-pie-ring')).toHaveCount(2);
  const composer=page.locator('#composer');
  await checkComposerQuotaSize(composer,'default-text-size');
  await page.evaluate(()=>{contextUsageControl.update({id:'synthetic-quota',connected:true,contextUsage:{usedTokens:52,contextWindow:100}});composerToolbar.sync();});
  await expect(composer.locator('.context-usage .toolbar-copy')).toHaveText('上下文52%');
  await page.evaluate(()=>{
    const font=document.getElementById('appearance-font');font.value='24';font.dispatchEvent(new Event('input'));
  });
  await expect(page.locator('html')).toHaveCSS('--message-size','24px');
  await checkComposerQuotaSize(composer,'24px-message-text');
  const clipped=await rings.evaluate(node=>{
    const strip=node.closest('.toolbar-bottom-scroll'),percent=node.querySelector('.quota-chip:last-child .quota-window-percent');
    return percent.getBoundingClientRect().right-strip.getBoundingClientRect().right;
  });
  expect(clipped).toBeLessThanOrEqual(1);
  await composer.screenshot({path:test.info().outputPath('composer-quota-size.png')});
  await expect(rings).toHaveAttribute('title',/66.6%.*恢复时间/s);
  await expect(page.locator('#model-button .quota-pie-ring')).toHaveCount(0);
  await rings.click();const onlyQuota=page.locator('.quota-details-dialog');await expect(onlyQuota).toBeVisible();await expect(onlyQuota.locator('.quota-pool')).toHaveCount(1);await expect(onlyQuota.locator('.model-choice')).toHaveCount(0);await expect(page.locator('#model-dialog')).toBeHidden();
  await expect(onlyQuota.locator('.quota-pie-ring').first()).toHaveCSS('width','14px');
  await expect(onlyQuota.locator('.quota-chip').first()).toHaveCSS('font-size',page.viewportSize().width<=420?'10px':'11px');
  const originalTime=await onlyQuota.locator('.quota-updated').textContent();percent=42.5;
  await onlyQuota.getByRole('button',{name:'刷新额度',exact:true}).click();await expect(onlyQuota).toContainText('42.5%');await expect(rings).toContainText('42.5%');
  offline=true;await onlyQuota.getByRole('button',{name:'刷新额度',exact:true}).click();await expect(onlyQuota).toContainText('quota fixture offline');await expect(onlyQuota).toContainText('42.5%');await expect(onlyQuota.locator('.quota-updated')).toHaveText(originalTime);
  offline=false;await onlyQuota.getByRole('button',{name:'刷新额度',exact:true}).click();await expect(onlyQuota.locator('.error')).toBeHidden();
  await onlyQuota.getByRole('button',{name:'关闭',exact:true}).click();
  await page.locator('#model-button').click();const dialog=page.locator('#model-dialog');await expect(dialog).toBeVisible();
  const pool=dialog.locator('.quota-pool');await expect(pool).not.toHaveAttribute('open');await pool.locator('summary').click();await expect(pool.locator('.quota-details')).toContainText('已用 57.5%');
  await expect(pool.locator('.quota-details')).toContainText('恢复时间：');
  const before=reads;await dialog.getByRole('button',{name:'刷新额度',exact:true}).click();await expect.poll(()=>reads).toBeGreaterThan(before);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
});

test('shared quota model columns and pools work for native clients and preserve edited selection',async({page})=>{
  await page.goto('/?case=success');await expect(page.locator('#bootstrap-status')).toBeHidden();await page.waitForFunction(()=>typeof DesktopSessionsView==='function'&&typeof QuotaDisplay!=='undefined');
  await page.evaluate(async()=>{
    await BridgeFeatures.load('chat');BridgeI18n.setLanguage('zh');
    const root=document.createElement('div');root.id='quota-native';root.className='app';root.style.cssText='position:fixed;inset:0;background:var(--page);z-index:10';document.body.append(root);
    const windows=[{remainingPercent:58.8,windowDurationMins:300,resetsAt:2000000000},{remainingPercent:87.5,windowDurationMins:10080,resetsAt:2000600000}];
    const catalog={models:[{id:'first',name:'双窗口模型',efforts:['low','high'],quotaWindows:windows},{id:'second',name:'仅每周额度',efforts:['low','high'],quotaWindows:[windows[1]]}],usageSummary:{tier:'Fixture Plan',credits:'Prompt: 100 / Flow: 200',checkedAt:1795200000,groups:[{name:'共享额度池',models:['双窗口模型'],windows}]},capabilities:{models:true,effort:true}};
    const session={id:'q',title:'额度显示',model:'first',modelDisplayName:'双窗口模型',status:'idle',quotaWindows:windows,capabilities:{models:true,effort:true,send:true}};
    window.quotaNativeCatalog=catalog;window.quotaNativeFail=false;window.quotaNative=new DesktopSessionsView({root,select:document.createElement('select'),notify(){},onSelect(){},csrf:()=>'',onUnauthorized(){},request:async url=>url.includes('/detail')?{session,messages:[]}:url.includes('/catalog')?(()=>{if(window.quotaNativeFail)throw Error('native quota offline');return structuredClone(window.quotaNativeCatalog);})():url.includes('/workspace')?{entries:[],total:0,project:'/fixture'}:{sessions:[session]}});
    await quotaNative.choose('antigravity');await quotaNative.open('q');clearTimeout(quotaNative.timer);
    quotaNative.toolbar.preferences.update({display:{...quotaNative.toolbar.display,context:'both'}});quotaNative.toolbar.sync();
  });
  await expect(page.locator('#quota-native .remaining-quota')).toBeVisible();
  await checkComposerQuotaSize(page.locator('#quota-native .composer'),'desktop-provider-text-size');
  await page.locator('#quota-native .remaining-quota').click();await expect(page.locator('.quota-details-dialog')).toBeVisible();await expect(page.locator('.quota-details-dialog')).toContainText('共享额度池');await expect(page.locator('.quota-details-dialog .model-choice')).toHaveCount(0);
  await page.evaluate(()=>{quotaNativeCatalog.usageSummary.groups[0].windows[0].remainingPercent=44;});
  await page.locator('.quota-details-dialog').getByRole('button',{name:'刷新额度',exact:true}).click();await expect(page.locator('.quota-details-dialog')).toContainText('44%');await expect(page.locator('#quota-native .remaining-quota')).toContainText('44%');
  await page.evaluate(()=>{quotaNativeFail=true;});await page.locator('.quota-details-dialog').getByRole('button',{name:'刷新额度',exact:true}).click();await expect(page.locator('.quota-details-dialog')).toContainText('native quota offline');await expect(page.locator('.quota-details-dialog')).toContainText('44%');
  await page.evaluate(()=>{quotaNativeFail=false;});await page.locator('.quota-details-dialog').getByRole('button',{name:'关闭',exact:true}).click();
  await page.locator('#quota-native .ds-model').click();const dialog=page.locator('.ds-model-dialog');await expect(dialog).toBeVisible();
  await expect(dialog.locator('.model-choice').nth(1).locator('.quota-chip.is-placeholder')).toHaveCount(1);
  const columns=await dialog.locator('.model-choice').evaluateAll(rows=>rows.map(row=>row.querySelectorAll('.quota-chip')[1].getBoundingClientRect().left));expect(Math.abs(columns[0]-columns[1])).toBeLessThan(1);
  await dialog.locator('.model-choice').nth(1).click();await dialog.locator('.ds-model-refresh').click();await expect(dialog.locator('.model-choice').nth(1).locator('input')).toBeChecked();
  await expect(dialog.locator('.quota-credits')).toHaveCount(0);
  const pool=dialog.locator('.quota-pool');await pool.locator('summary').click();await expect(pool.locator('.quota-details')).toContainText('44%');await expect(pool).toContainText('共用模型：双窗口模型');
  expect(await dialog.evaluate(node=>node.scrollWidth<=node.clientWidth)).toBe(true);await page.screenshot({path:'.tmp/remaining-quota-20261122/quota-'+test.info().project.name+'.png'});
});


test('remaining quota customization moves hides and restores the independent control',async({page,context})=>{
 await context.route('**/api/account?**',route=>route.fulfill({json:{visible:true,accountKey:'custom-quota',updatedAt:1795200000,limits:[{id:'codex',windows:[{windowDurationMins:300,remainingPercent:75}]}]}}));
 await page.goto('/?case=session-menu');await expect(page.locator('#app')).toBeVisible();
 await page.evaluate(async()=>{await BridgeFeatures.load('chat');workbench.open('custom-quota','local');document.getElementById('app').classList.add('chat-open');document.getElementById('welcome').hidden=true;document.getElementById('chat').hidden=false;document.getElementById('message').value='preserve quota draft';});
 const quota=page.locator('#quota-button');await expect(quota).toBeVisible();
 const edit=async()=>{await page.locator('#composer .toolbar-more').click();await page.locator('.toolbar-menu[open] .toolbar-edit-button').click();await page.locator('.toolbar-editor [data-drag-tool=quota]').click();};
 await edit();await page.locator('.toolbar-editor [data-move-zone=topRight]').click();await page.locator('.toolbar-editor [data-display-choice=icon]').click();await page.locator('.toolbar-editor-close').click();
 await expect(page.locator('#composer [data-toolbar-zone=topRight] #quota-button')).toBeVisible();await expect(quota.locator('.quota-pie-ring')).toBeVisible();await expect(quota.locator('.quota-pie-ring')).toHaveCSS('width','17px');await expect(quota.locator('.quota-window-percent')).toBeHidden();
 await edit();await page.locator('.toolbar-editor [data-display-choice=text]').click();await page.locator('.toolbar-editor-close').click();await expect(quota.locator('.quota-window-percent')).toBeVisible();await expect(quota.locator('.quota-window-percent')).toHaveCSS('font-size','11px');await expect(quota.locator('.quota-pie-ring')).toBeHidden();
 await edit();await page.locator('.toolbar-editor [data-display-choice=hidden]').click();await page.locator('.toolbar-editor-close').click();await expect(quota).toBeHidden();
 await edit();await page.locator('.toolbar-editor [data-move-zone=bottomRight]').click();await page.locator('.toolbar-editor-close').click();await expect(quota).toBeVisible();expect(await page.evaluate(()=>composerToolbar.display.quota)).toBe('both');
 await expect(page.locator('#message')).toHaveValue('preserve quota draft');expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
 await quota.click();await expect(page.locator('.quota-details-dialog')).toBeVisible();await page.evaluate(()=>codexQuota.clear());await expect(page.locator('.quota-details-dialog')).toBeHidden();
 await page.reload();await page.waitForFunction(()=>document.getElementById('bootstrap-status')?.hidden);expect(await page.evaluate(()=>composerToolbar.layout.bottomRight.includes('quota'))).toBe(true);
});

test('context dialog updates in place as readings and available operations change',async({page,context})=>{
  await page.goto('/?case=session-menu');
  await expect(page.locator('#app')).toBeVisible();
  await page.evaluate(()=>{
    BridgeI18n.setLanguage('zh');
    contextUsageControl.update({id:'context-fixture',scope:'host-a',preview:true,
      contextUsage:{usedTokens:20,contextWindow:100,cached:true}});
    contextUsageControl.open();
  });
  const dialog=page.getByRole('dialog',{name:'上下文使用量'});
  const close=dialog.getByRole('button',{name:'关闭',exact:true});
  const compact=dialog.getByRole('button',{name:'压缩上下文',exact:true});
  await expect(dialog).toContainText('20.0%');
  await expect(dialog).toContainText('显示最近一次可用的上下文读数。');
  await expect(compact).toBeHidden();
  await close.focus();

  await page.evaluate(()=>{
    window.contextFixtureDialog=contextUsageControl.dialog;
    contextUsageControl.update({id:'context-fixture',scope:'host-a',account:'account-a',connected:true,
      contextUsage:{usedTokens:35,contextWindow:100,cached:true},capabilities:{compact:true}});
  });
  await expect(dialog).toContainText('35.0%');
  await expect(dialog).toContainText('显示最近一次可用的上下文读数。');
  await expect(dialog).not.toContainText('连接恢复后更新');
  await expect(compact).toBeEnabled();
  await expect(close).toBeFocused();
  expect(await page.evaluate(()=>window.contextFixtureDialog===contextUsageControl.dialog)).toBe(true);

  await context.setOffline(true);
  await expect(dialog).toContainText('连接恢复后更新');
  await expect(compact).toBeDisabled();
  await context.setOffline(false);
  await expect(compact).toBeEnabled();
  await compact.focus();
  await page.evaluate(()=>{
    contextUsageControl.update({id:'context-fixture',scope:'host-a',account:'account-a',connected:true,
      contextUsageStatus:'unsupported',lastPromptContextUsage:{usedTokens:42}});
  });
  await expect(dialog).toContainText('当前客户端或会话模式未提供上下文用量接口。');
  await expect(dialog).toContainText('最近一次模型输入 · 42');
  await expect(dialog).not.toContainText('使用占比');
  await expect(compact).toBeHidden();
  await expect(close).toBeFocused();

  await page.evaluate(()=>contextUsageControl.update({id:'context-fixture',scope:'host-a',account:'account-b',connected:true}));
  await expect(dialog).toBeHidden();
});

for(const provider of ['codex','antigravity'])test(`offline composer opens read-only snapshots for ${provider}`,async({page,context})=>{
  await page.goto('/?case=session-menu');
  await expect(page.locator('#app')).toBeVisible();
  const network=[];
  await context.route('**/api/**',route=>{
    const url=new URL(route.request().url());
    if(/\/(account|catalog|compact|settings)$/.test(url.pathname))network.push(url.pathname);
    return route.abort();
  });
  await page.evaluate(async provider=>{
    await BridgeFeatures.load('chat');
    BridgeI18n.setLanguage('zh');
    const stamp=1795200000000;
    const saved={account:'fixture-account',context:{
      usage:{syncedAt:stamp,contextUsage:{usedTokens:3200,contextWindow:10000}},
      models:{syncedAt:stamp,rows:[{id:'saved',name:'本机同步模型'}]},
      quota:{syncedAt:stamp,value:{groups:[{name:'本机额度池',windows:[{remainingPercent:62,windowDurationMins:300}]}]}},
    }};
    const matches=route=>route?.provider===provider&&route.host==='local'&&route.session==='offline-catalog';
    // Codex enters hot with resident data. The desktop view exercises local storage hydration after opening.
    let hydrated=provider==='codex';
    window.BridgeOfflineComposer={peek:route=>matches(route)&&hydrated?saved:null,async get(route){
      if(!matches(route))return null;
      if(!hydrated){hydrated=true;queueMicrotask(()=>window.dispatchEvent(new CustomEvent('bridge-offline-composer-change',{detail:{route}})));}
      return saved;
    }};
    OfflineChatReader.enableHot(async action=>action==='list'?{sessions:[]}:Promise.reject(Error('Unexpected archive operation')));
    if(provider==='codex'){
      currentId='offline-catalog';currentHost='local';
      workbench.open(currentId,currentHost);
      $('welcome').hidden=true;$('chat').hidden=false;$('app').classList.add('chat-open');
      state={id:currentId,access:{source:'history'},readOnly:true};
      document.dispatchEvent(new CustomEvent('bridge-hot-offline',{detail:{active:true}}));
      syncCodexOfflineComposer();
      composerToolbar.sync();
    }else{
      const root=document.createElement('div');root.id='offline-desktop';root.className='app';root.style.cssText='position:fixed;inset:0;z-index:10';document.body.append(root);
      const view=window.offlineDesktop=new DesktopSessionsView({root,select:document.createElement('select'),notify(){},onSelect(){},csrf:()=>'',onUnauthorized(){},request:async url=>{throw Error('Unexpected desktop network: '+url);}});
      view.provider=provider;view.container.hidden=false;root.classList.add('desktop-mode');
      view.sid='offline-catalog';view.showSelection(true);
      view.applyDetail({connected:false,readOnly:true,session:{id:view.sid,title:'本机聊天',readOnly:true},messages:[]});
    }
  },provider);
  const composer=page.locator(provider==='codex'?'#composer':'#offline-desktop .composer');
  for(const [selector,name,text] of [
    ['.context-usage','上下文使用量','32.0%'],
    [provider==='codex'?'#model-button':'.ds-model','可用模型','本机同步模型'],
    ['.remaining-quota','剩余额度','本机额度池'],
  ]){
    const trigger=composer.locator(selector);
    // The toolbar is customizable. Open from its current zone or the same tools panel.
    if(!await trigger.isVisible()){
      await composer.getByRole('button',{name:'工具面板'}).click();
      await page.getByRole('dialog',{name:'工具面板'}).getByRole('button',{name:new RegExp(name==='上下文使用量'?'上下文':name==='可用模型'?'模型':name)}).click();
    }else await trigger.click();
    const dialog=page.getByRole('dialog',{name,exact:true});
    await expect(dialog).toContainText(text);
    await expect(dialog).toContainText('最后同步');
    await expect(dialog.getByRole('button',{name:/刷新|应用|压缩/})).toHaveCount(0);
    await dialog.getByRole('button',{name:'关闭',exact:true}).click();
  }
  await page.evaluate(provider=>{
    if(provider==='codex'){currentId='other-session';$('model-button').click();}
    else {offlineDesktop.sid='other-session';void offlineDesktop.models();}
  },provider);
  const empty=page.getByRole('dialog',{name:'可用模型',exact:true});
  await expect(empty).toContainText('本机暂无记录');
  await expect(empty).not.toContainText('本机同步模型');
  expect(network).toEqual([]);
});

test('cold offline composer loads persisted readings through the native storage port',async({page,context})=>{
  const session='offline-persisted',archive=JSON.stringify(['codex','local',session]);
  const records=new Map(),calls=[],network=[];
  await page.exposeFunction('offlineReadingHost',request=>{
    const action=request.type||request.action;
    calls.push(action);
    const saved=records.get(request.sessionId)||{revision:0,value:null};
    if(action==='composerRead')return saved;
    if(action==='composerWrite'){
      if(request.revision!==saved.revision)return {...saved,applied:false};
      const row={revision:saved.revision+1,value:request.value};
      records.set(request.sessionId,row);return {...row,applied:true};
    }
    if(action==='composerPending')return {sessions:[]};
    if(action==='list')return {sessions:[{id:archive,title:'本机持久化会话',syncedAt:1795200000000}],lang:'zh',composerWritable:false};
    if(action==='session')return {id:archive,title:'本机持久化会话',turns:[],before:null,gaps:false};
    return {error:'Unexpected host operation '+action};
  });
  await page.addInitScript(()=>{
    window.OfflineChatHost={postMessage(raw){
      const message=JSON.parse(raw);
      void window.offlineReadingHost(message).then(value=>OfflineChatReader.receive(message.id,value));
    }};
  });
  await page.goto('/?case=session-menu');
  await expect(page.locator('#app')).toBeVisible();
  await page.evaluate(async session=>{
    window.CodexBridgeNativeOffline={request:event=>window.offlineReadingHost(event)};
    BridgeOfflineComposer.bind({authenticated:true,draftIdentity:'fixture-snapshot',sessionSync:{version:1,gatewayId:'a'.repeat(32),identity:'b'.repeat(64)}},
      async()=>{throw Error('Unexpected network request');});
    const account='native-account',route={provider:'codex',host:'local',session},syncedAt=1795200000000;
    await BridgeOfflineComposer.observe(route,{gateway:'fixture-snapshot',account,canSend:false});
    await BridgeOfflineComposer.saveContext(route,{
      usage:{syncedAt,contextUsage:{usedTokens:22,contextWindow:100}},
      models:{syncedAt,rows:[{id:'persisted',name:'重启后读取的模型'}]},
      quota:{syncedAt,value:{groups:[{name:'重启后读取的额度',windows:[{remainingPercent:64,windowDurationMins:300}]}]}},
    },account);
  },session);
  expect(records.get(archive).value).toMatchObject({account:'native-account',canSend:false});
  await context.route(url=>url.searchParams.get('cold-composer')==='1',async route=>{
    const reply=await route.fetch();
    await route.fulfill({response:reply,body:(await reply.text()).replace('<html ','<html data-bridge-offline ')});
  });
  await context.route('**/api/**',route=>{network.push(new URL(route.request().url()).pathname);return route.abort();});
  await page.goto('/?case=session-menu&cold-composer=1');
  await expect(page.locator('#app')).toBeVisible();
  expect(await page.evaluate(()=>OfflineChatReader.cold)).toBe(true);
  expect(await page.evaluate(session=>BridgeOfflineComposer.peek({provider:'codex',host:'local',session}),session)).toBeNull();
  await page.locator('#sessions .session').filter({hasText:'本机持久化会话'}).click();
  const composer=page.locator('#composer');
  const usage=composer.locator('.context-usage');
  await expect(usage).toHaveAttribute('aria-label',/22%$/);
  await usage.click({timeout:5000});
  const dialog=page.getByRole('dialog',{name:'上下文使用量',exact:true});
  await expect(dialog).toContainText('22.0%');
  await expect(dialog).toContainText('最后同步');
  const saved=records.get(archive);
  records.set(archive,{revision:saved.revision+1,value:{...saved.value,context:{...saved.value.context,
    usage:{syncedAt:1795200001000,contextUsage:{usedTokens:34,contextWindow:100}}}}});
  await page.evaluate(session=>BridgeOfflineComposer.get({provider:'codex',host:'local',session}),session);
  await expect(dialog).toContainText('34.0%');
  await expect(dialog.getByRole('button',{name:'压缩上下文',exact:true})).toHaveCount(0);
  await dialog.getByRole('button',{name:'关闭',exact:true}).click();
  for(const [trigger,name,text] of [
    ['#model-button','可用模型','重启后读取的模型'],
    ['.remaining-quota','剩余额度','重启后读取的额度'],
  ]){
    const button=composer.locator(trigger);
    if(!await button.isVisible()){
      await composer.getByRole('button',{name:'工具面板'}).click();
      await page.getByRole('dialog',{name:'工具面板'}).getByRole('button',{name:new RegExp(name==='可用模型'?'模型':name)}).click();
    }else await button.click();
    const details=page.getByRole('dialog',{name,exact:true});
    await expect(details).toContainText(text);
    await expect(details).toContainText('最后同步');
    await expect(details.getByRole('button',{name:/刷新|应用/})).toHaveCount(0);
    await details.getByRole('button',{name:'关闭',exact:true}).click();
  }
  expect(calls.filter(action=>action==='composerWrite')).toHaveLength(2);
  expect(network).toEqual([]);

  // Switch accounts through the existing account panel before any new session data confirms the new owner.
  await context.unroute('**/api/**');
  await page.goto('/?case=session-menu');
  await expect(page.locator('#app')).toBeVisible();
  await page.evaluate(async session=>{
    window.CodexBridgeNativeOffline={request:event=>window.offlineReadingHost(event)};
    const route={provider:'codex',host:'local',session};
    BridgeOfflineComposer.bind({authenticated:true,draftIdentity:'fixture-snapshot',sessionSync:{version:1,gatewayId:'a'.repeat(32),identity:'b'.repeat(64)}},
      async()=>{throw Error('Unexpected network request');});
    if((await BridgeOfflineComposer.get(route))?.account!=='native-account')throw Error('Prior account not found');
    accountsPanel.accept({accounts:[],activeId:'native-account'});
    accountsPanel.accept({accounts:[],activeId:'another-account'});
    if((await BridgeOfflineComposer.get(route))?.context?.usage)throw Error('Prior account reading is visible after account switch');
  },session);
  await context.route('**/api/**',route=>{network.push(new URL(route.request().url()).pathname);return route.abort();});
  await page.goto('/?case=session-menu&cold-composer=1');
  await page.locator('#sessions .session').filter({hasText:'本机持久化会话'}).click();
  await expect(usage).toHaveAttribute('aria-label',/暂未提供/);
  await usage.click({timeout:5000});
  await expect(dialog).toContainText('本机暂无记录');
  await expect(dialog).not.toContainText('34.0%');
  expect(network).toEqual([]);
});
