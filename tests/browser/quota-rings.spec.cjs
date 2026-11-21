'use strict';
const {test,expect}=require('@playwright/test');

test.beforeEach(async({context,baseURL})=>{
  await context.route('**/*',route=>new URL(route.request().url()).origin===baseURL?route.continue():route.abort());
});

test('shared quota rings stay beside the Codex model and open account pool details',async({page,context})=>{
  let reads=0;
  await context.route('**/api/account?**',route=>{reads++;return route.fulfill({contentType:'application/json',body:JSON.stringify({visible:true,accountKey:'fixture-account',updatedAt:1795200000,limits:[{id:'codex',name:'Codex',windows:[{kind:'primary',remainingPercent:66.6,windowDurationMins:300,resetsAt:2000000000},{kind:'secondary',remainingPercent:21,windowDurationMins:10080,resetsAt:2000600000}]}]})});});
  await page.goto('/?case=session-menu');await expect(page.locator('#app')).toBeVisible();
  await page.evaluate(async()=>{
    await BridgeFeatures.load('chat');BridgeI18n.setLanguage('zh');workbench.open('synthetic-quota','local');
    document.getElementById('welcome').hidden=true;document.getElementById('chat').hidden=false;
    document.getElementById('app').classList.add('chat-open');document.getElementById('model-button').disabled=false;
  });
  const rings=page.locator('#model-button .model-quota-indicator');
  await expect(rings).toBeVisible();await expect(rings.locator('.quota-pie-ring')).toHaveCount(2);
  await expect(rings).toHaveAttribute('title',/66.6%.*恢复时间/s);
  await page.locator('#model-button').click();const dialog=page.locator('#model-dialog');await expect(dialog).toBeVisible();
  const pool=dialog.locator('.quota-pool');await pool.locator('summary').click();await expect(pool.locator('.quota-details')).toContainText('已用 33.4%');
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
    const catalog={models:[{id:'first',name:'双窗口模型',efforts:['low','high'],quotaWindows:windows},{id:'second',name:'仅每周额度',efforts:['low','high'],quotaWindows:[windows[1]]}],usageSummary:{tier:'Fixture Plan',checkedAt:1795200000,groups:[{name:'共享额度池',models:['双窗口模型'],windows}]},capabilities:{models:true,effort:true}};
    const session={id:'q',title:'额度显示',model:'first',modelDisplayName:'双窗口模型',status:'idle',quotaWindows:windows,capabilities:{models:true,effort:true,send:true}};
    window.quotaNative=new DesktopSessionsView({root,select:document.createElement('select'),notify(){},onSelect(){},csrf:()=>'',onUnauthorized(){},request:async url=>url.includes('/detail')?{session,messages:[]}:url.includes('/catalog')?catalog:url.includes('/workspace')?{entries:[],total:0,project:'/fixture'}:{sessions:[session]}});
    await quotaNative.choose('antigravity');await quotaNative.open('q');clearTimeout(quotaNative.timer);
  });
  await expect(page.locator('#quota-native .model-quota-indicator')).toBeVisible();
  await page.locator('#quota-native .ds-model').click();const dialog=page.locator('.ds-model-dialog');await expect(dialog).toBeVisible();
  await expect(dialog.locator('.model-choice').nth(1).locator('.quota-chip.is-placeholder')).toHaveCount(1);
  const columns=await dialog.locator('.model-choice').evaluateAll(rows=>rows.map(row=>row.querySelectorAll('.quota-chip')[1].getBoundingClientRect().left));expect(Math.abs(columns[0]-columns[1])).toBeLessThan(1);
  await dialog.locator('.model-choice').nth(1).click();await dialog.locator('.ds-model-refresh').click();await expect(dialog.locator('.model-choice').nth(1).locator('input')).toBeChecked();
  const pool=dialog.locator('.quota-pool');await pool.locator('summary').click();await expect(pool.locator('.quota-details')).toContainText('58.8%');await expect(pool).toContainText('共用模型：双窗口模型');
  expect(await dialog.evaluate(node=>node.scrollWidth<=node.clientWidth)).toBe(true);await page.screenshot({path:'.tmp/issue-pr-implementation-20261121/quota-'+test.info().project.name+'.png'});
});
