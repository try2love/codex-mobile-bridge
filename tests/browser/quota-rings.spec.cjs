'use strict';
const {test,expect}=require('@playwright/test');

async function checkComposerQuotaSize(composer,label){
  const quota=composer.locator('.remaining-quota'),context=composer.locator('.context-usage');
  await expect(context.locator('.toolbar-copy')).toBeVisible();
  const measure=(node,selectors)=>{
    const {width,height}=getComputedStyle(node.querySelector(selectors.ring));
    return {width,height,font:getComputedStyle(node.querySelector(selectors.label)).fontSize};
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
