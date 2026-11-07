'use strict';
const {test,expect}=require('@playwright/test');
const {readFileSync}=require('node:fs');
const path=require('node:path');

test('native attached chats use standard identity, navigation and lifecycle',async({page,context,baseURL},testInfo)=>{
 await context.route('**/*',route=>new URL(route.request().url()).origin===baseURL?route.continue():route.abort());
 await page.goto('/?case=success');
 await page.waitForFunction(()=>document.getElementById('bootstrap-status')?.hidden);
 await page.evaluate(async()=>{
  await BridgeFeatures.load('chat');
  document.getElementById('app').hidden=false;document.getElementById('chat').hidden=false;
  document.getElementById('login').hidden=true;document.getElementById('sessions').hidden=true;
  document.getElementById('app').classList.add('chat-open');document.getElementById('welcome').hidden=true;
  document.getElementById('chat-title').textContent='原生父聊天';BridgeI18n.setLanguage('zh');
 });
 await page.evaluate(readFileSync(path.join(__dirname,'side-chat-ui.browser.js'),'utf8'));
 const result=await page.evaluate(()=>runSideChatTests());
 expect(result.passed).toBeGreaterThanOrEqual(14);
 await expect(page.locator('.wb-side-chat-row')).toHaveCount(2);
 await expect(page.locator('.wb-side-chat-row').first()).toBeVisible();
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBe(true);
 await testInfo.attach('native-side-directory',{body:await page.screenshot(),contentType:'image/png'});
});
