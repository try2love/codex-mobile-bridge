// Run only in the isolated workbench fixture page after opening Git.
async function runFloatingPanelTests() {
  const wb=window.BridgeWorkbench, panel=wb.current.files.find(t=>t.id==='git').git, checks=[];
  const check=(value,text)=>{if(!value)throw Error(text);checks.push(text);};
  const wait=async predicate=>{for(let i=0;i<400;i++){if(predicate())return;await new Promise(r=>setTimeout(r,25));}throw Error('Floating detail did not settle');};
  panel.mode='changes';await panel.load();document.querySelector('.wb-git-file').click();
  await wait(()=>document.querySelector('.wb-float .wb-diff-line'));
  let win=panel.floating;const before={...win.actual};
  check(win.element.getAttribute('role')==='region','Detail is non-modal and scoped to the tab');
  win.resize.dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowUp',bubbles:true}));
  check(win.actual.h===before.h-16,'Keyboard resize changes content height');
  win.grip.dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowUp',bubbles:true}));
  check(win.actual.y<before.y,'Keyboard moves window');
  const saved=JSON.stringify(win.actual);wb.select('chat');wb.select('git');win.layout();
  check(JSON.stringify(win.actual)===saved,'Tab switching retains geometry');
  win.maximize.click();check(win.actual.h===win.host.clientHeight-12,'Maximize stays inside tab');
  win.maximize.click();check(JSON.stringify(win.actual)===saved,'Restore recovers previous rectangle');
  win.adjust('move',win.actual,10000,10000);
  check(win.actual.x+win.actual.w<=win.host.clientWidth&&win.actual.y+win.actual.h<=win.host.clientHeight,'Dragging stays inside tab');
  win.adjust('resize',win.actual,-10000,-10000);
  check(win.actual.w>=Math.min(280,win.host.clientWidth-12)&&win.actual.h>=Math.min(180,win.host.clientHeight-12),'Resize respects minimum dimensions');
  win.element.querySelector('[aria-label="关闭详情"]').click();
  check(!document.querySelector('.wb-float')&&!panel.selected,'Close clears detail and selection');
  document.querySelector('.wb-git-file').click();await wait(()=>document.querySelector('.wb-float .wb-diff-line'));
  check(panel.floating.actual.h===Math.min(180,panel.floating.host.clientHeight-12),'Reopen retains dimensions');
  panel.mode='history';await panel.load();document.querySelector('.wb-history-row').click();await wait(()=>document.querySelector('.wb-history-file'));
  check(panel.floating.element.contains(document.querySelector('.wb-history-file')),'Commit picker belongs to floating window');
  await wait(()=>document.querySelector('.wb-history-diff-area .wb-diff-line'));
  check(!!document.querySelector('.wb-history-diff-area .wb-diff-line'),'Historical diff loads');
  panel.floating.element.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}));
  check(!document.querySelector('.wb-float')&&!panel.selectedCommit,'Escape closes without navigating');return checks;
}
