// Run on the isolated workbench fixture; never against a real user's chat.
async function runWorkbenchSettingsTests(){
 const wb=window.BridgeWorkbench,checks=[],old=BridgeI18n.language();
 const check=(ok,name)=>{if(!ok)throw Error(name);checks.push(name);};
 const change=value=>{$('phone-language').value=value;$('phone-language').onchange();};
 change('zh');wb.sideChat();const panel=wb.current.files.find(t=>t.id==='sidechat').sideChat;await panel.load();if(!panel.state.id)await panel.create();
 panel.input.value='保留侧边草稿';
 const body=()=>[...$('messages').querySelectorAll('.message-body')].map(n=>n.innerHTML).join('');const content=body(),parentModel=state.model;
 await panel.modelSettings();let dialog=[...document.querySelectorAll('dialog[open]')].at(-1);
 const selectors=dialog.querySelectorAll('select');selectors[0].value='__custom__';selectors[0].dispatchEvent(new Event('change'));dialog.querySelector('input').value='custom-model-draft';
 change('en');
 check(dialog.querySelectorAll('select').length===2,'Changing language preserves model controls');
 check(dialog.querySelector('input').value==='custom-model-draft','Custom model draft survives language change');
 check(panel.input.value==='保留侧边草稿','Side chat draft survives');
 check(body()===content,'Main chat contents survive');
 check(dialog.textContent.includes('Side chat settings'),'Side model dialog switches to English');dialog.close();
 wb.newTab();dialog=document.querySelector('.wb-new-tab');check(!/[\u4e00-\u9fff]/.test(dialog.textContent),'Every New tab choice is English');dialog.close();
 check($('send-mode').selectedOptions[0].textContent==='Send message'&&$('work-mode').options[0].textContent==='Default mode','Send and work mode labels are English');
 const raw=wb.raw(wb.node('span','','文件')),code=wb.node('code','','文件');panel.tab.body.append(raw,code);wb.relabel();check(raw.textContent==='文件'&&code.textContent==='文件','Data resembling a UI label is not translated');raw.remove();code.remove();
 const child=panel.state.id;panel.apply(await panel.request({action:'settings',id:child,model:'fixture-new',effort:'high'}));
 check(panel.state.effort==='high'&&panel.state.model==='fixture-new'&&state.model===parentModel,'Side settings do not alter the parent');
 change('zh');wb.select('chat');wb.select('sidechat');check(wb.tabs.textContent.includes('侧边聊天'),'Tab labels follow language after switching tabs');
 change(old);return [...checks,...await runWorkbenchFileLanguageTests(wb)];
}

async function runWorkbenchFileLanguageTests(wb=window.BridgeWorkbench){
 const checks=[],oldLanguage=BridgeI18n.language(),oldSession=wb.current,oldRequest=wb.request;
 const check=(ok,name)=>{if(!ok)throw Error(name);checks.push(name);};
 let total=1,session;
 try {
  BridgeI18n.setLanguage('zh');
  wb.request=async()=>({project:'文件',total,entries:total?[{name:'隐藏文件',path:'隐藏文件',kind:'file',size:10}]:[],nextOffset:null});
  wb.open('__file_language_fixture__','local');session=wb.current;
  const tab={id:'files',name:'文件',body:wb.node('div','wb-browser')};session.files.push(tab);
  await wb.directory(session,tab,'');wb.select('files');
  for(const language of ['en','zh','en']) {
   BridgeI18n.setLanguage(language);wb.relabel();
   check(tab.body.querySelector('label').textContent===(language==='en'?'Hidden files':'隐藏文件'),'Hidden files follows '+language);
   check(tab.status.textContent===(language==='en'?'1 items · Local project':'1 项 · 电脑上的项目'),'Directory count follows '+language);
   check(tab.body.querySelector('.wb-path').title===(language==='en'?'This chat’s project directory':'当前聊天的项目目录'),'Project tooltip follows '+language);
   check(tab.body.querySelector('.wb-path').textContent==='文件'&&tab.body.querySelector('.wb-file').title==='隐藏文件'&&tab.body.querySelector('.wb-file-name').textContent==='隐藏文件','User project and file labels stay unchanged in '+language);
  }
  await wb.directory(session,tab,'搜索');
  for(const language of ['zh','en']) {
   BridgeI18n.setLanguage(language);wb.relabel();
   check(tab.body.querySelector('.wb-path').textContent==='文件 / 搜索'&&tab.body.querySelector('.wb-path').title==='搜索','User directory and tooltip stay unchanged in '+language);
  }
  session.host='fixture-ssh';await wb.directory(session,tab,'');
  BridgeI18n.setLanguage('zh');wb.relabel();check(tab.status.textContent==='1 项 · SSH 项目','SSH directory summary switches to Chinese');
  BridgeI18n.setLanguage('en');wb.relabel();check(tab.status.textContent==='1 items · SSH project','SSH directory summary switches to English');
  total=0;await wb.directory(session,tab,'');
  BridgeI18n.setLanguage('zh');wb.relabel();check(tab.status.textContent==='当前目录没有匹配的文件','Empty directory switches to Chinese');
  BridgeI18n.setLanguage('en');wb.relabel();check(tab.status.textContent==='No matching files in this directory','Empty directory switches to English');
  return checks;
 } finally {
  wb.request=oldRequest;if(session)wb.sessions.delete(session.key);wb.current=oldSession;
  BridgeI18n.setLanguage(oldLanguage);wb.paint();wb.relabel();
 }
}
