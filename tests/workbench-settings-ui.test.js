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
 change(old);return checks;
}
