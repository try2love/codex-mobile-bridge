// Synthetic native replies; production shared controls and panel, no live mutations.
async function runNativeControlsChecks(){
 const checks=[],check=(ok,label)=>{if(!ok)throw Error(label);checks.push(label);};
 let compactCalls=0;const usage=new ContextUsageControl({onCompact:async()=>{compactCalls++;return true;}});
 document.body.append(usage.button);
 const state={id:'fixture',connected:true,capabilities:{compact:true},contextUsage:{usedTokens:250,contextWindow:1000,autoCompactThresholdTokens:800,cache:{cacheReadTokens:100}}};
 usage.update(state);usage.open();const dialog=usage.dialog,button=usage.compactButton;
 check(dialog.textContent.includes('800')&&dialog.textContent.includes('100'),'native details visible');
 check(dialog.getBoundingClientRect().right<=innerWidth,'context dialog fits');
 usage.update({...state,connected:false});check(button.disabled,'stale context cannot compact');
 usage.update(state);button.click();await Promise.resolve();await Promise.resolve();check(compactCalls===1,'single compaction dispatch');usage.dispose();
 const tab={id:'agents',body:document.createElement('div')};document.body.append(tab.body);
 let stops=[],reads=0;const tasks=[{id:'one',title:'后台任务的较长标题用于检查窄屏显示',status:'running',cancellable:true},{id:'done',title:'Completed task',status:'resultPending',cancellable:false}];
 const workbench={node:(tag,cls='',text='')=>{const n=document.createElement(tag);n.className=cls;n.textContent=text;return n;},button:(text,fn)=>{const n=document.createElement('button');n.textContent=text;n.onclick=fn;return n;},request:async url=>{reads++;check(url.includes('/zcode/tasks?sessionId=fixture'),'shared panel uses scoped native task route');return {tasks};},isVisible:()=>false,notify(){},stopTask:async(session,id)=>{stops.push([session.id,id]);tasks[0].cancellable=false;return {status:'accepted'};}};
 const panel=new AgentsPanel(workbench,{provider:'zcode',id:'fixture'},tab);await Promise.resolve();await Promise.resolve();
 check(panel.list.querySelectorAll('button').length===1,'only cancellable task has stop');
 await panel.list.querySelector('button').onclick();await Promise.resolve();
 check(stops.length===1&&stops[0][1]==='one','stops exactly selected task');
 check(panel.list.querySelectorAll('button').length===0,'finished task loses stop control');
 panel.dispose();tab.body.remove();return {checks,width:innerWidth,reads};
}
