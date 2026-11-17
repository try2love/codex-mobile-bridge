'use strict';
const {test,expect}=require('@playwright/test');

test.beforeEach(async({page,context,baseURL})=>{
  await context.route('**/*',route=>new URL(route.request().url()).origin===baseURL?route.continue():route.abort());
  await page.goto('/?case=success');
  await page.waitForFunction(()=>document.getElementById('bootstrap-status')?.hidden);
  await page.evaluate(()=>{
    document.getElementById('login').hidden=true;document.getElementById('app').hidden=false;document.getElementById('app').classList.add('chat-open');document.getElementById('welcome').hidden=true;document.getElementById('chat').hidden=false;
    window.__toolbarTest={main:composerToolbar,other:desktopSessions.toolbar,model:document.getElementById('model-button'),select:document.getElementById('send-mode')};
    const input=document.getElementById('message');input.value='保留草稿与光标';input.setSelectionRange(2,4);
  });
});
async function edit(page,select=true){await page.locator('#composer .toolbar-more').click();const spacing=await page.locator('.toolbar-menu[open] .picker-head').evaluate(head=>{const edit=head.querySelector('.toolbar-edit-button').getBoundingClientRect(),close=head.lastElementChild.getBoundingClientRect();return close.left-edit.right;});expect(spacing).toBeGreaterThanOrEqual(0);expect(spacing).toBeLessThanOrEqual(8);await page.locator('.toolbar-menu[open] .toolbar-edit-button').click();await expect(page.locator('.toolbar-editor')).toBeVisible();if(select)await page.locator('.toolbar-editor [data-drag-tool=model]').click();}
test('editor keeps settings below the library and switches selected tools in place',async({page})=>{
 await edit(page,false);await expect(page.locator('.toolbar-editor-inspector')).toBeVisible();
 await expect(page.locator('[data-drag-tool=model]')).toHaveAttribute('aria-pressed','true');
 await expect(page.locator('.toolbar-inspector-close')).toHaveCount(0);
 const before=await page.locator('.toolbar-editor-inspector').boundingBox();
 await page.locator('[data-drag-tool=skills]').click();
 const after=await page.locator('.toolbar-editor-inspector').boundingBox();expect(after.height).toBeCloseTo(before.height,0);
 expect(await page.locator('.toolbar-editor-inspector').evaluate(node=>!!node.previousElementSibling?.classList.contains('toolbar-editor-toolbox'))).toBe(true);
 await expect(page.locator('.toolbar-editor-canvas [data-drag-tool=permissions] .toolbar-preview-caption')).toHaveText('权限');
 await expect(page.locator('.toolbar-editor-canvas [data-drag-tool=permissions] .toolbar-preview-caption')).toBeVisible();
 await expect(page.locator('.toolbar-editor-canvas [data-drag-tool=model] .toolbar-preview-caption')).toBeVisible();
 await page.locator('[data-drag-tool=model]').click();await expect(page.locator('.toolbar-editor-inspector')).toBeVisible();
 for(const mode of ['icon','text','both']){
  await page.locator('[data-display-choice='+mode+']').click();
  await expect(page.locator('.toolbar-editor-canvas [data-drag-tool=model] .toolbar-preview-caption')).toBeVisible();
  await expect(page.locator('.toolbar-editor-canvas [data-drag-tool=model] svg')).toBeVisible();
  await expect(page.locator('#composer [data-tool=model]')).toHaveAttribute('data-display',mode);
 }
 await expect(page.locator('.toolbar-editor-canvas [data-drag-tool=model] .toolbar-preview-caption')).toBeVisible();
 await page.locator('[data-editor-tool=submit]').click();await expect(page.locator('.toolbar-editor-inspector')).toBeVisible();
 expect((await page.locator('.toolbar-editor-inspector').boundingBox()).height).toBeCloseTo(before.height,0);
 await expect(page.locator('[data-move-zone=topLeft]')).toBeHidden();
 await expect(page.locator('.toolbar-editor')).toBeVisible();
 await drag(page,'skills','topLeft');await expect(page.locator('.toolbar-editor-inspector')).toBeVisible();
 await expect(page.locator('.toolbar-display-title').first()).toHaveText('Skill');
});
test('preview preserves native button sizing across display styles',async({page})=>{
 await page.setViewportSize({width:390,height:844});await edit(page,false);
 const preview=page.locator('.toolbar-editor-canvas');
 await page.locator('[data-drag-tool=model]').click();
 const alignment=await preview.evaluate(canvas=>{
  const attachment=canvas.querySelector('[data-drag-tool=attachments]'),model=canvas.querySelector('[data-drag-tool=model]');
  const icon=attachment.querySelector('svg').getBoundingClientRect(),label=model.querySelector('svg').getBoundingClientRect();
  return {delta:Math.abs(icon.top+icon.height/2-label.top-label.height/2),overflow:canvas.scrollWidth>canvas.clientWidth,heights:[attachment,model,canvas.querySelector('[data-editor-tool=submit]')].map(node=>node.getBoundingClientRect().height)};
 });
 expect(alignment.delta).toBeLessThanOrEqual(1);expect(alignment.overflow).toBe(false);expect(alignment.heights).toEqual([44,44,44]);
});
test('lightweight editor fits phone widths in light and dark themes',async({page})=>{
 for(const theme of ['light','dark'])for(const width of [320,390]){
  await page.setViewportSize({width,height:844});await page.evaluate(theme=>document.documentElement.dataset.theme=theme,theme);
  await edit(page,false);
  const layout=await page.locator('.toolbar-editor').evaluate(dialog=>{
   const bounds=dialog.getBoundingClientRect(),preview=dialog.querySelector('.toolbar-editor-preview-area').getBoundingClientRect(),library=dialog.querySelector('.toolbar-editor-toolbox').getBoundingClientRect(),settings=dialog.querySelector('.toolbar-editor-inspector').getBoundingClientRect();
   const canvas=dialog.querySelector('.toolbar-editor-canvas').getBoundingClientRect(),grid=dialog.querySelector('.toolbar-editor-library').getBoundingClientRect();
   return {bottom:settings.bottom-dialog.querySelector('.toolbar-move-choices').getBoundingClientRect().bottom,above:grid.top-canvas.bottom,below:settings.top-grid.bottom,fits:dialog.scrollWidth<=dialog.clientWidth&&bounds.left>=0&&bounds.right<=innerWidth&&bounds.top>=0&&bounds.bottom<=innerHeight,order:preview.bottom<=library.top&&library.bottom<=settings.top,tile:getComputedStyle(dialog.querySelector('.toolbar-editor-library .toolbar-drag-tool')).backgroundColor,library:getComputedStyle(dialog.querySelector('.toolbar-editor-toolbox')).backgroundColor};
  });
  expect(layout.above).toBe(16);expect(layout.below).toBe(16);expect(layout.bottom).toBe(16);
  expect(layout.fits).toBe(true);expect(layout.order).toBe(true);expect(layout.tile).toBe('rgba(0, 0, 0, 0)');
  await page.locator('.toolbar-editor > .picker-head .primary').click();
 }
});
test('layout presets switch rows, synchronize composers and allow further customization',async({page})=>{
  await page.evaluate(()=>__toolbarTest.main.preferences.update({followUpMode:'steer',display:{permissions:'hidden'}}));
  await edit(page);
  const editor=page.locator('.toolbar-editor'),preset=id=>editor.locator('[data-composer-preset='+id+']');
  for(const id of ['single','default']){
    if(id==='default'){page.once('dialog',dialog=>dialog.accept());await editor.getByRole('button',{name:'双栏默认',exact:true}).click();}
    else{page.once('dialog',dialog=>dialog.accept());await preset(id).click();await expect(preset(id)).toHaveAttribute('aria-pressed','true');}
    expect(await page.evaluate(id=>{
      const {main,other}=__toolbarTest,expected=composerPreset(id);
      return JSON.stringify(main.layout)===JSON.stringify(expected.layout)&&JSON.stringify(other.layout)===JSON.stringify(expected.layout)&&main.followUpMode==='steer';
    },id)).toBe(true);
    await expect(page.locator('#composer .toolbar-top'))[id==='single'?'toBeHidden':'toBeVisible']();
    if(id==='single'){
      expect(await editor.locator('[data-drop-zone=bottomRight] [data-drag-tool]').evaluateAll(nodes=>nodes.map(node=>node.dataset.dragTool))).toEqual(['context','attachments']);
      await expect(editor.locator('[data-drag-tool=collapse]')).toHaveClass(/toolbar-drag-hidden/);
    }
    await expect(page.locator('#message')).toHaveValue('保留草稿与光标');
  }
  const before=await page.evaluate(()=>JSON.stringify(composerToolbar.preferences.value));
  page.once('dialog',dialog=>{expect(dialog.type()).toBe('confirm');expect(dialog.message()).toBe('恢复单栏默认布局和显示方式？');return dialog.dismiss();});
  await preset('single').click();
  expect(await page.evaluate(()=>JSON.stringify(composerToolbar.preferences.value))).toBe(before);
  page.once('dialog',dialog=>dialog.accept());await preset('single').click();
  await editor.locator('[data-drag-tool=permissions]').click();
  await editor.locator('[data-display-choice=both]').click();
  await expect(preset('single')).toHaveAttribute('aria-pressed','false');
  await page.reload();await page.waitForFunction(()=>document.getElementById('bootstrap-status')?.hidden);
  expect(await page.evaluate(()=>composerToolbar.layout.topLeft.length===0&&composerToolbar.display.permissions==='both'&&composerToolbar.followUpMode==='steer')).toBe(true);
});
test('permissions stored in the tools panel open the original session permission picker',async({page})=>{
  await page.evaluate(()=>{
    currentId='permission-panel-fixture';currentHost='local';state={connected:true,permissionMode:'ask'};
    const t=__toolbarTest.main,layout=structuredClone(t.layout);
    for(const zone of composerZones)layout[zone]=layout[zone].filter(id=>id!=='permissions');layout.more.push('permissions');
    t.controls.permissions.disabled=false;t.save(layout);t.sync();
  });
  await page.locator('#composer .toolbar-more').click();await expect(page.locator('.toolbar-menu[open] [data-menu-tool=permissions]')).toBeEnabled();
  await page.locator('.toolbar-menu[open] [data-menu-tool=permissions]').click();
  await expect(page.locator('dialog[open][data-permission-thread="permission-panel-fixture"]')).toBeVisible();
  await expect(page.locator('dialog[open][data-permission-thread="permission-panel-fixture"] .permission-option')).toHaveCount(3);
});
test('hidden display stores the tool, hides normal entries and supports restoring or dragging it back',async({page})=>{
  await page.setViewportSize({width:320,height:844});
  for(const which of ['main','other']){
    await page.evaluate(which=>{
      const t=__toolbarTest[which];if(which==='other'){document.body.append(t.form);t.form.style.width='290px';}
      const ids=composerTools.map(tool=>tool.id);t.save({topLeft:['model'],topRight:[],bottomLeft:['more'],bottomRight:[],more:ids.filter(id=>!['model','more'].includes(id))},{...t.display,model:'text'});t.input.value='保留隐藏工具时的草稿';t.sync();
    },which);
    const root=page.locator(which==='main'?'#composer':'.ds-composer');
    await root.locator('.toolbar-more').click();await page.locator('.toolbar-menu[open] .toolbar-edit-button').click();
    await page.locator('.toolbar-editor[open] [data-drag-tool=model]').click();await page.locator('.toolbar-editor[open] [data-display-choice=hidden]').click();
    await expect(page.locator('.toolbar-editor[open] [data-drop-zone=more] [data-drag-tool=model]')).toContainText('隐藏');
    await expect(page.locator('.toolbar-editor[open] .toolbar-tool-status')).toContainText('已隐藏');
    await expect(root.locator('.toolbar-top')).toBeHidden();
    await page.locator('.toolbar-editor[open] .toolbar-editor-close').click();await root.locator('.toolbar-more').click();
    await expect(page.locator('.toolbar-menu[open] [data-menu-tool=model]')).toHaveCount(0);
    await page.locator('.toolbar-menu[open] .toolbar-edit-button').click();
    await page.locator('.toolbar-editor[open] [data-drag-tool=model]').click();await page.locator('.toolbar-editor[open] [data-display-choice=text]').click();
    await page.locator('.toolbar-editor[open] .toolbar-editor-close').click();await root.locator('.toolbar-more').click();
    await expect(page.locator('.toolbar-menu[open] [data-menu-tool=model]')).toHaveCount(1);
    await page.locator('.toolbar-menu[open] .toolbar-edit-button').click();await page.locator('.toolbar-editor[open] [data-drag-tool=model]').click();
    await page.locator('.toolbar-editor[open] [data-display-choice=hidden]').click();await drag(page,'model','bottomLeft');
    expect(await page.evaluate(which=>__toolbarTest[which].display.model,which)).toBe('text');
    await page.locator('.toolbar-editor[open] [data-drag-tool=more]').click();await expect(page.locator('.toolbar-editor[open] [data-display-choice=hidden]')).toBeHidden();
    await page.locator('.toolbar-editor[open] [data-editor-tool=submit]').click();await expect(page.locator('.toolbar-editor[open] [data-display-choice=hidden]')).toBeHidden();
    await page.locator('.toolbar-editor[open] .toolbar-editor-close').click();await expect(root.locator('[data-tool=model]')).toBeVisible();
    await expect(root.locator('textarea')).toHaveValue('保留隐藏工具时的草稿');
  }
  await page.evaluate(()=>{const t=__toolbarTest.main;t.save(t.layout,{...t.display,model:'hidden'});});
  await page.reload();await page.waitForFunction(()=>document.getElementById('bootstrap-status')?.hidden);
  expect(await page.evaluate(()=>({display:composerToolbar.display.model,stored:composerToolbar.layout.more.includes('model')}))).toEqual({display:'hidden',stored:true});
});
test('movable panel entry enables a single row and stays reachable through collapse, editing and reload',async({page})=>{
  await page.setViewportSize({width:320,height:844});
  for(const which of ['main','other']){
    await page.evaluate(which=>{
      const t=__toolbarTest[which];if(which==='other'){document.body.append(t.form);t.form.style.width='290px';}
      const ids=composerTools.map(tool=>tool.id);t.save({topLeft:[],topRight:[],bottomLeft:['more','model'],bottomRight:[],more:ids.filter(id=>!['more','model'].includes(id))});
      t.input.value='保留单栏草稿';t.sync();
    },which);
    const root=page.locator(which==='main'?'#composer':'.ds-composer');
    await expect(root.locator('.toolbar-top')).toBeHidden();await expect(root.locator('.toolbar-more')).toBeVisible();
    await root.locator('.toolbar-more').click();await page.locator('.toolbar-menu[open] .toolbar-edit-button').click();
    await page.locator('.toolbar-editor[open] [data-drag-tool=more]').click();
    await expect(page.locator('.toolbar-editor[open] [data-move-zone=more]')).toBeDisabled();
    for(const zone of ['topLeft','topRight','bottomLeft','bottomRight']){
      await page.locator('.toolbar-editor[open] [data-move-zone='+zone+']').click();
      expect(await page.evaluate(({which,zone})=>__toolbarTest[which].layout[zone].includes('more'),{which,zone})).toBe(true);
      for(const display of ['icon','text','both'])await page.locator('.toolbar-editor[open] [data-display-choice='+display+']').click();
    }
    await page.locator('.toolbar-editor[open] [data-drag-tool=more]').press('Delete');
    expect(await page.evaluate(which=>__toolbarTest[which].layout.bottomRight.includes('more'),which)).toBe(true);
    await page.locator('.toolbar-editor[open] .toolbar-editor-close').click();
    await expect(root.locator('.toolbar-top')).toBeHidden();
    await root.locator('.toolbar-more').click();await page.locator('.toolbar-menu[open] [data-menu-tool=collapse]').click();
    await expect(root.locator('textarea')).toBeHidden();await expect(root.locator('.toolbar-more')).toBeVisible();
    await root.locator('.toolbar-more').click();await expect(page.locator('.toolbar-menu[open] .toolbar-edit-button')).toBeVisible();await page.keyboard.press('Escape');
    await root.locator('.toolbar-restore').click();await expect(root.locator('.toolbar-top')).toBeHidden();
    await expect(root.locator('textarea')).toHaveValue('保留单栏草稿');
    expect(await root.evaluate(form=>form.scrollWidth<=form.clientWidth+1)).toBe(true);
  }
  await page.reload();await page.waitForFunction(()=>document.getElementById('bootstrap-status')?.hidden);
  expect(await page.evaluate(()=>composerToolbar.layout.bottomRight.includes('more'))).toBe(true);
  expect(await page.evaluate(()=>composerToolbar.tools.hidden)).toBe(true);
});
test('toolbar adopts the model settings name when the catalog arrives and retains the full ID in its tooltip',async({page})=>{
  const id='provider/very-long-model-route',name='Model Short';
  await page.route('**/api/sessions/model-label-chat/catalog?kind=models',route=>route.fulfill({json:{models:[{id,name}]}}));
  await page.evaluate(id=>{
    currentId='model-label-chat';currentHost='local';state={model:id,effort:'high',provider:'codex'};
    catalogData.models=null;catalogTasks.models=null;renderComposerModel();
  },id);
  await expect(page.locator('#model-name')).toHaveText(id);
  await page.evaluate(()=>loadCatalogKind('models'));
  await expect(page.locator('#model-name')).toHaveText(name);
  await expect(page.locator('#model-effort')).toHaveText('· 高');
  await expect(page.locator('#model-button')).toHaveAttribute('title',id+' · 高');
  await page.evaluate(()=>{
    $('effort-select').replaceChildren(new Option('high','high'));renderEffortChoices();
  });
  await expect(page.locator('#effort-options .effort-choice>span').first()).toHaveText('高');
});
test('independent send mode defaults to More, moves to a toolbar and shares the merged selection',async({page})=>{
  await page.evaluate(()=>{const t=__toolbarTest.main;t.controls.sendMode.disabled=false;for(const option of t.controls.sendMode.options)option.disabled=false;t.submitControl.getState=()=>({running:true,hasDraft:true});t.sync();});
  await page.locator('#composer .toolbar-more').click();await page.locator('[data-menu-tool=sendMode]').click();await page.locator('.toolbar-choice-option').filter({hasText:'补充当前任务'}).click();await expect(page.locator('#send')).toContainText('补充任务');
  await page.locator('#composer .composer-submit-caret').click();await page.locator('#composer [data-send-mode=queue]').click();expect(await page.evaluate(()=>__toolbarTest.select.value)).toBe('queue');
  await page.evaluate(()=>{const t=__toolbarTest.main,layout=structuredClone(t.layout);layout.more=layout.more.filter(id=>id!=='sendMode');layout.topLeft.push('sendMode');t.save(layout);});
  await expect(page.locator('#send-mode')).toBeVisible();await expect(page.locator('#send-mode')).toHaveValue('queue');await page.locator('#send-mode').selectOption('send');await expect(page.locator('#send')).toContainText('发送');
  for(const style of ['icon','text','both']){
    await page.evaluate(style=>{const t=__toolbarTest.main;t.save(t.layout,{...t.display,sendMode:style});},style);
    const icon=page.locator('#composer [data-tool=sendMode] .toolbar-select-icon');if(style==='text')await expect(icon).toBeHidden();else await expect(icon).toBeVisible();
    await page.locator('#send-mode').selectOption('steer');await expect(page.locator('#send')).toContainText('补充任务');await page.locator('#send-mode').selectOption('queue');await expect(page.locator('#send')).toContainText('排队');
  }
  await page.locator('#composer .toolbar-more').click();await expect(page.locator('[data-menu-tool=sendMode]')).toHaveCount(0);await page.keyboard.press('Escape');
  expect(await page.evaluate(()=>__toolbarTest.main.controls.sendMode===__toolbarTest.select)).toBe(true);
});
test('merged actions switch with draft content, preserve drafts on stop and keep mode selection separate from sending',async({page})=>{
  for(const which of ['main','other']){
    await page.evaluate(which=>{
      const t=__toolbarTest[which];if(which==='other'){document.body.append(t.form);t.form.style.width='360px';}
      t.preferences.update({followUpMode:'queue'});const fixture=window.__merged={running:false,pending:false,writes:[],stops:0};
      t.input.value='保留草稿与光标';t.input.oninput=()=>t.requestSync();t.controls.sendMode.onchange=()=>{};t.submitControl.getState=()=>({running:fixture.running,pending:fixture.pending,hasDraft:!!t.input.value.trim()});
      t.controls.sendMode.disabled=false;for(const option of t.controls.sendMode.options)option.disabled=false;
      t.submitControl.send.disabled=false;t.submitControl.stop.hidden=false;t.submitControl.stop.disabled=false;
      t.submitControl.stop.onclick=()=>{fixture.stops++;fixture.running=false;t.sync();};
      t.form.onsubmit=event=>{event.preventDefault();fixture.writes.push({text:t.input.value,mode:t.actionMode()});};t.sync();
    },which);
    const root=page.locator(which==='main'?'#composer':'.ds-composer'),caret=root.locator('.composer-submit-caret'),send=root.locator('button[type=submit]'),input=root.locator('textarea');
    await expect(caret).toBeHidden();await expect(send).toContainText('发送');
    await page.evaluate(which=>{__merged.running=true;__toolbarTest[which].sync();},which);
    await expect(caret).toBeVisible();await expect(send).toContainText('排队');await caret.click();
    await root.locator('[data-send-mode=steer]').click();await expect(send).toContainText('补充任务');expect(await page.evaluate(()=>__merged.writes.length)).toBe(0);
    await send.click();expect(await page.evaluate(()=>__merged.writes.at(-1).mode)).toBe('steer');
    const draft=await input.inputValue();await caret.click();await root.locator('.composer-submit-menu-stop').click();await expect(input).toHaveValue(draft);await expect(caret).toBeHidden();expect(await page.evaluate(()=>__merged.stops)).toBe(1);
    await page.evaluate(which=>{__merged.running=true;__toolbarTest[which].sync();},which);await caret.click();await input.fill('   ');await expect(caret).toBeHidden();await expect(root.locator('.composer-submit-menu')).toBeHidden();await expect(send).toBeHidden();await root.locator('.composer-submit-stop').click();expect(await page.evaluate(()=>__merged.stops)).toBe(2);await expect(send).toBeVisible();await expect(send).toHaveAttribute('aria-disabled','true');
    await input.fill('新的草稿');await send.click();expect(await page.evaluate(()=>__merged.writes.at(-1).mode)).toBe('send');
  }
});
test('merged choices follow capabilities, pending state and payloads without widening a 320px composer',async({page})=>{
  await page.setViewportSize({width:320,height:844});
  await page.evaluate(()=>{const t=__toolbarTest.main;t.submitControl.getState=()=>({running:true,hasDraft:true,pending:false});t.controls.sendMode.disabled=false;t.controls.sendMode.querySelector('[value=queue]').disabled=true;t.controls.sendMode.querySelector('[value=steer]').disabled=false;t.submitControl.stop.hidden=true;t.sync();});
  await expect(page.locator('#send')).toContainText('补充任务');await page.locator('#composer .composer-submit-caret').click();await expect(page.locator('#composer [data-send-mode=queue]')).toBeDisabled();await expect(page.locator('#composer .composer-submit-menu-stop')).toBeDisabled();
  const fits=await page.locator('#composer .composer-submit-menu').evaluate(menu=>{const a=menu.getBoundingClientRect(),b=menu.closest('form').getBoundingClientRect();return a.left>=b.left&&a.right<=b.right;});expect(fits).toBe(true);
  await page.keyboard.press('Escape');await expect(page.locator('#composer .composer-submit-menu')).toBeHidden();
  await page.evaluate(()=>{const t=__toolbarTest.main;t.submitControl.getState=()=>({running:true,hasDraft:true,pending:true});t.sync();});await expect(page.locator('#composer .composer-submit-caret')).toBeDisabled();
  await page.evaluate(()=>{const t=__toolbarTest.main;t.submitControl.getState=()=>({running:true,hasDraft:true,pending:false});t.sync();});await page.locator('#composer .composer-submit-caret').click();await page.locator('#composer .toolbar-more').click();await expect(page.locator('#composer .composer-submit-menu')).toBeHidden();
});
async function drag(page,id,zone,first=false){
  const source=page.locator('.toolbar-editor[open] [data-drag-tool='+id+']'),target=page.locator('.toolbar-editor[open] [data-drop-zone='+zone+']');
  await source.scrollIntoViewIfNeeded();const from=await source.boundingBox();await page.mouse.move(from.x+from.width/2,from.y+from.height/2);await page.mouse.down();await page.waitForTimeout(380);
  const to=await target.boundingBox();await page.mouse.move(to.x+(first?8:to.width-8),to.y+to.height/2,{steps:8});await expect(page.locator('.toolbar-drop-marker')).toHaveCount(1);await page.mouse.up();
  await expect(target.locator('[data-drag-tool='+id+']')).toHaveCount(1);
}
test('mouse moves tools immediately without waiting for a long press',async({page})=>{
 await edit(page,false);
 const source=await page.locator('[data-drag-tool=skills]').boundingBox();
 await page.mouse.move(source.x+source.width/2,source.y+source.height/2);await page.mouse.down();
 await page.mouse.move(source.x+source.width/2,source.y+source.height/2-12);
 await expect(page.locator('.toolbar-drag-ghost')).toHaveCount(1);
 const target=await page.locator('[data-drop-zone=topLeft]').boundingBox();
 await page.mouse.move(target.x+target.width/2,target.y+target.height/2,{steps:8});await page.mouse.up();
 await expect(page.locator('[data-drop-zone=topLeft] [data-drag-tool=skills]')).toHaveCount(1);
});
test('lazy editor drags across rows and alignment groups while preserving original controls and drafts',async({page})=>{
  expect(await page.evaluate(()=>performance.getEntriesByType('resource').some(r=>r.name.includes('composer-toolbar-editor.js')))).toBe(false);
  await edit(page);
  await drag(page,'model','topLeft');await drag(page,'model','topRight');await drag(page,'model','bottomRight');await drag(page,'model','more');await drag(page,'model','bottomLeft');
  await drag(page,'model','bottomLeft',true);
  expect(await page.locator('[data-drop-zone=bottomLeft] [data-drag-tool]').evaluateAll(nodes=>nodes.map(n=>n.dataset.dragTool))).toEqual(['model','attachments']);
  const preserved=await page.evaluate(()=>({sameModel:__toolbarTest.main.controls.model===__toolbarTest.model,sameSelect:__toolbarTest.main.controls.sendMode===__toolbarTest.select,text:document.getElementById('message').value,selection:[document.getElementById('message').selectionStart,document.getElementById('message').selectionEnd]}));
  expect(preserved).toEqual({sameModel:true,sameSelect:true,text:'保留草稿与光标',selection:[2,4]});
  await page.waitForTimeout(510);await page.locator('.toolbar-editor > .picker-head .primary').click();await expect(page.locator('.toolbar-editor')).not.toBeVisible();
  const result=await page.evaluate(()=>({layout:__toolbarTest.main.layout,other:__toolbarTest.other.layout,editorRequests:performance.getEntriesByType('resource').filter(r=>r.name.includes('composer-toolbar-editor.js')).length}));
  expect(result.layout.bottomLeft).toEqual(['model','attachments']);expect(result.other).toEqual(result.layout);expect(result.editorRequests).toBe(1);
  await page.reload();await page.waitForFunction(()=>document.getElementById('bootstrap-status')?.hidden);
  expect(await page.evaluate(()=>composerToolbar.layout.bottomLeft)).toEqual(['model','attachments']);
});
test('closing retains immediate edits, reset restores defaults and keyboard editing preserves disabled capabilities',async({page})=>{
  await edit(page);const original=await page.evaluate(()=>structuredClone(__toolbarTest.main.layout));
  const skill=page.locator('[data-drag-tool=skills]');await skill.focus();await skill.press('ArrowDown');await page.locator('[data-drag-tool=skills]').press('Shift+ArrowRight');
  await expect(page.locator('[data-drop-zone=bottomRight] [data-drag-tool=skills]')).toHaveCount(1);
  await page.locator('.toolbar-editor > .picker-head .primary').click();
  expect(await page.evaluate(()=>__toolbarTest.main.layout.bottomRight)).toContain('skills');
  await edit(page);
  const customized=await page.evaluate(()=>structuredClone(__toolbarTest.main.layout));
  page.once('dialog',async dialog=>{expect(dialog.type()).toBe('confirm');await dialog.dismiss();});
  await page.locator('.toolbar-editor [data-composer-preset=default]').click();
  expect(await page.evaluate(()=>__toolbarTest.main.layout)).toEqual(customized);
  page.once('dialog',dialog=>dialog.accept());await page.locator('.toolbar-editor [data-composer-preset=default]').click();
  expect(await page.evaluate(()=>__toolbarTest.main.layout)).toEqual(original);
  await page.locator('.toolbar-editor > .picker-head .primary').click();
  await page.evaluate(()=>{const t=__toolbarTest.main;t.controls.permissions.disabled=true;const layout=structuredClone(t.layout);layout.topLeft=layout.topLeft.filter(id=>id!=='permissions');layout.more.push('permissions');t.save(layout);});
  await page.locator('#composer .toolbar-more').click();await expect(page.locator('[data-menu-tool=permissions]')).toBeDisabled();
});
test('More shows only stored tools and follows moves into and out of either toolbar',async({page})=>{
  await page.locator('#composer .toolbar-more').click();
  await expect(page.locator('[data-menu-tool=model]')).toHaveCount(0);
  await expect(page.locator('[data-menu-tool=skills]')).toHaveCount(1);
  await expect(page.locator('[data-menu-tool=plan]')).toHaveCount(1);
  await page.locator('.toolbar-menu[open] .toolbar-edit-button').click();
  await drag(page,'plan','topLeft');await drag(page,'model','more');
  await page.waitForTimeout(510);await page.locator('.toolbar-editor > .picker-head .primary').click();
  await page.locator('#composer .toolbar-more').click();
  await expect(page.locator('[data-menu-tool=plan]')).toHaveCount(0);
  await expect(page.locator('[data-menu-tool=model]')).toHaveCount(1);
  await page.locator('.toolbar-menu[open] .toolbar-edit-button').click();
  await drag(page,'plan','bottomRight');await drag(page,'model','bottomLeft');
  await page.waitForTimeout(510);await page.locator('.toolbar-editor > .picker-head .primary').click();
  await page.locator('#composer .toolbar-more').click();
  await expect(page.locator('[data-menu-tool=plan]')).toHaveCount(0);
  await expect(page.locator('[data-menu-tool=model]')).toHaveCount(0);
});
test('pinned and More controls use original mode/draft handlers, active work mode remains visible',async({page})=>{
  await page.evaluate(()=>{
    __toolbarTest.select.disabled=false;__toolbarTest.select.addEventListener('change',()=>window.__toolbarMode=__toolbarTest.select.value);
    const t=__toolbarTest.main;t.submitControl.getState=()=>({running:true,hasDraft:true});t.controls.sendMode.querySelector('[value=queue]').disabled=false;t.sync();
  });
  await page.locator('#composer .composer-submit-caret').click();await page.locator('#composer [data-send-mode=queue]').click();
  expect(await page.evaluate(()=>__toolbarMode)).toBe('queue');
  await page.evaluate(()=>workModes.render({host:'local',capabilities:{plan:true,goal:true}},'send',false));
  await page.locator('#composer .toolbar-more').click();await page.locator('[data-menu-tool=plan]').click();await expect(page.locator('#composer .toolbar-mode-tag')).toContainText('计划模式');
  await page.locator('#composer .toolbar-mode-tag').click();expect(await page.evaluate(()=>document.getElementById('work-mode').value)).toBe('default');
  expect(await page.evaluate(()=>document.getElementById('message').value)).toBe('保留草稿与光标');
});
test('narrow, landscape and wide panes keep toolbars within their container after customization',async({page})=>{
  await page.evaluate(()=>{const t=__toolbarTest.main;t.controls.model.querySelector('#model-name').textContent='A-very-long-provider-model-name';t.controls.context.hidden=false;});
  for(const width of [320,390,844,1440]){
    await page.setViewportSize({width,height:900});
    for(const language of ['zh','en']){
      await page.evaluate(language=>BridgeI18n.setLanguage(language),language);
      const fits=await page.locator('#composer').evaluate(form=>form.scrollWidth<=form.clientWidth+1);expect(fits,'composer width '+width+' '+language).toBe(true);
    }
  }
});

test('touch can drag a selected tool in the single-row preset',async({page,browserName})=>{
 test.skip(browserName!=='chromium','Real touch input is dispatched through Chromium CDP.');
 await page.evaluate(()=>composerToolbar.preferences.applyPreset('single'));await edit(page,false);
 await page.locator('[data-drag-tool=skills]').click();await page.locator('[data-drag-tool=skills]').scrollIntoViewIfNeeded();
 const cdp=await page.context().newCDPSession(page);await cdp.send('Emulation.setTouchEmulationEnabled',{enabled:true});
 const from=await page.locator('[data-drag-tool=skills]').boundingBox();
 await cdp.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x:from.x+from.width/2,y:from.y+from.height/2}]});await page.waitForTimeout(380);
 await expect(page.locator('.toolbar-drag-ghost')).toHaveCount(1);
 const to=await page.locator('[data-drop-zone=bottomLeft]').boundingBox();
 await cdp.send('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:[{x:to.x+8,y:to.y+to.height/2}]});await page.waitForTimeout(50);
 await cdp.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});
 await expect(page.locator('[data-drop-zone=bottomLeft] [data-drag-tool=skills]')).toHaveCount(1);
});
test('touch long press moves a tool across rows and cancelled gestures leave it in place',async({page,browserName})=>{
  test.skip(browserName!=='chromium','Real touch input is dispatched through Chromium CDP.');
  await edit(page);const cdp=await page.context().newCDPSession(page);await cdp.send('Emulation.setTouchEmulationEnabled',{enabled:true});
  const from=await page.locator('[data-drag-tool=skills]').boundingBox();const to=await page.locator('[data-drop-zone=bottomRight]').boundingBox();
  await cdp.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x:from.x+from.width/2,y:from.y+from.height/2}]});await page.waitForTimeout(380);
  await cdp.send('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:[{x:to.x+to.width-8,y:to.y+to.height/2}]});await page.waitForTimeout(50);
  await cdp.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});
  await expect(page.locator('[data-drop-zone=bottomRight] [data-drag-tool=skills]')).toHaveCount(1);
  const moved=await page.locator('[data-drag-tool=skills]').boundingBox();
  await cdp.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x:moved.x+10,y:moved.y+10}]});await page.waitForTimeout(380);
  await cdp.send('Input.dispatchTouchEvent',{type:'touchCancel',touchPoints:[]});
  await expect(page.locator('.toolbar-drag-ghost')).toHaveCount(0);await expect(page.locator('[data-drop-zone=bottomRight] [data-drag-tool=skills]')).toHaveCount(1);
});

test('storage failure retains committed settings and keeps the editor open and attachment choices in More anchor to its fixed button',async({page})=>{
  await edit(page);
  await page.evaluate(()=>{Storage.prototype.setItem=function(){throw new DOMException('fixture','QuotaExceededError');};});
  await page.locator('[data-display-choice=both]').click();await expect(page.locator('.toolbar-editor[open]')).toBeVisible();await expect(page.locator('.toolbar-editor-status')).toContainText('保存失败');
  expect(await page.evaluate(()=>__toolbarTest.main.display.model)).toBe('text');await page.locator('.toolbar-editor > .picker-head .primary').click();
  const anchored=await page.evaluate(()=>{const t=__toolbarTest.main,layout=structuredClone(t.layout);layout.bottomLeft=layout.bottomLeft.filter(id=>id!=='attachments');layout.more.push('attachments');t.layout=layout;t.apply();return t.anchor('attachments')===t.moreButton;});
  expect(anchored).toBe(true);
});

test('per-tool styles persist immediately and preserve the native select',async({page})=>{
  await edit(page);
  for(const id of ['model','skills','permissions','context']){
    await page.locator('[data-drag-tool='+id+']').click();
    for(const mode of ['icon','text','both']){
      await page.locator('[data-display-choice='+mode+']').click();
      expect(await page.locator('#composer [data-tool='+id+']').getAttribute('data-display')).toBe(mode);
      const count=await page.locator('.toolbar-display-preview svg').count();expect(count).toBe(mode==='text'?0:1);
      expect(await page.locator('.toolbar-display-preview span').count()).toBe(mode==='icon'?0:1);
      if(id==='model'&&mode!=='text'){const size=await page.locator('#model-button>svg').evaluate(icon=>({width:icon.getBoundingClientRect().width,height:icon.getBoundingClientRect().height}));expect(size).toEqual({width:18,height:18});}
    }
  }
  await page.locator('.toolbar-editor > .picker-head .primary').click();
  expect(await page.evaluate(()=>__toolbarTest.main.controls.sendMode===__toolbarTest.select)).toBe(true);
  await page.evaluate(()=>{__toolbarTest.main.controls.context.hidden=false;__toolbarTest.main.controls.context.title='上下文使用量 · 42%';__toolbarTest.main.sync();});
  await expect(page.locator('#composer [data-tool=context] .toolbar-copy')).toContainText('42%');
  for(const width of [320,390,844]){await page.setViewportSize({width,height:900});expect(await page.locator('#composer').evaluate(form=>form.scrollWidth<=form.clientWidth+1)).toBe(true);}
  await page.evaluate(()=>{__toolbarTest.select.disabled=false;__toolbarTest.select.addEventListener('change',()=>window.__toolbarStyleMode=__toolbarTest.select.value);});
  await expect(page.locator('#send-mode')).toBeHidden();
  expect(await page.evaluate(()=>__toolbarTest.other.display)).toEqual(await page.evaluate(()=>__toolbarTest.main.display));
  await edit(page);await page.locator('[data-display-choice=icon]').click();await page.locator('.toolbar-editor > .picker-head .primary').click();
  expect(await page.evaluate(()=>__toolbarTest.main.display.model)).toBe('icon');
  expect(await page.evaluate(()=>document.getElementById('message').value)).toBe('保留草稿与光标');
  await page.reload();await page.waitForFunction(()=>document.getElementById('bootstrap-status')?.hidden);
  expect(await page.evaluate(()=>composerToolbar.display.sendMode)).toBe('text');expect(await page.evaluate(()=>composerToolbar.display.model)).toBe('icon');
});

test.describe('touch composer geometry',()=>{
  test.use({hasTouch:true,isMobile:true});
  test('touch collapse leaves a clean compact toolbar without a lingering hover background',async({page})=>{
    await page.evaluate(()=>{document.documentElement.classList.add('bridge-mobile');document.getElementById('message').disabled=false;});
    for(const theme of ['light','dark'])for(const width of [320,400]){
      await page.setViewportSize({width,height:900});await page.evaluate(theme=>document.documentElement.dataset.theme=theme,theme);
      const toggle=page.locator('#composer [data-composer-tool=collapse]');await page.locator('#message').tap();await toggle.tap();
      await expect(page.locator('#composer-body')).toBeHidden();
      expect(await toggle.evaluate(button=>getComputedStyle(button).backgroundColor)).toBe('rgba(0, 0, 0, 0)');
      expect(await toggle.evaluate(button=>getComputedStyle(button).outlineStyle)).toBe('none');
      expect(await page.locator('#composer .toolbar-top').evaluate(bar=>getComputedStyle(bar).marginBottom)).toBe('0px');
      await toggle.tap();await expect(page.locator('#composer-body')).toBeVisible();
      expect(await toggle.evaluate(button=>getComputedStyle(button).backgroundColor)).toBe('rgba(0, 0, 0, 0)');
    }
  });
  test('real model and effort stay on one bottom row on a native mobile host',async({page})=>{
    await page.evaluate(()=>{
      document.documentElement.classList.add('bridge-mobile');document.documentElement.dataset.theme='dark';
      const main=__toolbarTest.main,other=__toolbarTest.other;
      other.form.style.width='calc(100% - 28px)';document.body.append(other.form);
      for(const toolbar of [main,other]){
        toolbar.controls.model.querySelector('#model-name,.ds-model-name').textContent='gpt-6.1-sol';
        const effort=toolbar.controls.model.querySelector('#model-effort,.ds-model-effort');effort.textContent='· medium';effort.hidden=false;
        toolbar.controls.context.hidden=false;toolbar.controls.context.title='上下文使用量 · 42%';
        toolbar.controls.sendMode.disabled=false;toolbar.controls.model.hidden=false;
      }
    });
    for(const width of [320,390,400,844]){
      await page.setViewportSize({width,height:900});
      for(const which of ['main','other']){
        const geometry=await page.evaluate(which=>{
          const t=__toolbarTest[which],bar=t.bottom.getBoundingClientRect();
          const rect=node=>{const r=node.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height,center:r.y+r.height/2};};
          return {barHeight:bar.height,controls:[t.controls.attachments,t.controls.model,t.submitControl.root].map(rect),fits:t.form.scrollWidth<=t.form.clientWidth+1,touch:matchMedia('(pointer:coarse)').matches};
        },which);
        expect(geometry.touch).toBe(true);expect(geometry.fits,which+' '+width).toBe(true);
        expect(geometry.barHeight,which+' '+width).toBeLessThanOrEqual(52);
        expect(Math.max(...geometry.controls.map(r=>r.center))-Math.min(...geometry.controls.map(r=>r.center)),which+' '+width).toBeLessThanOrEqual(1);
      }
    }
    await page.setViewportSize({width:320,height:900});
    await page.evaluate(()=>{const t=__toolbarTest.main;for(const id of Object.keys(t.display))t.display[id]='icon';t.layout={topLeft:composerTools.map(tool=>tool.id),topRight:[],bottomLeft:[],bottomRight:[],more:[]};t.apply();});
    expect(await page.locator('#composer').evaluate(form=>form.scrollWidth<=form.clientWidth+1)).toBe(true);
    expect(await page.locator('#composer .toolbar-top').evaluate(bar=>bar.getBoundingClientRect().height)).toBeLessThanOrEqual(48);
  });
});

test('hide composer defaults to the top right and remains customizable with draft and cursor intact',async({page})=>{
  const top=page.locator('#composer .toolbar-top [data-composer-tool=collapse]');await expect(top).toBeVisible();
  await expect(top.locator('svg')).toHaveAttribute('data-icon','collapse');expect(await top.locator('svg').evaluate(icon=>getComputedStyle(icon).transform)).toBe('none');
  await top.click();await expect(page.locator('#composer-body')).toBeHidden();await expect(page.locator('#composer .toolbar-restore')).toBeHidden();
  await expect.poll(()=>top.locator('svg').evaluate(icon=>{const transform=new DOMMatrix(getComputedStyle(icon).transform);return [Math.round(transform.a),Math.round(transform.d)];})).toEqual([-1,-1]);
  expect(await page.locator('#message').inputValue()).toBe('保留草稿与光标');
  await top.click();await expect(page.locator('#composer-body')).toBeVisible();
  expect(await page.locator('#message').evaluate(input=>[input.selectionStart,input.selectionEnd])).toEqual([2,4]);
  await edit(page);await page.locator('[data-drag-tool=collapse]').focus();await page.locator('[data-drag-tool=collapse]').press('ArrowDown');await page.locator('[data-drag-tool=collapse]').press('Shift+ArrowRight');await page.locator('.toolbar-editor > .picker-head .primary').click();
  await page.locator('#composer .toolbar-bottom [data-composer-tool=collapse]').click();await expect(page.locator('#composer-body')).toBeHidden();await expect(page.locator('#composer .toolbar-restore')).toBeVisible();
  await page.locator('#composer .toolbar-restore').click();await expect(page.locator('#composer-body')).toBeVisible();
  await page.evaluate(()=>{__toolbarTest.main.setCollapsed(true);BridgePresentation.showError();});await expect(page.locator('#composer-body')).toBeVisible();
  await page.evaluate(()=>{const t=__toolbarTest.main;const layout=structuredClone(t.layout);layout.bottomRight=layout.bottomRight.filter(id=>id!=='collapse');layout.more.push('collapse');t.save(layout);});
  await page.locator('#composer .toolbar-more').click();await page.locator('[data-menu-tool=collapse]').click();await expect(page.locator('#composer-body')).toBeHidden();
  await page.locator('#composer .toolbar-more').click();await expect(page.locator('[data-menu-tool=collapse]')).toHaveText('展开输入区');expect(await page.locator('[data-menu-tool=collapse] svg').evaluate(icon=>{const transform=new DOMMatrix(getComputedStyle(icon).transform);return [Math.round(transform.a),Math.round(transform.d)];})).toEqual([-1,-1]);await page.keyboard.press('Escape');
  await page.locator('#composer .toolbar-restore').click();await expect(page.locator('#composer-body')).toBeVisible();
  const shared=await page.evaluate(()=>{const t=__toolbarTest.other;t.input.value='其他客户端草稿';t.input.setSelectionRange(1,3);t.setCollapsed(true);const hidden=t.body.hidden&&!t.restoreButton.hidden;const mainVisible=!__toolbarTest.main.body.hidden;t.restoreButton.click();return {hidden,mainVisible,restored:!t.body.hidden,text:t.input.value,selection:[t.input.selectionStart,t.input.selectionEnd]};});
  expect(shared).toEqual({hidden:true,mainVisible:true,restored:true,text:'其他客户端草稿',selection:[1,3]});
});

test('send modes, model details, context compaction and attachment sources remain accessible',async({page})=>{
  await expect(page.locator('#send-mode')).toBeHidden();expect(await page.locator('#send-mode option').allTextContents()).toEqual(['发送新消息','完成后发送','补充当前任务']);
  await page.evaluate(()=>{document.getElementById('model-name').textContent='gpt-6.1-sol';const effort=document.getElementById('model-effort');effort.textContent='· medium';effort.hidden=false;
    contextUsageControl.update({id:'audit',connected:true,contextUsage:{usedTokens:84000,contextWindow:200000},capabilities:{compact:true}});contextUsageControl.onCompact=async()=>{window.__auditCompact=(window.__auditCompact||0)+1;return true;};__toolbarTest.main.sync();});
  await expect(page.locator('#model-name')).toBeVisible();await expect(page.locator('#model-effort')).toBeVisible();await expect(page.locator('#composer .context-usage-ring')).toBeVisible();
  await page.locator('#composer [data-composer-tool=context]').click();await expect(page.locator('dialog[open]')).toContainText('42.0%');await page.getByRole('button',{name:'压缩上下文',exact:true}).click();expect(await page.evaluate(()=>__auditCompact)).toBe(1);
  await page.evaluate(()=>{attachments.open('audit');attachments.canUpload=()=>true;attachments.setLocked(false);});await page.locator('#composer [data-composer-tool=attachments]').click();
  await expect(page.locator('.attachment-menu[open]')).toContainText('从本机上传');await expect(page.locator('.attachment-menu[open]')).toContainText('引用宿主机项目文件');
});

test('native permission modes keep distinct icons, real labels and original controls across clients',async({page})=>{
  for(const [which,states] of [['main',[['ask','请求批准'],['auto-review','帮我批准'],['full-access','完全访问']]],['other',[['default','默认权限'],['acceptEdits','允许文件编辑'],['plan','计划模式'],['bypassPermissions','完全访问']]],['other',[['desktop','电脑端处理'],['mobile-ask','手机端审批'],['auto-allow','自动允许']]],['other',[['build','变更前确认'],['guarded','自主模式'],['yolo','完全访问']]]]){
    const paths=[];
    for(const [mode,label] of states){
      await page.evaluate(({which,mode,label})=>{const b=__toolbarTest[which].controls.permissions;b.dataset.permissionMode=mode;b.textContent=label;__toolbarTest[which].sync();}, {which,mode,label});
      await expect.poll(()=>page.evaluate(which=>__toolbarTest[which].controls.permissions.querySelector('svg')?.dataset.permissionMode,which)).toBe(mode);
      const actual=await page.evaluate(which=>{const b=__toolbarTest[which].controls.permissions;return {path:Array.from(b.querySelectorAll('path'),p=>p.getAttribute('d')).join(' '),label:b.getAttribute('aria-label')};},which);expect(actual.label).toBe(label);paths.push(actual.path);
    }
    expect(new Set(paths).size).toBe(states.length);
  }
});

test.describe('toolbar layout audit',()=>{
  test.use({hasTouch:true,isMobile:true});
  test.beforeEach(async({page})=>{
    await page.setViewportSize({width:320,height:900});
    await page.evaluate(()=>{
      document.documentElement.classList.add('bridge-mobile');
      const other=__toolbarTest.other;other.form.style.width='calc(100% - 28px)';document.body.append(other.form);
      for(const t of [__toolbarTest.main,other]){
        t.workMode.hidden=false;t.workMode.disabled=false;for(const option of t.workMode.options)option.disabled=false;
        for(const control of Object.values(t.controls)){control.hidden=false;control.disabled=false;}
        t.controls.model.querySelector('#model-name,.ds-model-name').textContent='gpt-6.1-sol';const effort=t.controls.model.querySelector('#model-effort,.ds-model-effort');effort.textContent='· medium';effort.hidden=false;t.apply();
      }
    });
  });
  test('editor keeps close and reset reachable in landscape and a short keyboard viewport',async({page})=>{
    for(const [width,height,save] of [[844,390,true],[400,360,false]]){
      await page.setViewportSize({width,height});await edit(page);await page.locator('.toolbar-display-settings').scrollIntoViewIfNeeded();
      const bounds=await page.locator('.toolbar-editor').evaluate(dialog=>{const head=dialog.querySelector('.picker-head').getBoundingClientRect(),rect=dialog.getBoundingClientRect();return {top:head.top-rect.top,bottom:rect.bottom-head.bottom};});
      expect(bounds.top).toBeGreaterThanOrEqual(0);expect(bounds.bottom).toBeGreaterThanOrEqual(0);
      await page.locator('[data-display-choice='+(save?'both':'icon')+']').click();await page.locator('.toolbar-editor > .picker-head .primary').click();
      expect(await page.evaluate(()=>__toolbarTest.main.display.model)).toBe(save?'both':'icon');
    }
  });
  test('empty left sides keep right tools and send anchored, and packed right tools remain reachable in every style',async({page})=>{
    for(const which of ['main','other']){
      const anchors=await page.evaluate(which=>{
        const t=__toolbarTest[which],ids=composerTools.map(tool=>tool.id);t.layout={topLeft:[],topRight:['context','collapse'],bottomLeft:[],bottomRight:[],more:ids.filter(id=>!['context','collapse'].includes(id))};t.apply();
        return {top:t.tools.getBoundingClientRect().right,right:t.groups.topRight.getBoundingClientRect().right,bottom:t.bottom.getBoundingClientRect().right,send:t.actions.getBoundingClientRect().right};
      },which);
      expect(Math.abs(anchors.top-anchors.right)).toBeLessThanOrEqual(1);expect(Math.abs(anchors.bottom-anchors.send)).toBeLessThanOrEqual(1);
      for(const style of ['icon','text','both']){
        const packed=await page.evaluate(({which,style})=>{
          const t=__toolbarTest[which],ids=composerTools.map(tool=>tool.id);t.layout={topLeft:[],topRight:ids,bottomLeft:[],bottomRight:[],more:[]};for(const id of ids)t.display[id]=style;t.apply();
          const group=t.groups.topRight,bounds=group.getBoundingClientRect();group.scrollLeft=0;const first=t.wrappers[ids[0]].getBoundingClientRect().left;group.scrollLeft=group.scrollWidth;const last=t.wrappers[ids.at(-1)].getBoundingClientRect().right;
          return {firstOffset:first-bounds.left,lastOffset:last-bounds.right,scrollable:group.scrollWidth>group.clientWidth,height:t.tools.getBoundingClientRect().height,fits:t.form.scrollWidth<=t.form.clientWidth+1,
            targets:style==='icon'?ids.map(id=>{const r=t.controls[id].getBoundingClientRect();return [id,r.width,r.height];}):[]};
        },{which,style});
        expect(packed.firstOffset,which+' '+style).toBeGreaterThanOrEqual(-1);expect(packed.lastOffset,which+' '+style).toBeLessThanOrEqual(1);expect(packed.scrollable).toBe(true);expect(packed.height).toBeLessThanOrEqual(48);expect(packed.fits).toBe(true);
        for(const [id,width,height] of packed.targets){expect(width,which+' '+id).toBeGreaterThanOrEqual(44);expect(height,which+' '+id).toBeGreaterThanOrEqual(44);}
      }
    }
  });
  test('live menu updates preserve button identity, keyboard focus and selected work mode',async({page})=>{
    for(const which of ['main','other']){
      await page.evaluate(which=>{const t=__toolbarTest[which];t.openMenu();window.__auditMenu=t.moreContent.querySelector('[data-menu-tool=plan]');__auditMenu.focus();t.workMode.value='plan';t.controls.model.title='native model refresh';t.sync();},which);
      await expect.poll(()=>page.evaluate(which=>{const t=__toolbarTest[which];return t.moreContent.querySelector('[data-menu-tool=plan]')===__auditMenu&&document.activeElement===__auditMenu;},which)).toBe(true);
      await expect(page.locator('.toolbar-menu[open] [data-menu-tool=plan]')).toHaveAttribute('aria-pressed','true');await page.evaluate(which=>__toolbarTest[which].menu.close(),which);
    }
  });
  test('merged running action fits a narrow composer',async({page})=>{
    for(const width of [320,400]){
      await page.setViewportSize({width,height:900});
      for(const which of ['main','other']){
        const sizes=await page.evaluate(which=>{const t=__toolbarTest[which];t.submitControl.getState=()=>({running:true,hasDraft:true});t.sync();return {height:t.bottom.getBoundingClientRect().height,fits:t.form.scrollWidth<=t.form.clientWidth+1,width:t.submitControl.root.getBoundingClientRect().width};},which);
        expect(sizes.fits).toBe(true);expect(sizes.height).toBeLessThanOrEqual(52);expect(sizes.width).toBeGreaterThanOrEqual(88);
      }
    }
  });
  test('new errors reveal a collapsed composer while ordinary refreshes keep it collapsed',async({page})=>{
    for(const which of ['main','other']){
      await page.evaluate(which=>{const t=__toolbarTest[which];t.input.value='保留错误前的草稿';t.input.setSelectionRange(1,3);t.setCollapsed(true);t.alerts[0].textContent='新的错误提示';},which);
      await expect.poll(()=>page.evaluate(which=>__toolbarTest[which].body.hidden,which)).toBe(false);
      expect(await page.evaluate(which=>{const t=__toolbarTest[which];return [t.input.value,t.input.selectionStart,t.input.selectionEnd];},which)).toEqual(['保留错误前的草稿',1,3]);
      await page.evaluate(which=>{const t=__toolbarTest[which];t.setCollapsed(true);t.controls.model.title='ordinary refresh';t.sync();},which);
      await page.waitForFunction(which=>__toolbarTest[which].controls.model.getAttribute('aria-label')==='ordinary refresh',which);
      expect(await page.evaluate(which=>__toolbarTest[which].body.hidden,which)).toBe(true);
      await page.evaluate(which=>__toolbarTest[which].setCollapsed(false),which);
    }
  });
});


test('refreshes are idempotent and never dispatch changes or persist preferences',async({page})=>{
 const result=await page.evaluate(async()=>{
  const t=__toolbarTest.main;t.controls.sendMode.disabled=false;for(const o of t.controls.sendMode.options)o.disabled=false;
  t.submitControl.getState=()=>({running:true,hasDraft:true});t.sync();await Promise.resolve();
  let changes=0,writes=0,mutations=0;t.controls.sendMode.addEventListener('change',()=>changes++);
  const set=Storage.prototype.setItem;Storage.prototype.setItem=function(...args){writes++;return set.apply(this,args)};
  const observer=new MutationObserver(records=>mutations+=records.length);observer.observe(t.form,{attributes:true,childList:true,subtree:true,characterData:true});
  t.sync();t.sync();await Promise.resolve();observer.disconnect();return {changes,writes,mutations};
 });expect(result).toEqual({changes:0,writes:0,mutations:0});
});
test('More choices update while open and global preference survives a new document',async({page})=>{
 await page.evaluate(()=>{const t=__toolbarTest.main;t.controls.sendMode.disabled=false;for(const o of t.controls.sendMode.options)o.disabled=false;});
 await page.locator('#composer .toolbar-more').click();await page.locator('[data-menu-tool=sendMode]').click();
 await page.evaluate(()=>{const t=__toolbarTest.main;t.controls.sendMode.querySelector('[value=queue]').disabled=true;t.requestSync();});
 await expect(page.locator('.toolbar-choice-option').nth(1)).toBeDisabled();
 await page.locator('.toolbar-choice-option').nth(2).click();expect(await page.evaluate(()=>composerToolbar.preferences.value.followUpMode)).toBe('steer');
 await page.reload();await page.waitForFunction(()=>document.getElementById('bootstrap-status')?.hidden);
 expect(await page.evaluate(()=>({main:composerToolbar.preferences.value.followUpMode,other:desktopSessions.toolbar.preferences.value.followUpMode}))).toEqual({main:'steer',other:'steer'});
});
test('editor updates language, supports explicit move and resets layout without losing preference or draft',async({page})=>{
 await page.evaluate(()=>__toolbarTest.main.preferences.update({followUpMode:'steer'}));await edit(page);
 await page.locator('[data-drag-tool=model]').click();await page.locator('[data-move-zone=topRight]').click();
 await expect(page.locator('[data-drop-zone=topRight] [data-drag-tool=model]')).toHaveCount(1);
 await page.locator('[data-display-choice=both]').click();await expect(page.locator('.toolbar-preview-details,.toolbar-editor-subtitle,.toolbar-editor-body>p')).toHaveCount(0);
 expect(await page.locator('#model-button').count()).toBe(1);
 await page.evaluate(()=>BridgeI18n.setLanguage('en'));await expect(page.locator('.toolbar-editor h2')).toHaveText('Customize toolbars');await expect(page.locator('.toolbar-editor > .picker-head .primary')).toHaveAttribute('aria-label','Close');await expect(page.locator('.toolbar-editor [data-composer-preset=default]')).toHaveText('Dual-row defaults');
 page.once('dialog',dialog=>dialog.accept());await page.locator('.toolbar-editor [data-composer-preset=default]').click();await page.locator('.toolbar-editor > .picker-head .primary').click();
 expect(await page.evaluate(()=>composerToolbar.layout)).toEqual(await page.evaluate(()=>defaultComposerLayout()));
 expect(await page.evaluate(()=>composerToolbar.preferences.value.followUpMode)).toBe('steer');await expect(page.locator('#message')).toHaveValue('保留草稿与光标');
});
test('disposing a toolbar invalidates an outstanding lazy editor open',async({page})=>{
 await page.evaluate(()=>{const original=BridgeFeatures.load.bind(BridgeFeatures);BridgeFeatures.load=async feature=>{await original(feature);if(feature==='composer-editor')await new Promise(resolve=>window.__releaseEditor=resolve)};window.__openingEditor=composerToolbar.edit();});
 await page.waitForFunction(()=>typeof __releaseEditor==='function');
 await page.evaluate(async()=>{composerToolbar.dispose();__releaseEditor();await __openingEditor;});
 await expect(page.locator('.toolbar-editor[open]')).toHaveCount(0);
});

test('same-origin windows synchronize settings into an open editor',async({page,context})=>{
 await edit(page);const other=await context.newPage();await other.goto('/?case=success');await other.waitForFunction(()=>document.getElementById('bootstrap-status')?.hidden);
 await other.evaluate(()=>{const t=composerToolbar,layout=structuredClone(t.layout);layout.bottomLeft=layout.bottomLeft.filter(id=>id!=='model');layout.topRight.push('model');t.save(layout,{...t.display,model:'both'});});
 await expect(page.locator('[data-drop-zone=topRight] [data-drag-tool=model]')).toHaveCount(1);
 await page.locator('[data-drag-tool=model]').click();await page.locator('[data-move-zone=more]').click();
 await expect.poll(()=>other.evaluate(()=>composerToolbar.layout.more.includes('model'))).toBe(true);await other.close();
});
test('touch swipe scrolls before long press without rearranging tools',async({page,browserName})=>{
 test.skip(browserName!=='chromium','CDP touch injection is Chromium-only');
 await page.setViewportSize({width:390,height:500});await edit(page);const cdp=await page.context().newCDPSession(page);await cdp.send('Emulation.setTouchEmulationEnabled',{enabled:true});
 const before=await page.evaluate(()=>JSON.stringify(composerToolbar.layout)),r=await page.locator('[data-drag-tool=permissions]').boundingBox();
 await cdp.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x:r.x+r.width/2,y:r.y+r.height/2}]});
 for(let step=1;step<=5;step++)await cdp.send('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:[{x:r.x+r.width/2,y:r.y+r.height/2-step*12}]});
 await cdp.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});
 await expect.poll(()=>page.locator('.toolbar-editor').evaluate(dialog=>dialog.scrollTop)).toBeGreaterThan(0);
 expect(await page.evaluate(()=>JSON.stringify(composerToolbar.layout))).toBe(before);await expect(page.locator('.toolbar-drag-ghost')).toHaveCount(0);
});

test('editor keeps preview aligned, touch targets usable and selected model current',async({page})=>{
 await page.setViewportSize({width:320,height:568});await edit(page);
 await expect(page.locator('.toolbar-editor-captions')).toHaveCount(0);
 await expect(page.locator('.toolbar-editor-row-bottom .toolbar-editor-captions')).toHaveCount(0);
 const geometry=await page.locator('.toolbar-editor').evaluate(dialog=>({
   rows:[...dialog.querySelectorAll('.toolbar-editor-row')].map(row=>{
     const zones=[...row.querySelectorAll('[data-drop-zone]')].map(n=>n.getBoundingClientRect());
     const captions=[...row.querySelectorAll('.toolbar-editor-captions span')].map(n=>n.getBoundingClientRect());
     const bounds=row.getBoundingClientRect();
     return {heightDelta:Math.abs(zones[0].height-zones[1].height),leftDelta:captions.length?Math.abs(captions[0].left-bounds.left):0,rightDelta:captions.length?Math.abs(captions[1].right-bounds.right):0};
   }),
   heights:[...dialog.querySelectorAll('.toolbar-display-choices button,.toolbar-move-choices button')].map(n=>n.getBoundingClientRect().height),
   emptyContent:getComputedStyle(dialog.querySelector('[data-drop-zone=bottomRight]'),'::after').content,
   overflow:dialog.scrollWidth>dialog.clientWidth
 }));
 for(const row of geometry.rows){expect(row.heightDelta).toBeLessThanOrEqual(1);expect(row.leftDelta).toBeLessThanOrEqual(1);expect(row.rightDelta).toBeLessThanOrEqual(1);}
 for(const height of geometry.heights)expect(height).toBeGreaterThanOrEqual(44);
 expect(geometry.emptyContent).not.toContain('+');expect(geometry.overflow).toBe(false);
 await page.locator('[data-drag-tool=skills]').scrollIntoViewIfNeeded();
 const scrollTop=await page.locator('.toolbar-editor').evaluate(n=>n.scrollTop);
 await page.locator('[data-drag-tool=skills]').click();
 await expect(page.locator('[data-drag-tool=skills]')).toHaveAttribute('aria-pressed','true');
 expect(await page.locator('.toolbar-editor').evaluate(n=>n.scrollTop)).toBe(scrollTop);
 await page.locator('[data-drag-tool=model]').click();
 await page.locator('[data-display-choice=both]').click();
 await page.evaluate(()=>{document.getElementById('model-name').textContent='updated-model';composerToolbar.sync();});
 await expect(page.locator('.toolbar-display-preview')).toContainText('updated-model');
});

test('add menu opens the original Skill picker, respects capabilities and rejects stale chat actions',async({page})=>{
 for(const which of ['main','other']){
  await page.evaluate(which=>{
   const t=__toolbarTest[which],a=which==='main'?attachments:desktopSessions.attachments;t.preferences.resetLayout();
   if(which==='other'){document.body.append(t.form);t.form.style.width='360px';desktopSessions.state={capabilities:{skills:true}};desktopSessions.canMutate=()=>true;}
   a.open('skill-menu-audit');a.canUpload=()=>false;a.setLocked(false);
   t.controls.skills.hidden=false;t.controls.skills.disabled=false;t.controls.skills.onclick=()=>{window.__skillOpens=(window.__skillOpens||0)+1;};t.sync();
  },which);
  const root=page.locator(which==='main'?'#composer':'.ds-composer');
  await root.locator('[data-composer-tool=attachments]').click();
  const choice=page.locator('.attachment-menu[open] button').filter({hasText:'Skill'});await expect(choice).toBeEnabled();await choice.click();
  expect(await page.evaluate(()=>__skillOpens)).toBe(which==='main'?1:2);await expect(page.locator('.attachment-menu[open]')).toHaveCount(0);
  await root.locator('.toolbar-more').click();await expect(page.locator('[data-menu-tool=skills]')).toHaveCount(1);await page.keyboard.press('Escape');
  await page.evaluate(which=>{__toolbarTest[which].controls.skills.disabled=true;__toolbarTest[which].sync();},which);
  await root.locator('[data-composer-tool=attachments]').click();await expect(choice).toBeDisabled();await page.keyboard.press('Escape');
  await page.evaluate(which=>{__toolbarTest[which].controls.skills.disabled=false;__toolbarTest[which].sync();},which);
  await root.locator('[data-composer-tool=attachments]').click();await page.evaluate(which=>(which==='main'?attachments:desktopSessions.attachments).open('changed-chat'),which);await choice.click();
  expect(await page.evaluate(()=>__skillOpens)).toBe(which==='main'?1:2);
  await page.evaluate(which=>{const t=__toolbarTest[which],layout=structuredClone(t.layout);layout.more=layout.more.filter(id=>id!=='skills');layout.topLeft.push('skills');t.save(layout);},which);
  await root.locator('.toolbar-more').click();await expect(page.locator('[data-menu-tool=skills]')).toHaveCount(0);await page.keyboard.press('Escape');
 }
});

test('Tools panel always matches every unpinned tool regardless of shortcuts or capabilities',async({page})=>{
 for(const which of ['main','other']){
  for(const placement of ['default','all','mixed']){
   const expected=await page.evaluate(({which,placement})=>{
    const t=__toolbarTest[which],ids=composerTools.map(tool=>tool.id),layout=defaultComposerLayout();
    if(which==='other'){document.body.append(t.form);t.form.style.width='360px';}
    if(placement!=='default'){
     for(const zone of composerZones)layout[zone]=[];
     layout.more=placement==='all'?ids:ids.slice(4);
     if(placement==='mixed'){layout.topLeft=ids.slice(0,2);layout.bottomRight=ids.slice(2,4);}
    }
    t.save(layout);t.workMode.hidden=true;t.sync();return t.layout.more;
   },{which,placement});
   const root=page.locator(which==='main'?'#composer':'.ds-composer');await root.locator('.toolbar-more').click();
   const actual=await page.locator('.toolbar-menu[open] [data-menu-tool]').evaluateAll(nodes=>nodes.map(node=>node.dataset.menuTool));expect(actual).toEqual(expected);
   if(expected.includes('goal'))await expect(page.locator('.toolbar-menu[open] [data-menu-tool=goal]')).toBeDisabled();
   await page.keyboard.press('Escape');
  }
 }
});

test('composer uses SVG action glyphs and permission icons keep their native weight',async({page})=>{
 await expect(page.locator('#send > svg')).toHaveAttribute('data-icon','send');await expect(page.locator('#send')).toHaveText('发送');
 await page.evaluate(()=>{const button=permissionOption(document.createElement('button'),'auto-review','帮我批准','',true);button.id='icon-weight-fixture';document.body.append(button);});
 await expect(page.locator('#icon-weight-fixture > svg')).toHaveAttribute('data-icon','permission-review');
 expect(await page.locator('#icon-weight-fixture > svg').evaluate(svg=>getComputedStyle(svg).stroke)).toBe('none');
 await expect(page.locator('#icon-weight-fixture .permission-check svg')).toHaveAttribute('data-icon','check');
 await page.evaluate(()=>{const t=composerToolbar;t.submitControl.getState=()=>({running:true,hasDraft:false});t.sync();});
 await expect(page.locator('#composer .composer-submit-stop > svg')).toHaveAttribute('data-icon','stop');
 await expect(page.locator('#composer .composer-submit-stop')).toHaveText('停止');
});

test('fixed submit supports three persisted display modes across action states and composers',async({page})=>{
 await page.evaluate(()=>{
  window.__submitStyleState={running:false,hasDraft:true};
  for(const t of Object.values(__toolbarTest).filter(value=>value instanceof ComposerToolbar)){
   t.submitControl.getState=()=>__submitStyleState;t.submitControl.send.disabled=false;t.submitControl.stop.disabled=false;
   t.controls.sendMode.disabled=false;for(const option of t.controls.sendMode.options)option.disabled=false;t.sync();
  }
 });
 await edit(page);const fixed=page.locator('[data-editor-tool=submit]');await fixed.click();
 await expect(page.locator('.toolbar-display-title').first()).toHaveText('发送按钮');await expect(page.locator('.toolbar-move-settings')).toBeHidden();
 await expect(fixed).not.toHaveAttribute('data-drag-tool');const layout=await page.evaluate(()=>structuredClone(composerToolbar.layout));
 for(const style of ['icon','text','both']){
  await page.locator('[data-display-choice='+style+']').click();
  for(const state of [{running:false,hasDraft:true},{running:true,hasDraft:true},{running:true,hasDraft:false}]){
   await page.evaluate(state=>{Object.assign(__submitStyleState,state);__toolbarTest.main.sync();__toolbarTest.other.sync();},state);
   for(const which of ['main','other']){
    const data=await page.evaluate(which=>{
     const t=__toolbarTest[which],s=t.submitControl,button=s.action().stop?s.stopButton:s.send;
     return {style:s.root.dataset.display,textVisible:getComputedStyle(button.querySelector('span')).display!=='none',iconVisible:getComputedStyle(button.querySelector('svg')).display!=='none',label:button.getAttribute('aria-label'),caretVisible:!s.caret.hidden,caretIconVisible:getComputedStyle(s.caret.querySelector('svg')).display!=='none',insideFixedActions:t.actions.contains(s.root),leftInLayout:Object.values(t.layout).some(ids=>ids.includes('submit'))};
    },which);
    expect(data.style).toBe(style);expect(data.textVisible).toBe(style!=='icon');expect(data.iconVisible).toBe(style!=='text');expect(data.label).toBeTruthy();
    expect(data.caretVisible).toBe(state.running&&state.hasDraft);expect(data.caretIconVisible).toBe(true);expect(data.insideFixedActions).toBe(true);expect(data.leftInLayout).toBe(false);
   }
  }
 }
 expect(await page.evaluate(()=>composerToolbar.layout)).toEqual(layout);
 await page.locator('[data-display-choice=icon]').click();await page.locator('.toolbar-editor-close').click();
 await page.reload();await page.waitForFunction(()=>document.getElementById('bootstrap-status')?.hidden);
 expect(await page.evaluate(()=>composerToolbar.display.submit)).toBe('icon');
 await page.evaluate(()=>{document.getElementById('login').hidden=true;document.getElementById('app').hidden=false;document.getElementById('app').classList.add('chat-open');document.getElementById('chat').hidden=false;});
 await edit(page);await page.locator('[data-editor-tool=submit]').click();page.once('dialog',dialog=>dialog.accept());await page.locator('.toolbar-editor [data-composer-preset=default]').click();
 expect(await page.evaluate(()=>composerToolbar.display.submit)).toBe('both');await expect(page.locator('.toolbar-move-settings')).toBeHidden();
});
