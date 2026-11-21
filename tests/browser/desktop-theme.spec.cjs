'use strict';
const {test,expect}=require('@playwright/test');
const fs=require('node:fs'),path=require('node:path');
const root=path.resolve(__dirname,'../..');
const files=['desktop/shell/style.css','desktop/shared/language.css','desktop/shell/layout.css','web/features/accounts/account.css','web/features/accounts/quota-display.css','web/features/clients/client-lifecycle.css'];
function shell(){
  return fs.readFileSync(path.join(root,'desktop/index.html'),'utf8')
    .replace(/<script\b[^>]*>[\s\S]*?<\/script>/g,'').replace(/<link\b[^>]*>/g,'')
    .replace(/<img([^>]*?)src="([^"]+)"/g,(_,attrs,src)=>{
      const file=path.resolve(root,'desktop',src);return '<img'+attrs+'src="data:image/png;base64,'+fs.readFileSync(file).toString('base64')+'"';
    });
}
test('desktop shell follows system colors without losing settings, controls or readable shared dialogs',async({page,baseURL},info)=>{
  await page.emulateMedia({colorScheme:'light'});
  await page.goto('/__fixture_health');
  await page.setContent(shell());
  for(const [i,file] of files.entries()){
    const url=baseURL+'/__fixture_desktop_style_'+i+'.css';
    await page.route(url,route=>route.fulfill({path:path.join(root,file),contentType:'text/css'}));
    await page.addStyleTag({url});
  }
  await page.evaluate(()=>{
    window.originalMain=document.querySelector('main');document.getElementById('username').value='unsaved-theme-draft';
    const probe=document.createElement('article');probe.id='theme-probe';probe.innerHTML='<p class="account-muted">Shared account text</p><button>Action</button><button class="primary">Primary</button><input value="Editable"><input disabled value="Disabled"><select><option>Option</option></select><pre>Runtime log</pre><div class="error">Failure detail</div><div class="feedback">Saved feedback</div><p class="state-ready">Ready</p><table class="connection-table"><tr><td class="state-pending">Waiting</td></tr></table>';
    document.querySelector('main').prepend(probe);
    const dialog=document.createElement('dialog');dialog.className='account-reset-dialog';dialog.id='theme-dialog';dialog.innerHTML='<h2>Account settings</h2><p class="account-muted">Confirm the selected account.</p><button>Cancel</button>';document.body.append(dialog);
  });
  const light=await page.locator('html').evaluate(node=>getComputedStyle(node).backgroundColor);
  await page.emulateMedia({colorScheme:'dark'});
  await expect.poll(()=>page.locator('html').evaluate(node=>getComputedStyle(node).colorScheme)).toBe('dark');
  expect(await page.locator('html').evaluate(node=>getComputedStyle(node).backgroundColor)).not.toBe(light);
  const checks=await page.locator('#theme-probe').evaluate(probe=>{
    const luminance=color=>{const c=color.match(/[\d.]+/g).slice(0,3).map(Number).map(n=>n/255).map(n=>n<=.04045?n/12.92:((n+.055)/1.055)**2.4);return .2126*c[0]+.7152*c[1]+.0722*c[2];};
    return [...probe.querySelectorAll('p,button,input,select,pre,.error,.feedback,.state-pending')].map(node=>{
      let background=node;while(getComputedStyle(background).backgroundColor==='rgba(0, 0, 0, 0)')background=background.parentElement;
      const a=luminance(getComputedStyle(node).color),b=luminance(getComputedStyle(background).backgroundColor);
      return {tag:node.tagName,classes:node.className,contrast:(Math.max(a,b)+.05)/(Math.min(a,b)+.05)};
    });
  });
  for(const check of checks)expect(check.contrast,JSON.stringify(check)).toBeGreaterThanOrEqual(4.5);
  await expect(page.locator('#username')).toHaveValue('unsaved-theme-draft');
  expect(await page.evaluate(()=>originalMain===document.querySelector('main'))).toBe(true);
  await page.screenshot({path:info.outputPath('desktop-dark.png')});
  await page.evaluate(()=>document.getElementById('theme-dialog').showModal());
  await expect(page.locator('#theme-dialog')).toBeVisible();
  expect(await page.locator('#theme-dialog').evaluate(node=>getComputedStyle(node).backgroundColor)).toBe('rgb(34, 39, 49)');
  await page.screenshot({path:info.outputPath('desktop-dialog-dark.png')});
  await page.emulateMedia({colorScheme:'light'});
  await expect.poll(()=>page.locator('html').evaluate(node=>getComputedStyle(node).colorScheme)).toBe('light');
  await expect(page.locator('#username')).toHaveValue('unsaved-theme-draft');
  await expect(page.locator('#theme-dialog')).toBeVisible();
});
