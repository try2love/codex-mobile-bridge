'use strict';
const {test,expect}=require('@playwright/test');

async function setup(page,provider='codex'){
  await page.route('**/api/**',async route=>{
    const path=new URL(route.request().url()).pathname;
    if(path.endsWith('/skills'))return route.fulfill({json:{skills:[],total:0}});
    return route.continue();
  });
  await page.goto('/?case=success');
  await page.waitForFunction(()=>document.getElementById('bootstrap-status')?.hidden);
  await page.evaluate(()=>BridgeFeatures.load('chat'));
  await page.evaluate(async provider=>{
    BridgeI18n.setLanguage('zh');document.getElementById('login').hidden=true;
    const session={id:'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee',title:'输入区 · 合成会话',cwd:'/fixture',model:'gpt-5.1-codex-max',effort:'high',permissionMode:'auto-review',host:'local',connected:true,status:'idle',requests:[],submissions:[],historyComplete:true};
    const capabilities={send:true,skills:true,models:true,permissions:true,attachments:true,uploadFiles:true,projectReferences:true,plan:true,goal:true,queue:true};
    const meta={...session,capabilities};
    if(provider==='codex'){
      document.getElementById('app').hidden=false;document.getElementById('welcome').hidden=true;document.getElementById('chat').hidden=false;document.getElementById('app').classList.add('chat-open');
      currentId=session.id;currentHost='local';workbench.open(currentId,'local');attachments.open('local|'+currentId);workModes.open('local|'+currentId);renderState(meta);
      globalThis.__composerFixture={form:document.getElementById('composer'),input:document.getElementById('message'),panel:attachments,modes:workModes,compact:compactComposer,meta};
    }else{
      const root=document.createElement('div');root.className='app';root.style.cssText='position:fixed;inset:0;z-index:10;background:var(--page)';document.body.append(root);
      const select=document.createElement('select');for(const id of ['codex',provider])select.add(new Option(id,id));
      const detail={session,capabilities,messages:[]};
      const view=new DesktopSessionsView({root,select,notify(){},onSelect(){},csrf:()=>'',onUnauthorized(){},request:async url=>url.includes('/detail')?detail:url.includes('/skills')?{skills:[],total:0}:{sessions:[session]}});
      view.setClientStates([{id:provider,enabled:true,connected:true}]);await view.choose(provider);await view.open(session.id);clearTimeout(view.timer);
      globalThis.__composerFixture={form:view.form,input:view.input,panel:view.attachments,modes:view.workModes,compact:view.compactComposer,view,meta};
    }
  },provider);
}

for(const provider of ['codex','claude'])test(provider+' compact composer menu, native inputs, shortcuts and themes',async({page},testInfo)=>{
  const errors=[];page.on('pageerror',error=>errors.push(error.message));
  await setup(page,provider);
  const form=page.locator(provider==='codex'?'#composer':'.ds-composer').last();
  const input=form.locator('textarea');
  for(const size of [{width:375,height:812},{width:768,height:1024}]){
    await page.setViewportSize(size);
    for(const theme of ['light','dark']){
      await page.evaluate(theme=>document.documentElement.dataset.theme=theme,theme);
      await input.fill('保留草稿，尚未发送');
      const geometry=await form.evaluate(form=>{
        const model=form.querySelector('#model-button,.ds-model'),permission=form.querySelector('#permissions-button,.ds-permissions'),ring=form.querySelector('.context-usage');
        const a=model.getBoundingClientRect(),b=permission.getBoundingClientRect(),c=ring.getBoundingClientRect();
        return {fits:form.scrollWidth<=form.clientWidth+1,order:b.left<a.left&&a.right<=c.left+1,modelWidth:a.width,background:getComputedStyle(form.querySelector('textarea')).backgroundColor,collapsed:!!form.querySelector('.composer-toggle'),visibleSelects:[...form.querySelectorAll('select')].filter(n=>n.getClientRects().length).length};
      });
      expect(geometry.fits).toBe(true);expect(geometry.order).toBe(true);expect(geometry.modelWidth).toBeGreaterThan(100);
      expect(geometry.collapsed).toBe(false);expect(geometry.visibleSelects).toBe(0);
      expect(geometry.background).not.toBe(theme==='dark'?'rgb(255, 255, 255)':'rgb(32, 34, 40)');
      await form.locator('.attach-button').click();
      const menu=page.locator('.attachment-menu[open]');
      await expect(menu.locator('button')).toHaveCount(7);
      for(const label of ['拍照','选择照片','选择文件','引用宿主机项目文件','Skill','计划模式','目标模式'])await expect(menu.getByRole('button',{name:label,exact:false})).toBeVisible();
      expect(await input.inputValue()).toBe('保留草稿，尚未发送');
      await page.waitForTimeout(2200);
      await page.screenshot({path:testInfo.outputPath(provider+'-'+size.width+'-'+theme+'-menu.png')});
      await page.keyboard.press('Escape');
      await page.screenshot({path:testInfo.outputPath(provider+'-'+size.width+'-'+theme+'-composer.png')});
    }
  }
  // Three real chooser events, not simulated clicks on an unrelated input.
  for(const [label,accept,capture,multiple] of [['拍照','image/*','environment',false],['选择照片','image/*',null,true],['选择文件',provider==='codex'?'': '',null,true]]){
    await form.locator('.attach-button').click();
    const choosing=page.waitForEvent('filechooser');
    await page.locator('.attachment-menu[open]').getByRole('button',{name:label,exact:true}).click();
    const chooser=await choosing,element=chooser.element();
    expect(await element.getAttribute('accept')||'').toBe(accept);expect(await element.getAttribute('capture')).toBe(capture);expect(chooser.isMultiple()).toBe(multiple);
    await chooser.setFiles([]);
    expect(await input.inputValue()).toBe('保留草稿，尚未发送');
  }
  const requests=[];page.on('request',r=>{if(new URL(r.url()).pathname.startsWith('/api/'))requests.push(r.url());});
  await input.fill('/');await expect(form.locator('.composer-suggestions')).toBeVisible();
  await page.keyboard.press('Enter');
  await expect(form.locator('.composer-mode-badge')).toHaveText('计划模式 ×');expect(await input.inputValue()).toBe('');
  await form.locator('.composer-mode-badge').click();
  await input.fill('/goal');await form.locator('.composer-suggestions button').click();await expect(form.locator('.composer-mode-badge')).toHaveText('目标模式 ×');
  await form.locator('.composer-mode-badge').click();
  await input.fill('/');await page.keyboard.press('Escape');await expect(form.locator('.composer-suggestions')).toBeHidden();
  await input.fill('');await input.fill('/');await expect(form.locator('.composer-suggestions')).toBeVisible();await input.fill('cost $10');await expect(form.locator('.composer-suggestions')).toBeHidden();
  await page.evaluate(()=>{const f=__composerFixture;if(f.view){f.view.state.status='active';f.view.updateComposer();}else{f.meta.status='active';renderState(f.meta);}});
  await input.fill('/');await expect(form.locator('.composer-suggestions button').first()).toBeVisible();
  const options=form.locator('.composer-suggestions button');expect(await options.count()).toBeGreaterThan(0);
  expect(await form.locator('.composer-suggestions button:disabled').count()).toBe(await options.count());
  await page.keyboard.press('Escape');expect(await input.inputValue()).toBe('/');
  expect(await form.locator('.composer-mode-badge').isHidden()).toBe(true);
  await page.evaluate(()=>{const f=__composerFixture;if(f.view){f.view.state.status='idle';f.view.updateComposer();}else{f.meta.status='idle';renderState(f.meta);}});
  expect(requests.filter(url=>url.includes('/skills'))).toHaveLength(0); // $ reuses the existing picker and catalog cache.
  await input.fill('$');await expect(page.locator(provider==='codex'?'#skills-dialog[open]':'dialog.picker[open]')).toBeVisible();
  expect(await input.inputValue()).toBe('');await page.keyboard.press('Escape');
  await page.evaluate(()=>{
    const f=__composerFixture;
    if(f.view){f.view.state.capabilities={...f.view.state.capabilities,skills:false,plan:false,goal:false,attachments:false,projectReferences:false};f.view.updateComposer();}
    else renderState({...f.meta,access:{canSend:true,canUseModes:false,canSelectSkills:false},capabilities:{plan:false,goal:false,skills:false}});
  });
  if(provider==='claude'){await expect(form.locator('.attach-button')).toBeHidden();expect(errors).toEqual([]);return;}
  await form.locator('.attach-button').click();
  const skill=page.locator('.attachment-menu[open]').getByRole('button',{name:'Skill',exact:false});
  await expect(skill).toBeDisabled();
  await page.keyboard.press('Escape');
  expect(errors).toEqual([]);
});
