// Production renderer with native-shaped synthetic rows; no model or account calls.
async function runTurnFoldBrowserChecks(){
  await BridgeFeatures.load('chat');
  const checks=[],check=(value,label)=>{if(!value)throw Error(label);checks.push(label);};
  const root=document.createElement('div');root.style.cssText='position:fixed;inset:80px 0 0;z-index:40;background:var(--page)';
  const viewport=document.createElement('div'),container=document.createElement('div'),older=document.createElement('button'),newer=document.createElement('button');
  viewport.style.cssText='height:100%;overflow:auto;padding:16px';viewport.append(container);root.append(viewport,older,newer);document.body.append(root);
  const timeline=new ChatTimeline({url:action=>'/synthetic/'+action+'?',elements:{viewport,container,older,newer},
    renderMeta(){},renderText:(node,text)=>node.textContent=text,status(){},request:async()=>{throw Error('Unexpected history/detail request');}});
  let sequence=0;
  const start=1700000000000,rows=[
    {key:'input',role:'user',text:'Initial question'},
    {key:'thought',role:'activity',kind:'reasoning',text:'Native reasoning'},
    {key:'progress',role:'assistant',phase:'commentary',text:'Progress update'},
    {key:'tool',role:'activity',kind:'commandExecution',text:'echo fixture'},
    {key:'supplement',role:'user',text:'Keep this supplement visible'},
    {key:'answer',role:'assistant',phase:'final_answer',text:'Final answer remains visible'},
  ].map((row,index)=>({...row,order:index,version:'v1',turnId:'native-turn',turnStatus:'inProgress',turnStartedAt:start}));
  const apply=(input,reset=false)=>timeline.apply({epoch:'fold-fixture',sequence:++sequence,rows:input,meta:{requests:[]},files:[],hasMore:false},reset);
  const node=key=>timeline.nodes.get(key).node,visible=key=>getComputedStyle(node(key)).display!=='none';
  try{
    apply(rows,true);
    check(visible('thought')&&visible('progress')&&visible('tool'),'running process stays visible');
    check(container.querySelector('.chat-turn-completion').disabled,'running process cannot be collapsed');
    const complete=rows.map(row=>({...row,turnStatus:'completed',turnCompletedAt:start+125000}));
    apply(complete);
    let control=container.querySelector('.chat-turn-completion');
    check(!visible('thought')&&!visible('progress')&&!visible('tool'),'native success collapses only intermediate rows');
    check(visible('input')&&visible('supplement')&&visible('answer'),'inputs, supplement and final answer remain visible');
    check([...container.children].filter(n=>n.dataset.key&&!n.classList.contains('chat-turn-completion')).map(n=>n.dataset.key).join(',')===rows.map(r=>r.key).join(','),'native row order is retained without wrappers');
    check(control.textContent.includes('2')&&control.textContent.includes('5'),'completion uses the native 125 second duration');
    const tool=node('tool');control.click();apply(complete);
    check(control===container.querySelector('.chat-turn-completion')&&node('tool')===tool&&visible('tool'),'refresh keeps manual expansion and process node identity');
    control.click();apply(complete);
    check(!visible('tool')&&container.querySelectorAll('.chat-turn-completion').length===1,'manual collapse is retained without duplicate controls');
    for(const status of ['failed','interrupted']){
      apply(rows.map(row=>({...row,turnStatus:status})),true);
      check(visible('thought')&&visible('tool')&&visible('answer'),status+' preserves the process and answer');
      check(container.querySelector('.chat-turn-completion').disabled,status+' cannot conceal the error context');
    }
    apply(complete.map(({turnStartedAt,turnCompletedAt,...row})=>row),true);
    check(container.querySelector('.chat-turn-completion').textContent===BridgeI18n.t('已完成')+' ▾','missing clocks do not produce a duration');
    apply(complete.map(({phase,forkable,...row})=>row),true);
    check(visible('thought')&&visible('tool'),'missing final answer identity keeps the process visible');
    apply(complete.map(row=>({...row,turnCompletionKnown:false})),true);
    check(visible('thought')&&!container.querySelector('.chat-turn-completion'),'idle fallback cannot collapse or claim completion');
    apply(complete.slice(3),true);control=container.querySelector('.chat-turn-completion');control.click();
    apply(complete);
    check(container.querySelectorAll('.chat-turn-completion').length===1&&visible('thought')&&visible('tool'),'backfill enriches the existing expanded native turn');
    apply(complete,true);
    check(!visible('tool'),'a new conversation does not inherit the old expansion');
    return {passed:checks.length,checks};
  }finally{timeline.dispose();root.remove();}
}
