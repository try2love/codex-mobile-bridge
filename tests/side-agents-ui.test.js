// Run against the synthetic workbench fixture after opening the side chat.
async function runSideAgentsTests(){
 const wb=window.BridgeWorkbench,session=wb.current,p=session.sideChat,checks=[];
 const check=(ok,label)=>{if(!ok)throw Error(label);checks.push(label);};
 const wait=async f=>{for(let i=0;i<400;i++){if(f())return;await new Promise(r=>setTimeout(r,25));}throw Error('Side chat fixture did not settle');};
 await wait(()=>p.timeline.nodes.size>0);
 const main=document.querySelector('#messages').textContent,hash=location.hash;
 document.querySelector('#message').value='main draft';p.input.value='side draft';p.input.dispatchEvent(new Event('input'));
 wb.files();await wait(()=>document.querySelector('.wb-file-list'));
 check(p.window.element.getBoundingClientRect().height>0&&getComputedStyle(wb.sideHost).display!=='none','Side chat stays visible alongside files');
 check(document.querySelector('#message').value==='main draft','Side draft does not overwrite main draft');
 check(document.querySelector('#messages').textContent===main&&location.hash===hash,'Opening side chat preserves main history and route');
 const original=wb.request,calls=[];
 wb.request=async(url,body,signal)=>{if(url===p.url('send')){calls.push({url,body});return {status:calls.length===1?'unknown':'sent'};}return original(url,body,signal);};
 try{
  await p.submit();check(p.input.value==='side draft'&&p.error.textContent.includes('未确认'),'Unknown send retains draft');
  await p.submit();check(calls[0].body.id===calls[1].body.id,'Explicit retry keeps idempotency key');
  check(calls.every(c=>c.url.includes(p.target.id)&&!('model' in c.body)&&!('provider' in c.body)),'Side send targets its own thread and inherits provider');
  check(p.input.value==='','Confirmed send clears only side draft');
 }finally{wb.request=original;}
 wb.agents();await wait(()=>document.querySelectorAll('.wb-agent-card').length===2);
 check(document.querySelectorAll('.wb-agent-card').length===2,'Subagent panel includes child and grandchild');
 check(parseInt(document.querySelectorAll('.wb-agent-card')[1].style.paddingLeft)>parseInt(document.querySelectorAll('.wb-agent-card')[0].style.paddingLeft),'Task tree indents descendants');
 p.window.element.querySelector('[aria-label="关闭详情"]').click();
 check(!session.sideChat,'Closing side chat leaves main workbench available');
 document.querySelector('.wb-agent-card').click();await wait(()=>document.querySelector('.wb-agent-message'));
 check(document.querySelector('.wb-agent-detail').textContent.includes('已完成'),'Saved child completion is displayed');
 check(location.hash===hash,'Inspecting child does not navigate or activate it');
 const agentPanel=session.files.find(t=>t.id==='agents').agents;
 agentPanel.window.element.querySelector('[aria-label="关闭详情"]').click();
 return checks;
}
