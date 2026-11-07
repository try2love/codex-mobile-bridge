// Run in a gateway browser page with timeline.js loaded. Uses synthetic data only.
async function runTimelineTests() {
  const checks=[];
  function check(value,label){if(!value)throw Error(label);checks.push(label);}
  const frame=()=>new Promise(resolve=>requestAnimationFrame(resolve));
  async function until(fn){const end=performance.now()+2500;while(!fn()){if(performance.now()>end)throw Error('Timed out waiting for fixture');await frame();}}
  document.getElementById('app').hidden=false;
  document.getElementById('chat').hidden=false;
  document.getElementById('app').classList.add('chat-open');
  const viewport=document.getElementById('timeline');
  viewport.style.cssText='height:400px;flex:none;overflow:auto;width:370px';
  const row=i=>({key:'k'+i,order:i,role:'assistant',version:'v'+i,text:'Record '+i+'\n'+'test paragraph '.repeat(12),
    ...(i===318?{model:'gpt-6-astra',effort:'max'}:i===319?{model:'gemini-pro',effort:'high'}:{})});
  const page=(from,to,sequence=1)=>({rows:Array.from({length:to-from},(_,i)=>row(from+i)),sequence,epoch:'epoch',before:'epoch.k'+from,hasMore:from>0,meta:{requests:[{id:'pending'}]},files:[]});
  const pending=[],requests=[];let meta,signal;
  const request=(url,body,abort)=>{
    signal=abort;
    const u=new URL(url,location.origin);requests.push(u);
    if(u.pathname.endsWith('/changes'))return new Promise((resolve,reject)=>abort.addEventListener('abort',()=>reject(new DOMException('aborted','AbortError')),{once:true}));
    if(!u.searchParams.has('before'))return Promise.resolve(page(300,320));
    return new Promise(resolve=>pending.push({resolve,limit:Number(u.searchParams.get('limit'))}));
  };
  const timeline=new ChatTimeline({url:action=>'/fixture/'+action+'?host=local',request,renderMeta:v=>meta=v,renderText:(node,text)=>node.textContent=text,status:()=>{}});
  await timeline.start();
  check(timeline.rows.size===20,'First paint has 20 messages');
  check(timeline.nodes.get('k318').node.firstChild.textContent==='CODEX · gpt-6-astra max','Old reply retains its own model and effort');
  check(timeline.nodes.get('k319').node.firstChild.textContent==='CODEX · gemini-pro high','New reply shows its own model and effort');
  check(timeline.nodes.get('k317').node.firstChild.textContent==='CODEX','Missing historical model is not guessed');
  check(meta.requests[0].id==='pending','Approval metadata arrives with first page');
  await frame();await frame();
  check(pending.length===0,'Opening does not prefetch older history');
  timeline.loadOlder(80);
  await until(()=>pending.length===1);
  check(pending[0].limit===80,'Explicit history read uses the requested page size');
  timeline.loadOlder(100);timeline.loadOlder(100);
  check(pending.length===1,'Only one history request in flight');
  pending.shift().resolve(page(220,300));
  await until(()=>timeline.rows.size===100&&!timeline.busy);await frame();await frame();
  check(timeline.atBottom(),'Backfill preserves bottom position');
  const anchorNode=timeline.nodes.get('k229').node;
  viewport.scrollTop+=anchorNode.getBoundingClientRect().top-viewport.getBoundingClientRect().top+4;
  viewport.dispatchEvent(new Event('scroll'));
  await until(()=>pending.length===1);
  const anchor=timeline.capture();
  check(pending[0].limit===100,'Within 10 records of top prefetches 100');
  pending.shift().resolve(page(120,220));
  await until(()=>timeline.rows.size===200&&!timeline.busy);await frame();
  check(Math.abs(timeline.nodes.get(anchor.key).node.getBoundingClientRect().top-viewport.getBoundingClientRect().top-anchor.offset)<2,'Prepending preserves message and pixel offset');
  const top=viewport.scrollTop;
  timeline.apply({...page(320,321,2),before:undefined,hasMore:undefined});
  check(timeline.rows.size===201,'Live append adds one message');
  check(Math.abs(viewport.scrollTop-top)<2&&!timeline.newer.hidden,'Live reply preserves history reading and shows new content button');
  await frame();await frame();
  const reading=timeline.capture(),readingTop=viewport.scrollTop;
  viewport.style.display='none';
  await frame();await frame();
  viewport.dispatchEvent(new Event('scroll'));
  check(JSON.stringify(timeline.capture())===JSON.stringify(reading),'Hidden workbench chat retains its reading anchor');
  viewport.style.display='';
  await frame();await frame();
  check(Math.abs(viewport.scrollTop-readingTop)<2,'Returning from a file tab preserves the timeline offset');
  const update=row(320);update.text+=' streamed';update.version='streamed';
  timeline.apply({...page(0,0,3),rows:[update]});
  check(timeline.rows.size===201,'Streaming replaces the same message');
  const sequence=timeline.sequence;
  timeline.apply(page(100,120,4),false,true);
  check(timeline.sequence===sequence,'History cannot skip the live update cursor');
  timeline.apply({...page(0,0,2),rows:[row(320)]});
  check(timeline.rows.get('k320').row.version==='streamed','Late responses cannot overwrite newer text');
  timeline.loadOlder(100);await until(()=>pending.length===1);
  timeline.dispose();const size=timeline.rows.size;
  check(signal.aborted,'Switching chat aborts active fetches');
  pending.shift().resolve(page(0,100,5));await frame();
  check(timeline.rows.size===size,'Late history cannot alter a disposed chat');
  timeline.rows.clear();timeline.container.replaceChildren();
  let detailCalls=0,fail=true;
  const tool={...row(0),role:'activity',title:'Tool',truncated:true,text:'Preview'};
  const long={...row(1),truncated:true,text:'Short preview'};
  const detailRequest=async url=>{
    const u=new URL(url,location.origin);
    if(u.pathname.endsWith('/detail')){detailCalls++;return {key:u.searchParams.get('key').split('.').pop(),version:'v1',offset:0,text:'Full body',next:null};}
    if(fail)throw Error('offline');
    return {...page(0,1,4),rows:[tool],hasMore:false};
  };
  const second=new ChatTimeline({url:action=>'/fixture/'+action+'?host=local',request:detailRequest,renderMeta:()=>{},renderText:(node,text)=>{const p=document.createElement('p');p.textContent=text;node.append(p);},status:()=>{}});
  second.apply({...page(1,2,4),rows:[long]},true);
  second.loadOlder(100);
  await until(()=>second.retryCount>0);
  check(second.older.textContent===timelineText('历史加载失败，点击重试'),'Failed history exposes a retry button');
  fail=false;await second.loadOlder(100);
  check(!second.hasMore&&second.older.hidden,'EOF stops history loading');
  check(detailCalls===0,'Collapsed tool bodies are not fetched');
  const message=second.nodes.get('k1').node;
  message.querySelector('button').click();
  await until(()=>detailCalls===1&&!message.querySelector('button').disabled);
  check(message.querySelectorAll('.message-body p').length===1&&message.textContent.includes('Full body')&&!message.textContent.includes('Short preview'),'Expanding text replaces the preview without duplicating Markdown');
  second.dispose();
  return checks;
}

async function runTimelineVisibilityTests() {
  const checks=[];
  document.getElementById('messages').replaceChildren();
  const check=(value,label)=>{if(!value)throw Error(label);checks.push(label);};
  const frame=()=>new Promise(resolve=>requestAnimationFrame(resolve));
  const rows=[
    {key:'user',role:'user',kind:'userMessage',text:'User message'},
    {key:'reasoning',role:'activity',kind:'reasoning',text:'Reasoning summary'},
    {key:'command',role:'activity',kind:'commandExecution',text:'git status --short\n\nCommand output'},
    {key:'tool',role:'activity',kind:'storedToolEvent',text:'Tool output'},
    {key:'files',role:'activity',kind:'fileChange',text:'timeline.js\n@@ -1 +1 @@'},
    {key:'progress',role:'assistant',kind:'agentMessage',phase:'commentary',text:'Progress update'},
    {key:'final',role:'assistant',kind:'agentMessage',phase:'final_answer',text:'Final reply\n'+('Reply text '.repeat(250))},
    {key:'legacy',role:'assistant',kind:'agentMessage',text:'Reply without a phase'},
    {key:'error',role:'error',text:'Execution failed'},
  ].map((row,order)=>({...row,order,version:'v1'}));
  let requested=0,meta;
  const page=(items,sequence=1,hasMore=false)=>({rows:items,sequence,epoch:'visibility',before:'visibility.'+items[0]?.key,hasMore,meta:{requests:[{id:'pending'}]},files:[]});
  const timeline=new ChatTimeline({url:action=>'/fixture/'+action+'?host=local',request:async()=>{requested++;return page(rows);},renderMeta:value=>meta=value,renderText:(node,text)=>node.textContent=text,status:()=>{}});
  const visible=()=>[...timeline.nodes].filter(([,entry])=>!entry.node.hidden&&!entry.node.closest('.activity-group[hidden]')).map(([key])=>key);
  timeline.setVisibility({showReasoning:true,showProcess:true});
  timeline.apply(page(rows),true);await frame();
  check(visible().length===9,'Default display retains all message categories');
  const activityGroup=timeline.container.querySelector('.activity-group');
  check(activityGroup&&!activityGroup.open&&activityGroup.dataset.key==='files','Consecutive activity steps collapse behind the latest step by default');
  check(timeline.activityStepLabel(rows[2])==='已运行 git status --short','Command summaries include the actual command');
  check(timeline.activityStepLabel(rows[4])==='已编辑 timeline.js','File summaries include the actual changed path');
  check(activityGroup.firstElementChild.textContent==='已编辑 timeline.js','Collapsed group exposes the latest command or file detail');
  const groupSummaryStyle=getComputedStyle(activityGroup.firstElementChild);
  check(groupSummaryStyle.whiteSpace==='nowrap'&&groupSummaryStyle.textOverflow==='ellipsis'&&groupSummaryStyle.overflow==='hidden','Primary fold summary is truncated to one line');
  check(getComputedStyle(timeline.nodes.get('reasoning').node).display==='none','Collapsed secondary steps do not occupy the timeline');
  activityGroup.open=true;await frame();
  const commandSummary=timeline.nodes.get('command').node.querySelector(':scope>summary'),commandSummaryStyle=getComputedStyle(commandSummary);
  check(commandSummary.textContent==='已运行 git status --short','Secondary step summaries include the actual command');
  check(commandSummaryStyle.whiteSpace==='nowrap'&&commandSummaryStyle.textOverflow==='ellipsis'&&commandSummaryStyle.overflow==='hidden','Secondary fold summary is truncated to one line');
  check(getComputedStyle(timeline.nodes.get('reasoning').node).display!=='none','Opening the secondary group reveals every step');
  activityGroup.open=false;await frame();
  timeline.details.set('command',{version:'v1',text:'Command output',next:null});
  timeline.nodes.get('command').node.open=true;
  timeline.setVisibility({showReasoning:false,showProcess:true});
  check(!visible().includes('reasoning')&&visible().includes('command'),'Reasoning can be hidden independently');
  timeline.setVisibility({showReasoning:true,showProcess:false});
  check(visible().includes('reasoning')&&!visible().includes('progress')&&!visible().includes('command'),'Activity filter includes tools and commentary but retains reasoning');
  timeline.setVisibility({showReasoning:false,showProcess:false});
  check(JSON.stringify(visible())===JSON.stringify(['user','final','legacy','error']),'Reply view retains user text, final replies, unknown-phase replies and errors');
  check(meta.requests[0].id==='pending'&&timeline.pendingRequests,'Pending user requests remain available');
  timeline.viewport.scrollTop=timeline.nodes.get('final').node.offsetTop+20;
  timeline.following=false;timeline.newer.hidden=true;
  const anchor=timeline.capture();
  timeline.apply({...page([{...rows[2],version:'v2',text:'Streaming command output'}],2),meta:{requests:[]}});
  check(timeline.nodes.get('command').node.hidden&&timeline.newer.hidden,'Hidden streaming activity stays hidden without a new-content alert');
  check(timeline.capture()?.key===anchor?.key,'Hidden updates preserve the reading anchor');
  timeline.apply(page([{...rows[6],version:'v2',text:rows[6].text+'\nLive reply'}],3));
  check(!timeline.nodes.get('final').node.hidden&&!timeline.newer.hidden,'Streaming reply remains visible and signals new content');
  timeline.setVisibility({showReasoning:true,showProcess:true});
  check(visible().length===9&&timeline.nodes.get('command').node.open,'Restoring activity preserves its expansion state');
  timeline.dispose();
  timeline.container.replaceChildren();
  const reasoningRows=[
    {key:'reason-user',order:0,role:'user',kind:'userMessage',text:'Check',version:'r1'},
    {key:'reason-command',order:1,role:'activity',kind:'commandExecution',text:'echo before-thinking\n\nDone',version:'r1'},
    {key:'reasoning-latest',order:2,role:'activity',kind:'reasoning',text:'Inspecting the current configuration',version:'r1'},
    {key:'reason-final',order:3,role:'assistant',kind:'agentMessage',phase:'final_answer',text:'Finished',version:'r1'},
  ];
  const reasoned=new ChatTimeline({url:action=>'/fixture/'+action+'?host=local',request:async()=>{},renderMeta:()=>{},renderText:(node,text)=>node.textContent=text,status:()=>{}});
  reasoned.apply({...page(reasoningRows,5),rows:reasoningRows},true);await frame();
  check(reasoned.container.querySelector('.activity-group>summary').textContent==='已运行 echo before-thinking','A thought followed by text falls back to the nearest prior step');
  check(reasoned.nodes.get('reasoning-latest').node.querySelector(':scope>summary').textContent==='思考摘要','The secondary reasoning fold retains the generic title');
  const belowOnly=reasoningRows.slice(1).map((row,order)=>({...row,order}));
  reasoned.apply({...page(belowOnly,6),rows:belowOnly},true);await frame();
  check(reasoned.container.querySelector('.activity-group>summary').textContent==='已运行 echo before-thinking','A thought followed by text without preceding text falls back to the nearest prior step');
  check(reasoned.nodes.get('reasoning-latest').node.querySelector(':scope>summary').textContent==='思考摘要','Falling back the primary fold does not remove the secondary thought summary');
  const latestOnly=belowOnly.slice(0,-1);
  reasoned.apply({...page(latestOnly,7),rows:latestOnly},true);await frame();
  check(reasoned.container.querySelector('.activity-group>summary').textContent==='Inspecting the current configuration','The latest primary fold uses reasoning content as its summary');
  const reasoningOnly=[
    {key:'reason-before',order:0,role:'activity',kind:'reasoning',text:'First reasoning step',version:'r2'},
    {key:'reasoning-latest',order:1,role:'activity',kind:'reasoning',text:'Inspecting without a prior tool step',version:'r2'},
    {key:'reason-final',order:2,role:'assistant',kind:'agentMessage',phase:'final_answer',text:'Finished',version:'r2'},
  ];
  reasoned.apply({...page(reasoningOnly,8),rows:reasoningOnly},true);await frame();
  check(reasoned.container.querySelector('.activity-group>summary').textContent==='Inspecting without a prior tool step','A thought followed by text uses reasoning content when no prior step exists');
  check(reasoned.nodes.get('reasoning-latest').node.querySelector(':scope>summary').textContent==='思考摘要','Reasoning-only fallback still retains the secondary generic title');
  reasoned.dispose();reasoned.container.replaceChildren();
  const backfill=new ChatTimeline({url:action=>'/fixture/'+action+'?host=local',request:async()=>{requested++;return page(rows);},renderMeta:()=>{},renderText:(node,text)=>node.textContent=text,status:()=>{}});
  backfill.setVisibility({showReasoning:false,showProcess:false});requested=0;
  backfill.apply(page([{...rows[2],key:'latest-command',order:20}],4,true),true);
  await frame();await frame();
  check(requested===0,'Hiding activity does not silently download old history');
  await backfill.loadOlder(100);
  check(requested===1&&!backfill.nodes.get('final').node.hidden,'Earlier replies remain available on demand');
  check(!backfill.hasMore,'Filtered history still stops at the end');
  backfill.dispose();
  const empty=new ChatTimeline({url:action=>'/fixture/'+action+'?host=local',request:async()=>page([{key:'empty-image',role:'activity',kind:'imageView',title:'图片预览',text:'',order:0,version:'v1',truncated:false}],1),renderMeta:()=>{},renderText:(node,text)=>node.textContent=text,status:()=>{}});
  empty.apply(page([{key:'empty-image',role:'activity',kind:'imageView',title:'图片预览',text:'',order:0,version:'v1',truncated:false}],1),true);
  const emptyBody=empty.nodes.get('empty-image').node.querySelector('.activity-body');
  check(emptyBody.hidden&&getComputedStyle(emptyBody).display==='none','Empty image preview body does not reserve space');
  empty.dispose();
  return checks;
}

async function runTimelineCacheTests() {
  const checks=[],check=(ok,label)=>{if(!ok)throw Error(label);checks.push(label);};
  ChatTimeline.clearCache();
  const row={key:'cached-row',order:0,role:'assistant',kind:'agentMessage',text:'Cached reply',version:'v1',forkable:true,turnStatus:'completed'};
  const page={rows:[row],sequence:9,epoch:'old',before:'old.cached-row',hasMore:true,meta:{requests:[{id:'old-approval'}],access:{canEditMessages:true}},files:[]};
  let renders=0,resolve;
  const options={url:action=>'/fixture/'+action+'?host=local',cacheKey:'gateway-a|auth-a|local|chat-a',
    renderMeta:()=>renders++,renderText:(node,text)=>node.textContent=text,status:()=>{},
    request:()=>new Promise(done=>resolve=done)};
  const first=new ChatTimeline(options);first.apply(page,true);first.dispose();first.container.replaceChildren();renders=0;
  const reopened=new ChatTimeline(options);const opening=reopened.start();
  check(reopened.container.textContent.includes('Cached reply'),'Returning to a chat paints cached text before the network replies');
  check(renders===0&&!reopened.canEdit()&&!reopened.pendingRequests,'Cached presentation cannot restore permissions or approvals');
  reopened.updates=()=>{};
  resolve({...page,rows:[],sequence:0,epoch:'new',meta:{loadingHistory:true,requests:[]}});await opening;
  check(reopened.container.textContent.includes('Cached reply')&&reopened.cached,'Pending owner discovery does not erase cached history');
  reopened.apply({...page,rows:[{...row,text:'Current reply',version:'v2'}],sequence:1,epoch:'new',meta:{requests:[]}},true);
  check(reopened.container.textContent.includes('Current reply')&&!reopened.cached,'Live history replaces stale cached content');
  reopened.dispose();reopened.container.replaceChildren();
  const foreign=new ChatTimeline({...options,cacheKey:'gateway-b|auth-a|local|chat-a'});foreign.start();
  check(!foreign.container.textContent.includes('Current reply'),'Another gateway cannot reuse this chat cache');foreign.dispose();
  ChatTimeline.clearCache();
  const loggedOut=new ChatTimeline(options);loggedOut.start();
  check(!loggedOut.container.textContent.includes('Current reply'),'Logout/account cache clearing removes retained messages');loggedOut.dispose();
  return checks;
}

async function runTimelineNativeFileChangesTests(){
  const checks=[];
  const check=(value,label)=>{if(!value)throw Error(label);checks.push(label);};
  document.getElementById('login').hidden=true;
  document.getElementById('welcome').hidden=true;
  document.getElementById('app').hidden=false;
  document.getElementById('chat').hidden=false;
  document.getElementById('app').classList.add('chat-open');
  document.getElementById('timeline').style.cssText='height:400px;flex:none;overflow:auto;width:100%;padding:20px';
  const frame=()=>new Promise(resolve=>requestAnimationFrame(resolve));
  const page=(from,to,sequence)=>({rows:[],sequence,epoch:'native-fixture',before:null,hasMore:false,meta:{requests:[]},files:[]});
  const timeline=new ChatTimeline({url:action=>'/fixture/'+action+'?host=local',request:async()=>{},renderMeta:()=>{},renderText:(node,text)=>node.textContent=text,status:()=>{}});
  timeline.container.replaceChildren();
  const nativePatch='@@ -1 +1 @@\n-old\n+new\n'+'+long patch line\n'.repeat(2000);
  const nativeFooter={key:'native-footer',order:322,role:'fileChanges',kind:'fileChanges',version:'patch-v1',text:'',truncated:true,
    fileSummary:{count:2,added:3,removed:1,basis:'recorded',files:[{path:'/repo/space name.py',added:2,removed:1},{path:'/repo/new.py',added:1,removed:0}]}};
  timeline.apply({...page(0,0,4),rows:[nativeFooter]});
  timeline.setVisibility({showProcess:false});
  const card=timeline.nodes.get('native-footer').node;
  check(!card.hidden&&card.classList.contains('edited-files-card'),'Native edited-files card stays visible with process hidden');
  check(card.querySelector('.edited-files-label').textContent.includes('/repo/space name.py'),'Native file summary includes the first changed path');
  check(getComputedStyle(card.querySelector('.edited-files-label')).textOverflow==='ellipsis','Native file summary truncates to one line');
  check(card.getBoundingClientRect().width>250&&card.scrollWidth<=card.clientWidth,'Native card fits the available message width');
  check(card.querySelector('.edited-files-basis').textContent===timelineText('累计修改'),'Recorded patch totals are labeled separately from net edits');
  check(!card.open&&card.querySelector('.edited-files-list').textContent.includes('/repo/space name.py'),'Native card starts collapsed and contains per-file paths and counts');
  let detailCalls=0;
  timeline.request=async()=>{detailCalls++;return {key:'native-footer',version:'patch-v1',offset:0,text:nativePatch.slice(0,16000),next:16000};};
  check(!card.textContent.includes('long patch line'),'Initial native card excludes patch body');
  card.open=true;
  await frame();await frame();
  check(detailCalls===0,'Opening a file summary never fetches patch details, even with old cached metadata');
  check(!card.querySelector('.activity-body')&&!card.querySelector('.detail-more'),'File summary contains no patch body or detail controls');
  check(card.querySelectorAll('.edited-files-list li').length===2&&card.querySelector('.edited-files-list').textContent.includes('+2 −1'),'Expanded card shows file paths and line counts only');
  card.open=false;await frame();card.open=true;await frame();
  check(detailCalls===0,'Reopening the summary does not request any details');
  timeline.apply({...page(0,0,5),rows:[{...nativeFooter,version:'patch-v2',fileSummary:{...nativeFooter.fileSummary,added:4,files:[{...nativeFooter.fileSummary.files[0],added:3},nativeFooter.fileSummary.files[1]]}}]});
  check(timeline.nodes.get('native-footer').node.querySelector('.edited-files-added').textContent==='+4','Native summary refreshes totals on update');
  check(timeline.nodes.get('native-footer').node.open,'Summary updates retain the expanded state');
  check(detailCalls===0,'Summary updates need no patch request');
  timeline.dispose();
  return checks;
}
