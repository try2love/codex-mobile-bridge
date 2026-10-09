// Run on the synthetic fixture at desktop width.
async function runWorkbenchSplitTests(){
 const wb=window.BridgeWorkbench,session=wb.current,checks=[];
 const check=(ok,label)=>{if(!ok)throw Error(label);checks.push(label);};
 const frame=()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
 wb.sideChat();const panel=session.files.find(t=>t.id==='sidechat').sideChat;await panel.load();if(!panel.state.id)await panel.create();
 const plus=wb.strip.querySelector('.wb-add').getBoundingClientRect(),split=wb.splitButton.getBoundingClientRect();
 check(plus.y===split.y&&plus.width===split.width&&plus.height===split.height,'Add and split icons have aligned equal-size controls');
 const draft=document.querySelector('#message');draft.value='main split draft';panel.input.value='side split draft';
 wb.unsplit();const tab=[...wb.tabs.children].find(n=>n.textContent.includes(BridgeI18n.t('侧边聊天'))),data=new DataTransfer();
 tab.dispatchEvent(new DragEvent('dragstart',{bubbles:true,dataTransfer:data}));
 check(wb.layout.classList.contains('wb-dragging'),'Dragging a workbench tab exposes the right drop target');
 wb.dropZone.dispatchEvent(new DragEvent('drop',{bubbles:true,dataTransfer:data}));await frame();
 check(wb.splitMode&&!wb.isFileVisible,'Dropping opens main chat and side tab together');
 check(wb.isVisible(session,panel.tab),'Right side remains active for polling');
 const left=wb.mainPane.getBoundingClientRect(),right=wb.panel.getBoundingClientRect();
 check(left.width>=320&&right.width>=320&&right.left>left.right,'Both panes are visible with minimum widths');
 const before=left.width;wb.divider.dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowRight',bubbles:true}));await frame();
 check(wb.mainPane.getBoundingClientRect().width>before,'Keyboard resizes the divider');
 wb.setRatio(0);await frame();check(wb.mainPane.getBoundingClientRect().width>=320,'Cannot shrink left pane below minimum');
 wb.setRatio(1);await frame();check(wb.panel.getBoundingClientRect().width>=320,'Cannot shrink right pane below minimum');
 wb.setRatio(.5);wb.files();await frame();check(wb.splitMode&&session.splitTab==='files','Selecting another tool replaces only the right pane');
 wb.select('sidechat');await frame();check(panel.input.value==='side split draft'&&draft.value==='main split draft','Switching tools preserves both drafts');
 document.documentElement.classList.add('bridge-mobile');await frame();
 check(wb.splitMode&&!wb.splitButton.hidden,'Wide native App supports split just like Web');
 check(panel.tab.body.isConnected&&panel.input.value==='side split draft','Native layout keeps selected tab and draft');
 document.documentElement.classList.remove('bridge-mobile');await frame();
 check(wb.splitMode,'Web and native App share split preference');
 wb.unsplit();check(!wb.splitMode&&session.active==='sidechat','Exiting split retains the selected tool');
 return checks;
}
