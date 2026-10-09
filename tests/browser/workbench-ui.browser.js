// Run against the isolated gateway fixture, with a scrollable chat and file list.
async function runWorkbenchTests() {
  const workbench=window.BridgeWorkbench, checks=[];
  const check=(value,label)=>{if(!value)throw Error(label);checks.push(label);};
  const settle=async()=>{for(let i=0;i<5;i++)await new Promise(resolve=>requestAnimationFrame(resolve));};
  const session=workbench.current, input=document.getElementById('message'), timeline=document.getElementById('timeline');
  workbench.select('chat');await settle();
  input.value='Draft retained across project tabs';input.dispatchEvent(new Event('input'));await settle();
  timeline.scrollTop=80;await settle();const reading=timeline.scrollTop;
  check(reading>0,'Fixture chat is scrollable');
  workbench.select('files');await settle();
  const list=document.querySelector('.wb-file-list');list.scrollTop=100;const fileTop=list.scrollTop;
  check(fileTop>0,'Fixture file list is scrollable');
  workbench.select('chat');await settle();
  check(input.value==='Draft retained across project tabs','Tab switching retains the draft');
  check(Math.abs(timeline.scrollTop-reading)<2,'Tab switching retains chat reading position');
  workbench.select('files');await settle();
  check(list.scrollTop===fileTop,'Tab switching retains file list position');
  check(workbench.back()&&workbench.current.active==='chat','Back returns from files to chat');
  check(!workbench.back(),'Back on chat lets the chat list handle navigation');
  workbench.open(session.id,'fixture-other-host');
  check(workbench.current.files.length===0,'Same thread ID on another host has independent tabs');
  workbench.open(session.id,session.host);
  check(workbench.current===session&&session.files.length>0,'Returning to the original host restores its tabs');
  workbench.select('files');
  document.getElementById('back').click();
  check(!document.getElementById('app').classList.contains('chat-open'),'Header back from files returns directly to the chat list');
  check(session.active==='files','Header back does not discard project tabs');
  document.getElementById('app').classList.add('chat-open');
  workbench.close('files');
  check(session.active==='chat'&&!workbench.isFileVisible,'Closing the file center keeps chat available');
  return checks;
}
