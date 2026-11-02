// Run in the isolated loopback bootstrap fixture. All provider requests below are synthetic.
window.runProjectCreationBrowserChecks=async function({language='zh',provider='claude',keepOpen=false,folder=false}={}){
  window.__projectCreationPreview?.cleanup();
  const calls=[],checks=[],previousLanguage=BridgeI18n.language(),check=(ok,label)=>{if(!ok)throw Error(label);checks.push(label);};
  BridgeI18n.setLanguage(language);BridgeI18n.apply();
  document.getElementById('login').hidden=true;
  const root=document.createElement('div'),select=document.createElement('select');root.className='app';root.style.cssText='position:fixed;inset:0;background:var(--page);z-index:10';document.body.append(root);
  for(const id of ['codex','claude','deepseek','zcode'])select.add(new Option(id,id));
  const environments=provider==='claude'?[
    {key:'code',name:'Claude Desktop · Code',surface:'code',host:'local',createRequiresMessage:true},
    {key:'cowork',name:'Claude Desktop · Cowork',surface:'cowork',host:'local',createRequiresMessage:true},
    {key:'cli-local',name:'Claude Code · 此电脑',surface:'cli',host:'local',createRequiresMessage:true},
    {key:'cli-ssh',name:'Claude Code · Research server',surface:'cli',host:'ssh:fixture',createRequiresMessage:true}
  ]:[{key:'desktop',name:(provider==='zcode'?'ZCode':'DSH')+' Desktop · 此电脑',surface:'desktop',host:'local',createRequiresMessage:provider==='zcode',createTitle:provider!=='zcode'}];
  const projects=[{key:'fixture-project',name:'mobile-bridge',cwd:'/Users/fixture/Documents/research/mobile-bridge',surface:environments[0].surface,environment:environments[0].key,host:'local'}];
  let folderPath='/home/fixture/projects';
  const view=new DesktopSessionsView({root,select,notify(){},onSelect(){},csrf:()=>'',onUnauthorized(){},request:async(url,body)=>{
    calls.push({url,body});
    if(url.includes('/projects/browse')){
      if(body.name)folderPath=body.path+'/'+body.name;else folderPath=body.path||folderPath;
      return {path:folderPath,home:'/home/fixture',parent:'/home/fixture',identity:'synthetic-folder',roots:[{name:'/',path:'/'}],directories:[{name:'mobile-bridge',path:folderPath+'/mobile-bridge'},{name:'research-and-evaluation-long-project-directory',path:folderPath+'/research-and-evaluation-long-project-directory'}]};
    }
    if(body)throw Error('Fixture must not submit a chat or register a project');
    if(url.includes('/projects'))return {environments,projects,browseDirectories:true};
    return {sessions:[],connected:true};
  }});
  let dialog;
  const cleanup=()=>{document.getElementById('folder-picker').close();dialog?.close();view.clear();root.remove();BridgeI18n.setLanguage(previousLanguage);BridgeI18n.apply();delete window.__projectCreationPreview;};
  try{
    await view.choose(provider);clearTimeout(view.timer);await view.create();dialog=[...document.querySelectorAll('dialog.project-picker')].find(node=>node.open&&node.id!=='folder-picker');
    check(!!dialog,'Creation dialog opens');check(!dialog.querySelector('input,textarea,select').matches(':focus'),'Opening keeps editable fields unfocused');
    const selects=dialog.querySelectorAll('select'),env=selects[0],project=selects[1],location=dialog.querySelector('.project-picker-location');
    check(project.value==='fixture-project','Existing project is selected by default');check(location.querySelector('summary').textContent==='research / mobile-bridge','Project path has a short summary');
    location.open=true;check(location.querySelector('p').textContent===projects[0].cwd,'Full path remains available');location.open=false;
    check(env.parentElement.hidden===(environments.length===1),'Single-environment clients hide the redundant selector');
    if(provider==='claude'){
      env.value='cli-ssh';env.onchange();check(dialog.querySelector('input').parentElement.hidden,'CLI does not ask for an unsupported title');check(dialog.querySelector('.primary').disabled,'An unselected directory cannot create a chat');
      await dialog.querySelector('.project-picker-browse').onclick();const picker=document.getElementById('folder-picker');
      check(picker.open,'All clients use the shared directory picker');check(document.getElementById('folder-picker-environment').textContent.includes('Research server'),'Directory picker identifies the selected environment');
      check(calls.at(-1).body.environment==='cli-ssh','Directory reads stay in the SSH environment');
      check(!document.getElementById('folder-picker-new-folder').open,'Folder creation is collapsed initially');
      document.getElementById('folder-picker-new-folder').open=true;document.getElementById('folder-picker-name').value='new-project';await document.getElementById('folder-picker-create').onclick();
      check(calls.at(-1).body.name==='new-project'&&calls.at(-1).body.identity==='synthetic-folder','Folder creation carries the chosen parent identity');
      check(!document.getElementById('folder-picker-new-folder').open,'A successful folder creation collapses the extra field');
      document.getElementById('folder-picker-select').onclick();check(project.value==='__directory__'&&!dialog.querySelector('.primary').disabled,'Selecting a folder readies the form without submitting it');
      dialog.querySelector('textarea').value=language==='en'?'Inspect the project structure.':'查看项目结构。';
      if(folder)await dialog.querySelector('.project-picker-browse').onclick();
    }
    if(provider==='zcode'){
      check(dialog.querySelector('input').parentElement.hidden,'ZCode uses its native generated title');
      await dialog.querySelector('.project-picker-browse').onclick();
      check(document.getElementById('folder-picker').open,'ZCode opens the shared folder picker');
      check(calls.at(-1).body.environment==='desktop','ZCode directory browsing keeps its environment');
      document.getElementById('folder-picker-select').onclick();
      check(project.value==='__directory__','ZCode can choose a new project directory');
      dialog.querySelector('textarea').value='Only reply OK';
    }
    const visible=[...document.querySelectorAll('dialog[open]')].at(-1);
    check(visible.scrollWidth<=visible.clientWidth+1,'Dialog content does not overflow horizontally');
    check(visible.getBoundingClientRect().left>=0&&visible.getBoundingClientRect().right<=innerWidth+1,'Dialog stays within the viewport');
    check(calls.every(call=>!call.url.includes('/register')&&!call.url.endsWith('/create')),'No project registration or chat is submitted');
    window.__projectCreationPreview={view,root,dialog,calls,cleanup};
    const result={width:innerWidth,language,provider,passed:checks.length,checks};
    if(!keepOpen)cleanup();return result;
  }catch(error){cleanup();throw error;}
};
