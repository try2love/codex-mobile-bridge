// Isolated production UI: the callbacks below never reach a real Agent or model.
window.runCreationModelsBrowserChecks=async function(provider){
  const checks=[],calls=[],check=(condition,label)=>{if(!condition)throw Error(label);checks.push(label);};
  const root=document.createElement('div'),select=document.createElement('select');root.className='app';root.style.cssText='position:fixed;inset:0;background:var(--page);z-index:20';document.body.append(root);
  const surface=provider==='claude'?'code':'desktop',environment={key:'fixture-scope',surface,host:'local',name:provider,createRequiresMessage:provider!=='deepseek',createTitle:provider!=='zcode'};
  const catalog={models:[{id:'expensive-first',name:'Expensive first entry',efforts:['high']},
    {id:'preferred',name:'Saved model',efforts:['low','high'],defaultEffort:'low'},
    {id:'alternate',name:'Selected model',efforts:['low','high'],defaultEffort:'low'}],
    currentModel:'preferred',currentEffort:'low',cache:{identity:'fixture-account'},capabilities:{models:true,effort:true}};
  const view=new DesktopSessionsView({root,select,notify(){},onSelect(){},csrf:()=>'',onUnauthorized(){},request:async(url,body)=>{
    calls.push({url,body});
    if(url.endsWith('/projects'))return {createModels:true,projects:[{key:'fixture-project',name:'Project',cwd:'/fixture/project',environment:environment.key,surface,host:'local'}],environments:[environment],browseDirectories:false};
    if(url.includes('/create-models?'))return catalog;
    if(url.endsWith('/create'))return {status:'accepted',result:{id:'synthetic-created'}};
    return {sessions:[],connected:true};
  }});
  view.provider=provider;view.generation=1;view.open=async()=>{};
  await view.create();
  const dialog=[...document.querySelectorAll('dialog.project-picker')].find(node=>node.open&&node.id!=='folder-picker'),model=dialog.querySelector('[aria-label="模型"]'),effort=dialog.querySelector('[aria-label="思考强度"]');
  const until=async predicate=>{const end=performance.now()+2000;while(!predicate()){if(performance.now()>end)throw Error('Creation catalog did not become ready');await new Promise(resolve=>setTimeout(resolve,10));}};
  try{
    await until(()=>model.value==='preferred');
    check(effort.value==='low','The native saved effort is selected');check(model.value!=='expensive-first','The first catalog entry is never chosen by position');
    const refresh=[...dialog.querySelectorAll('button')].find(button=>button.textContent==='刷新模型');refresh.click();await until(()=>!refresh.disabled);
    check(calls.some(call=>call.url.includes('refresh=true')),'Manual refresh reaches the shared catalog route');
    model.value='alternate';model.dispatchEvent(new Event('change'));effort.value='high';effort.dispatchEvent(new Event('change'));
    check(model.labels.length>0&&effort.labels.length>0,'Both model fields keep visible labels');
    check(dialog.scrollWidth<=dialog.clientWidth+1,'The creation dialog has no horizontal overflow');
    for(const control of dialog.querySelectorAll('input,textarea,select,button')){
      if(!control.getClientRects().length)continue;
      const bounds=control.getBoundingClientRect(),outer=dialog.getBoundingClientRect();
      check(bounds.left>=outer.left-1&&bounds.right<=outer.right+1,'Creation controls fit within the dialog');
    }
    const cleanup=()=>{dialog.close();view.clear();root.remove();delete window.__creationModelsPreview;};
    window.__creationModelsPreview={dialog,checks,submit:async()=>{dialog.querySelector('textarea').value='FIRST';dialog.querySelector('form').requestSubmit();await until(()=>calls.some(call=>call.url.endsWith('/create')));const body=calls.find(call=>call.url.endsWith('/create')).body;check(body.model==='alternate'&&body.effort==='high'&&body.modelCatalogIdentity==='fixture-account','The selected model and effort travel with the first create request');check(provider==='deepseek'||body.firstMessage==='FIRST','The first input is included with its model choice');cleanup();return {passed:checks.length,checks};},cleanup};
    return {passed:checks.length,checks};
  }catch(error){dialog?.close();view.clear();root.remove();throw error;}
};
