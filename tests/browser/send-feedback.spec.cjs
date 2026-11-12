'use strict';
const {test,expect}=require('@playwright/test');

test.beforeEach(async({context,request,baseURL})=>{
 await context.route('**/*',route=>new URL(route.request().url()).origin===baseURL?route.continue():route.abort());
 expect(await(await request.get('/__fixture_health')).json()).toEqual({fixture:'cmb-browser-regression'});
});
for(const [provider,backend] of [['claude','code'],['claude','cowork'],['deepseek','desktop']])test(`${provider} ${backend} has immediate, scoped send feedback`,async({page},testInfo)=>{
 await page.goto('/?case=success');await expect(page.locator('#bootstrap-status')).toBeHidden();await page.waitForFunction(()=>typeof DesktopSessionsView==='function');
 await page.evaluate(async({provider,backend})=>{
  await BridgeFeatures.load('chat');document.getElementById('login').hidden=true;
  const root=document.createElement('div');root.className='app';root.id='send-feedback-root';root.style.cssText='position:fixed;inset:0;background:var(--page);z-index:10';document.body.append(root);
  const select=document.createElement('select');['codex','claude','deepseek'].forEach(id=>select.add(new Option(id,id)));
  const fixture=window.sendFeedbackFixture={writes:[],notices:[],finish:null};
  const detail={session:{id:'feedback-test',title:'发送交互验证',backend,status:'idle',capabilities:{send:true,attachments:true,projectReferences:true}},messages:[{id:'reply',role:'assistant',text:'可以发送一条测试消息。'}],connected:true};
  const view=fixture.view=new DesktopSessionsView({root,select,notify:text=>fixture.notices.push(text),onSelect(){},csrf:()=>'',onUnauthorized(){},request:async(url,body)=>{
   if(body){fixture.writes.push(body);return new Promise(resolve=>fixture.finish=resolve);}
   if(url.includes('/detail'))return detail;if(url.includes('/workspace'))return {entries:[],total:0,project:'/fixture'};
   return {sessions:[detail.session]};
  }});
  await view.choose(provider);await view.open('feedback-test');clearTimeout(view.timer);
 },{provider,backend});
 const composer=page.locator('#send-feedback-root .ds-composer'),input=composer.locator('textarea'),send=composer.locator('button[type=submit]'),feedback=composer.locator('.ds-send-feedback');
 await input.fill('请检查这条消息的发送状态，暂不执行任何任务。');await send.click();
 await expect(feedback).toBeVisible();await expect(feedback).toContainText('正在发送…');await expect(feedback).toContainText('请检查这条消息');await expect(send).toBeDisabled();
 expect(await page.evaluate(()=>sendFeedbackFixture.notices)).toEqual([]);
 for(const theme of ['light','dark']){
  await page.evaluate(theme=>document.documentElement.dataset.theme=theme,theme);
  await expect(feedback).toBeInViewport();
  expect(await feedback.evaluate(node=>node.scrollWidth<=node.clientWidth+1)).toBe(true);
  await page.screenshot({path:testInfo.outputPath(`${provider}-${backend}-${theme}-sending.png`)});
 }
 await input.fill('下一条草稿');
 await page.evaluate(()=>sendFeedbackFixture.finish({status:'accepted'}));
 await expect(feedback).toBeHidden();await expect(input).toHaveValue('下一条草稿');
 await expect.poll(()=>page.evaluate(()=>sendFeedbackFixture.notices)).toEqual(['已发送到会话']);
 await send.click();await expect(feedback).toBeVisible();
 await page.evaluate(()=>sendFeedbackFixture.finish({status:'unknown',message:'发送结果待核对'}));
 await expect(feedback).toBeHidden();await expect(input).toHaveValue('下一条草稿');await expect(composer.locator('.error')).toContainText('发送结果待核对');
 expect(await page.evaluate(()=>sendFeedbackFixture.writes.length)).toBe(2);
 expect(await page.evaluate(()=>sendFeedbackFixture.notices.filter(value=>value==='已发送到会话').length)).toBe(1);
 // A successful submission is distinct from ongoing native work. The indicator
 // remains in the real chat timeline and never reuses the send acknowledgement.
 await page.evaluate(()=>{const v=sendFeedbackFixture.view;v.applyDetail({connected:true,session:{...v.state,status:'active',requests:[]},messages:v.historyMessages});});
 const progress=page.locator('#send-feedback-root .ds-messages .working');
 await expect(progress).toBeVisible();await expect(progress).toContainText('正在工作…');
 expect(await progress.evaluate(node=>node.scrollWidth<=node.clientWidth+1)).toBe(true);
 await page.screenshot({path:testInfo.outputPath(`${provider}-${backend}-native-working.png`)});
 await page.evaluate(()=>sendFeedbackFixture.view.staleDetail());
 await expect(progress).toContainText('运行状态暂不可用');
 await page.evaluate(()=>{const v=sendFeedbackFixture.view;v.applyDetail({connected:true,session:{...v.state,status:'idle',requests:[]},messages:v.historyMessages});});
 await expect(progress).toBeHidden();
 const imageId='a'.repeat(64),png=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a8XcAAAAASUVORK5CYII=','base64');
 await page.route('**/api/desktop-sessions/*/images/*',route=>route.fulfill({status:200,contentType:'image/png',body:png}));
 await page.evaluate(imageId=>{const v=sendFeedbackFixture.view;v.applyDetail({connected:true,session:v.state,messages:[{id:'history-image',role:'user',text:'',attachments:[{type:'image',name:'photo.png',desktopId:imageId}]}]});},imageId);
 const image=page.locator('#send-feedback-root .attachment-image');await expect(image).toBeVisible();
 await expect.poll(()=>image.evaluate(node=>node.naturalWidth)).toBe(1);
 await image.click();await expect(page.locator('#image-preview-dialog')).toBeVisible();
 await page.screenshot({path:testInfo.outputPath(`${provider}-${backend}-history-image.png`)});
 await page.keyboard.press('Escape');await expect(page.locator('#image-preview-dialog')).toBeHidden();
 await page.evaluate(()=>{const v=sendFeedbackFixture.view;document.documentElement.dataset.showProcess='true';v.applyDetail({connected:true,session:v.state,messages:[{id:'context',role:'activity',kind:'context',title:'上下文',text:'项目说明与自动注入内容'}]});});
 const context=page.locator('#send-feedback-root .activity');await expect(context.locator('pre')).toBeHidden();await context.locator('summary').click();await expect(context.locator('pre')).toBeVisible();
 await page.evaluate(()=>{const v=sendFeedbackFixture.view;v.collapseComposer(true);v.workbench.request=async()=>({name:'photo.png',path:'images/photo.png',absolutePath:'/fixture/images/photo.png',size:4,kind:'file'});BridgeFileActions.menu(v.workbench,v.workbench.current,{name:'photo.png',path:'images/photo.png',kind:'file'});});
 await page.getByRole('button',{name:'引用到消息',exact:true}).click();await expect(input).toBeVisible();await expect(input).toHaveValue('下一条草稿');await expect(page.locator('#send-feedback-root .attachment-chip')).toContainText('photo.png');
 expect(await page.evaluate(()=>sendFeedbackFixture.writes.length)).toBe(2);
 await page.screenshot({path:testInfo.outputPath(`${provider}-${backend}-project-reference.png`)});
 if(provider==='claude'&&backend==='code'){
  await page.evaluate(()=>{
   window.BridgeUploadSource=()=>true;
   const v=sendFeedbackFixture.view;v.state.cwd='/fixture';v.updateComposer();
   v.workbench.request=async()=>({project:'/fixture',entries:Array.from({length:60},(_,i)=>({name:`file-${i}.txt`,path:`file-${i}.txt`,kind:'file',size:10})),nextOffset:null});
  });
  await composer.locator('.attach-button').click();
  await expect(page.locator('.attachment-menu button')).toHaveCount(3);
  await expect(page.getByRole('button',{name:'拍照或录像',exact:true})).toHaveCount(0);
  // Opening the sheet by touch/pointer must not paint a keyboard focus ring
  // on Photos; keyboard navigation still reaches every action.
  expect(await page.locator('.attachment-menu').evaluate(node=>document.activeElement===node)).toBe(true);
  await page.keyboard.press(process.platform==='darwin'&&testInfo.project.name.startsWith('webkit')?'Alt+Tab':'Tab');
  await expect(page.getByRole('button',{name:'照片图库',exact:true})).toBeFocused();
  await page.keyboard.press('Escape');
  await composer.locator('.attach-button').click();
  await page.screenshot({path:testInfo.outputPath('attachment-menu-chat.png')});
  await page.getByRole('button',{name:'引用宿主机项目文件',exact:true}).click();
  const picker=page.locator('.host-file-picker'),list=picker.locator('.wb-file-list'),search=picker.getByRole('searchbox');
  await expect(list.locator('.wb-file')).toHaveCount(60);
  const before=await search.boundingBox();
  await list.locator('.wb-file').first().hover();await page.mouse.wheel(0,450);
  await expect.poll(()=>list.evaluate(node=>node.scrollTop)).toBeGreaterThan(100);
  expect((await search.boundingBox()).y).toBe(before.y);
  expect(await picker.evaluate(node=>node.scrollTop)).toBe(0);
  await page.screenshot({path:testInfo.outputPath('host-file-picker-scrolled-chat.png')});
  await page.keyboard.press('Escape');
 }
 await page.evaluate(()=>sendFeedbackFixture.view.clear());
});
