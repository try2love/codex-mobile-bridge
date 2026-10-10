// Run against the synthetic workbench fixture after opening the side chat.
async function runSideAgentsTests(){
 const wb=window.BridgeWorkbench,session=wb.current,checks=[];
 const check=(ok,label)=>{if(!ok)throw Error(label);checks.push(label);};
 const wait=async f=>{for(let i=0;i<400;i++){if(f())return;await new Promise(r=>setTimeout(r,25));}throw Error('Workbench fixture did not settle');};
 const main=document.querySelector('#messages').textContent,hash=location.hash;
 document.querySelector('#message').value='main draft';
 wb.sideChat();
 check(session.active==='sidechat','Side chat opens as a separate subtab');
 check(!document.querySelector('.wb-side-chat,.wb-side-picker'),'Side chat does not show a floating saved-chat picker');
 await wait(()=>session.files.find(t=>t.id==='sidechat').sideChat.state!==undefined);
 const side=session.files.find(t=>t.id==='sidechat').sideChat;
 check(!!side.input,'Side chat has its own composer');
 if(side.state.id){side.dispose();session.files.splice(session.files.indexOf(side.tab),1);wb.select('chat');}
 else await wb.close('sidechat');
 check(!session.files.some(t=>t.id==='sidechat'),'Close removes the temporary subtab');
 check(document.querySelector('#message').value==='main draft','Side subtab preserves main draft');
 check(document.querySelector('#messages').textContent===main&&location.hash===hash,'Side subtab preserves main history and route');
 wb.agents();await wait(()=>document.querySelectorAll('.wb-agent-card').length===2);
 check(document.querySelectorAll('.wb-agent-card').length===2,'Subagent panel includes child and grandchild');
 check(parseInt(document.querySelectorAll('.wb-agent-card')[1].style.paddingLeft)>parseInt(document.querySelectorAll('.wb-agent-card')[0].style.paddingLeft),'Task tree indents descendants');
 document.querySelector('.wb-agent-card').click();await wait(()=>document.querySelector('.wb-agent-message'));
 check(document.querySelector('.wb-agent-detail').textContent.includes('已完成'),'Saved child completion is displayed');
 check(location.hash===hash,'Inspecting child does not navigate or activate it');
 const agentPanel=session.files.find(t=>t.id==='agents').agents;
 agentPanel.window.element.querySelector('[aria-label="关闭详情"]').click();
 return checks;
}
