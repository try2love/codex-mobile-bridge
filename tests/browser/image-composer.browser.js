// Synthetic-only: execute the production app callback with real Workbench,
// ComposerQuotes, ChatAttachments and image viewer DOM. Never sends a chat.
async function runImageComposerWorkbenchTests(callbackSource,keepOpen=false){
 const results=[],check=(ok,label)=>{if(!ok)throw Error(label);results.push(label);};
 const until=async test=>{for(let i=0;i<100;i++){if(test())return;await new Promise(resolve=>setTimeout(resolve,20));}throw Error('Image composer workbench fixture timed out');};
 const sid='aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee';
 const canvas=document.createElement('canvas');canvas.width=240;canvas.height=160;canvas.getContext('2d').fillRect(0,0,240,160);const data=canvas.toDataURL('image/png').split(',')[1];
 window.uuid=()=>crypto.randomUUID();
 for(const provider of ['codex','claude','deepseek']){
  const root=document.createElement('section');root.className='chat-open';root.style.cssText='position:fixed;inset:0;display:flex;z-index:10;background:var(--page)';
  const chat=document.createElement('section');chat.className=provider==='codex'?'chat':'chat ds-conversation';if(provider==='codex')chat.id='chat';chat.style.cssText='display:flex;flex-direction:column;width:100%;height:100%';
  const head=document.createElement('header');head.className='chat-head';head.textContent=provider;
  const messages=document.createElement('div');messages.className='timeline';messages.style.flex='1';messages.innerHTML='<div class="message assistant"><div class="message-body">Synthetic reply</div></div>';
  const form=document.createElement('form');form.className='composer';const list=document.createElement('div'),input=document.createElement('textarea'),button=document.createElement('button'),fileInput=document.createElement('input');input.value='Existing '+provider+' draft';form.append(list,input,button,fileInput);chat.append(head,messages,form);root.append(chat);document.body.append(root);
  const scope={gateway:'image-fixture',provider,account:'original-account',host:'local',session:sid};
  const quotes=new ComposerQuotes({messages,input,container:form,scope:()=>scope,onChange(){},onPrompt(){},canQuote:()=>provider!=='deepseek'});quotes.restore([{text:'existing quote',provider}]);
  const panel=new ChatAttachments({root:list,button,input:fileInput,upload:async(key,id,file)=>({id,name:file.name,size:file.size,image:'image/png'}),onChange(){}});const attachmentKey=provider==='codex'?'local|'+sid:JSON.stringify([provider,sid]);sessionStorage.removeItem('attachments:'+attachmentKey);panel.open(attachmentKey);
  const clientNavigation={provider},desktopSessions={provider:provider==='codex'?'codex':provider,sid,quotes,attachments:panel,state:{capabilities:{attachments:true}},container:root,conversation:chat,canMutate:()=>true,toolbar:{setCollapsed(){}}};
  const codexQuotes=quotes,attachments=panel,currentId=sid,currentHost='local',state={},sending=false;
  const $=id=>({app:root,chat}[id]),isCodexClient=value=>value==='codex',chatKey=(id,host)=>host+'|'+id,currentAccess=()=>({canSend:true}),t=value=>value;
  const workbench=new Workbench({chat,request:async()=>({kind:'image',mime:'image/png',data,name:'workspace.png',size:120}),csrf:()=>'',notify(){},onUnauthorized(){}});workbench.provider=provider;desktopSessions.workbench=workbench;workbench.open(sid,'local');
  eval(callbackSource);
  await workbench.preview(workbench.current,{path:'workspace.png',name:'workspace.png'});
  check(workbench.current.active==='file:workspace.png',provider+' opens a real file tab');
  check(getComputedStyle(workbench.mainPane).display==='none',provider+' file tab hides the original composer through Workbench CSS');
  check(quotes.available(),provider+' opaque composer scope remains available while the file tab is active');
  const source=workbench.panel.querySelector('.wb-preview-content img');source.click();
  await until(()=>document.querySelector('#image-preview-dialog')?.open);
  const reuse=document.querySelector('#image-preview-reuse');check(!reuse.hidden,provider+' workspace image exposes Add to conversation');
  reuse.click();try{await until(()=>panel.rows.length===1&&panel.rows[0].status==='ready'&&!document.querySelector('#image-preview-dialog').open);}catch(error){throw Error(provider+' add did not settle: '+JSON.stringify({rows:panel.rows.map(({status,error})=>({status,error})),open:document.querySelector('#image-preview-dialog').open,status:document.querySelector('#image-preview-status').textContent,active:workbench.current.active}));}
  check(workbench.current.active==='chat'&&getComputedStyle(workbench.mainPane).display!=='none',provider+' image add returns to a visible chat composer');
  check(list.querySelector('.attachment-chip')?.getBoundingClientRect().height>0,provider+' reused attachment is visible in the composer');
  check(input.value==='Existing '+provider+' draft'&&quotes.quotes[0].text==='existing quote',provider+' image add preserves text and quote drafts');
  check(panel.rows[0].name==='workspace.png',provider+' existing attachment uploader receives the registered workspace image');
  scope.account='changed-account';workbench.select('file:workspace.png');source.click();await until(()=>document.querySelector('#image-preview-dialog').open);
  check(document.querySelector('#image-preview-reuse').hidden,provider+' cached workspace image cannot be reused after an account identity change');
  document.querySelector('#image-preview-close').click();scope.account='original-account';workbench.select('chat');
  workbench.resizeObserver.disconnect();workbench.mobileObserver.disconnect();if(!keepOpen||provider!=='deepseek'){panel.reset();root.remove();}else root.dataset.imageComposerFixture='true';
 }
 return results;
}
