'use strict';
const {test,expect}=require('@playwright/test');

test('mobile chat quota rings precede add button and show read-only quota details',async({page,context,baseURL})=>{
  await context.route('**/*',route=>new URL(route.request().url()).origin===baseURL?route.continue():route.abort());
  let reads=0;
  await context.route('**/api/account?**',route=>{
    reads++;
    return route.fulfill({contentType:'application/json',body:JSON.stringify({visible:true,loginType:'chatgpt',updatedAt:1795200000,
      limits:[{id:'codex',windows:[
        {kind:'primary',remainingPercent:66.6,windowDurationMins:300,resetsAt:1795207200},
        {kind:'secondary',remainingPercent:21,windowDurationMins:10080,resetsAt:1795286400}
      ]}]})});
  });
  await page.goto('/?case=session-menu');await expect(page.locator('#app')).toBeVisible();
  await page.evaluate(async()=>{
    await BridgeFeatures.load('chat');BridgeI18n.setLanguage('zh');workbench.open('synthetic-quota','local');
    document.getElementById('welcome').hidden=true;document.getElementById('chat').hidden=false;
    document.getElementById('app').classList.add('chat-open');
  });
  const rings=page.locator('.account-quota-rings');
  if(page.viewportSize().width>720){await expect(rings).toBeHidden();return;}
  await expect(rings).toBeVisible();await expect(rings.locator('.account-quota-percent')).toHaveText(['66.6%','21%']);
  expect(await page.evaluate(()=>{
    const r=document.querySelector('.account-quota-rings').getBoundingClientRect(),
      a=document.querySelector('.wb-add').getBoundingClientRect(),t=document.querySelector('.wb-tabs').getBoundingClientRect();
    return t.right<=r.left&&r.right<=a.left&&r.right<=innerWidth&&document.documentElement.scrollWidth<=innerWidth;
  })).toBe(true);
  await rings.locator('button').first().click();const dialog=page.locator('.account-quota-dialog');
  await expect(dialog).toBeVisible();await expect(dialog).toContainText('已用 33.4%');await expect(dialog).toContainText('恢复时间：');
  const before=reads;await dialog.getByRole('button',{name:'刷新额度',exact:true}).click();await expect.poll(()=>reads).toBeGreaterThan(before);
  await dialog.getByRole('button',{name:'关闭',exact:true}).click();await expect(dialog).not.toBeVisible();
});
