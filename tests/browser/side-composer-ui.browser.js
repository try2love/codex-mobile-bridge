// Isolated workbench fixture only; never run against personal chats.
async function runSideComposerTests(){
 const wb=window.BridgeWorkbench,checks=[],check=(ok,label)=>{if(!ok)throw Error(label);checks.push(label);};
 const wait=async fn=>{for(let i=0;i<160;i++){if(fn())return;await new Promise(r=>setTimeout(r,25));}throw Error('Side composer did not settle');};
 wb.sideChat();const panel=wb.current.files.find(t=>t.id==='sidechat').sideChat;await panel.load();
 const original=window.confirm;try{window.confirm=()=>true;if(panel.state.id)await panel.end();}finally{window.confirm=original;}
 check(panel.form.hidden&&!panel.landing.hidden&&!panel.createButton.disabled,'Uncreated side chat shows a usable creation screen, not disabled composer controls');
 await panel.create();check(!panel.form.hidden&&panel.landing.hidden,'Creating the chat replaces the landing with the full composer');
 check(panel.form.querySelectorAll('select').length===2,'Send and work modes are available');
 check([...panel.sendMode.options].map(o=>o.value).join(',')==='send,queue,steer','Same three send modes as main chat');
 check([...panel.workMode.options].map(o=>o.value).join(',')==='default,plan','Side chat has only Default and Plan modes');
 check(document.querySelector('#work-mode [value=goal]'),'Main chat retains Goal mode');
 check(panel.input.getBoundingClientRect().height===document.querySelector('#message').getBoundingClientRect().height,'Empty input height matches main chat');
 await panel.skills();let dialog=[...document.querySelectorAll('dialog[open]')].at(-1);
 check(dialog.textContent.includes('Review a change and explain the findings.'),'Skill picker shows descriptions');
 const checkbox=dialog.querySelector('input[type=checkbox]');checkbox.click();dialog.close();
 check(panel.skillPills.textContent.includes('Review')&&panel.skillsButton.textContent.includes('(1)'),'Selected Skill is visible in the composer');
 const mainPermission=state.permissionMode;panel.permissions();dialog=[...document.querySelectorAll('dialog[open]')].at(-1);
 [...dialog.querySelectorAll('.skill-option')].find(button=>button.textContent.includes(BridgeI18n.t('帮我批准'))).click();await wait(()=>!dialog.open);
 check(panel.state.permissionMode==='auto-review'&&state.permissionMode===mainPermission,'Side permissions update independently of main chat');
 panel.input.value='side draft';panel.collapse(true);check(panel.composerBody.hidden&&panel.input.value==='side draft','Collapse preserves side draft');panel.collapse(false);
 panel.workMode.value='plan';panel.workMode.dispatchEvent(new Event('change'));panel.sendMode.value='queue';panel.sendMode.dispatchEvent(new Event('change'));await panel.send();
 await wait(()=>!panel.pending);await panel.load();
 check(panel.input.value===''&&panel.selectedSkills.size===0,'Queued submission clears accepted draft and Skill selection');
 await wait(()=>panel.state.status!=='active');check(panel.state.collaborationMode==='plan','Plan mode is reflected by shared side state');
 check(panel.sendMode.querySelector('[value=steer]').disabled,'Steer is unavailable when there is no running turn');
 const old=BridgeI18n.language();BridgeI18n.setLanguage('en');wb.relabel();
 check(!/[\u4e00-\u9fff]/.test(panel.form.textContent),'Side composer controls switch fully to English');BridgeI18n.setLanguage(old);wb.relabel();
 return checks;
}
