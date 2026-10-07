// Run only against the synthetic workbench fixture, with its main chat open.
async function runSideChatTests(){
 const wb=window.BridgeWorkbench,session=wb.current,checks=[];
 const check=(ok,label)=>{if(!ok)throw Error(label);checks.push(label);};
 const wait=async f=>{for(let i=0;i<200;i++){if(f())return;await new Promise(r=>setTimeout(r,25));}throw Error('Side chat fixture did not settle');};
 const main=document.querySelector('#messages').textContent,hash=location.hash;
 document.querySelector('#message').value='main draft';
 wb.sideChat();let panel=session.files.find(t=>t.id==='sidechat').sideChat;
 await wait(()=>panel.state!==undefined);
 if(!panel.state.id)await panel.create();
 check(panel.state.connected,'Remote creation succeeds without a desktop tab');
 const child=panel.state.id;
 check(session.active==='sidechat'&&!panel.tab.body.closest('.wb-float'),'Side chat is an independent subtab');
 panel.input.value='first line\nsecond line';panel.input.dispatchEvent(new Event('input'));
 const turns=panel.state.turns.length;
 panel.input.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true}));
 check(panel.state.turns.length===turns,'Enter does not submit a terminal-style command');
 await panel.send();await panel.load();
 check(panel.state.turns.some(t=>t.messages.some(m=>m.role==='user'&&m.text==='first line\nsecond line'))&&!!panel.messages.querySelector('br'),'Explicit send displays the multiline message');
 check(panel.messages.textContent.includes('侧边聊天回复'),'Reply is rendered');
 panel.dispose();session.files.splice(session.files.indexOf(panel.tab),1);wb.sideChat();
 panel=session.files.find(t=>t.id==='sidechat').sideChat;await wait(()=>panel.state?.id);
 check(panel.state.id===child&&panel.messages.textContent.includes('侧边聊天回复'),'Reopened view discovers same child and transcript');
 check(document.querySelector('#message').value==='main draft','Main draft is preserved');
 check(document.querySelector('#messages').textContent===main&&location.hash===hash,'Main history and route are preserved');
 const confirmOriginal=window.confirm;let confirmations=0;
 try{
  window.confirm=()=>{confirmations++;return false;};await wb.close('sidechat');
  check(session.files.some(t=>t.id==='sidechat'),'Cancel close keeps chat');
  window.confirm=()=>{confirmations++;return true;};await wb.close('sidechat');
  check(!session.files.some(t=>t.id==='sidechat'),'Confirmed close removes subtab');
 }finally{window.confirm=confirmOriginal;}
 check(confirmations===2,'Close requires explicit confirmation');
 wb.sideChat();panel=session.files.find(t=>t.id==='sidechat').sideChat;await wait(()=>panel.state!==undefined);
 check(!panel.state.id&&!panel.messages.textContent.includes('侧边聊天回复'),'Closed chat does not resurrect');
 await panel.create();check(panel.state.id!==child,'Explicit new chat receives a different child ID');
 return checks;
}
