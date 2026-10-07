// Run on the synthetic gateway. The browser driver changes the real viewport
// between prepareWorkbenchAdaptiveTest() and checkWorkbenchAdaptiveTest(width).
async function prepareWorkbenchAdaptiveTest(){
 const wb=window.BridgeWorkbench;
 wb.sideChat();const side=wb.current.files.find(t=>t.id==='sidechat').sideChat;
 await side.load();if(!side.state.id)await side.create();
 wb.split('sidechat');
 await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)));
 const main=wb.chat.querySelector('.timeline'),draft=document.querySelector('#message');
 draft.value='Main draft across fold/unfold';side.input.value='Side draft across fold/unfold';
 // A stable scrollable side conversation, independent of fixture reply timing.
 side.messages.replaceChildren(...Array.from({length:60},(_,i)=>{const p=document.createElement('p');p.textContent='Side reading position '+i;return p;}));
 main.scrollTop=80;side.messages.scrollTop=100;side.input.focus();
 window.adaptiveSplitTest={wb,session:wb.current,side,main,draft,mainTop:main.scrollTop,sideTop:side.messages.scrollTop,files:[...wb.current.files]};
 if(!main.scrollTop||!side.messages.scrollTop)throw Error('Fixture must have scrollable main and side chats');
}

async function checkWorkbenchAdaptiveTest(width){
 await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)));
 const {wb,session,side,main,draft,mainTop,sideTop,files}=window.adaptiveSplitTest,checks=[];
 const check=(ok,label)=>{if(!ok)throw Error(width+'px: '+label);checks.push(label);};
 check(window.innerWidth===width,'Real viewport resized');
 check(wb.current===session&&files.every((tab,i)=>session.files[i]===tab),'Same session and open tabs retained');
 check(draft.value==='Main draft across fold/unfold'&&side.input.value==='Side draft across fold/unfold','Both drafts retained');
 check(side.tab.body.isConnected&&document.activeElement===side.input,'Visible tool stays mounted and focused');
 check(Math.abs(side.messages.scrollTop-sideTop)<2,'Side chat reading position retained');
 const wide=width>=720;
 check(wb.splitMode===wide&&wb.splitButton.hidden===!wide,'Split follows usable width');
 check(session.splitTab==='sidechat','Split preference retained while folded');
 if(wide){
  check(Math.abs(main.scrollTop-mainTop)<2,'Main chat reading position restored');
  const left=wb.mainPane.getBoundingClientRect(),right=wb.panel.getBoundingClientRect();
  check(left.width>=319&&right.width>=319&&left.right<right.left,'Both panes have usable widths');
  check(wb.panel.scrollWidth<=wb.panel.clientWidth+1,'Right pane has no horizontal overflow');
 }
 check(document.documentElement.scrollWidth<=width,'No page horizontal overflow');
 return checks;
}
