// Production renderer with native-shaped synthetic rows; no model or account calls.
async function runTurnFoldBrowserChecks(){
  await BridgeFeatures.load('chat');
  const checks=[],check=(value,label)=>{if(!value)throw Error(label);checks.push(label);};
  const root=document.createElement('div');root.style.cssText='position:fixed;inset:80px 0 0;z-index:40;background:var(--page)';
  const viewport=document.createElement('div'),container=document.createElement('div'),older=document.createElement('button'),newer=document.createElement('button');
  viewport.style.cssText='height:100%;overflow:auto;padding:16px';viewport.append(container);root.append(viewport,older,newer);document.body.append(root);
  const timeline=new ChatTimeline({url:action=>'/synthetic/'+action+'?',elements:{viewport,container,older,newer},
    renderMeta(){},renderText:(node,text)=>renderMarkdown(node,text,[]),status(){},request:async()=>{throw Error('Unexpected history/detail request');}});
  let sequence=0;
  const start=1700000000000,rows=[
    {key:'input',role:'user',text:'Initial question'},
    {key:'thought',role:'activity',kind:'reasoning',text:'**Native reasoning**',model:'historical-model',effort:'off'},
    {key:'progress',role:'assistant',phase:'commentary',text:'**Progress update**',model:'old-progress-model',effort:'high'},
    {key:'response',role:'user',interaction:'response',text:'Selected option: continue'},
    {key:'tool',role:'activity',kind:'commandExecution',text:'echo fixture'},
    {key:'supplement',role:'user',text:'Keep this supplement visible'},
    {key:'answer',role:'assistant',phase:'final_answer',text:'Final answer remains visible',timestamp:start+123000},
  ].map((row,index)=>({...row,order:index,version:'v1',turnId:'native-turn',turnStatus:'inProgress',turnStartedAt:start}));
  const apply=(input,reset=false)=>timeline.apply({epoch:'fold-fixture',sequence:++sequence,rows:input,meta:{requests:[]},files:[],hasMore:false},reset);
  const node=key=>timeline.nodes.get(key).node,visible=key=>getComputedStyle(node(key)).display!=='none';
  try{
    apply(rows.map(row=>row.key==='answer'?{...row,text:''}:row),true);
    check(container.querySelector('.chat-turn-completion').disabled,'an empty final-answer record keeps the running indication');
    apply(rows);
    check(visible('thought')&&visible('progress')&&visible('tool'),'running process stays visible');
    const running=container.querySelector('.chat-turn-completion');
    check(!running.disabled&&!running.hasAttribute('aria-expanded')&&running.nextElementSibling===node('thought'),
      'running answer keeps the elapsed control before the first step');
    const style=key=>{const {color,fontSize,lineHeight}=getComputedStyle(node(key).querySelector('.message-body'));return [color,fontSize,lineHeight].join(' ');};
    check(style('progress')===style('answer'),'commentary uses final answer typography and theme color');
    check(node('thought').tagName==='DETAILS'&&!node('thought').open&&
      node('thought').querySelector('summary').textContent===BridgeI18n.t('思考摘要'),
      'reasoning retains its independent closed disclosure');
    node('thought').open=true;
    check(node('thought').querySelector('.activity-body').textContent==='**Native reasoning**'&&
      node('progress').querySelector('.who').textContent.endsWith('old-progress-model high'),
      'reasoning and recorded commentary labels retain their original content');
    const complete=rows.map(row=>({...row,turnStatus:'completed',turnCompletedAt:start+125000}));
    apply(complete);
    let control=container.querySelector('.chat-turn-completion');
    check(!visible('thought')&&!visible('progress')&&!visible('tool')&&!visible('response'),'native success collapses intermediate rows and answered choices');
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
    const failed=rows.filter(row=>row.key!=='answer').map(row=>({...row,turnStatus:'failed',turnError:'Native failure evidence'}));
    apply(failed,true);
    control=container.querySelector('.chat-turn-completion');
    let outcome=container.querySelector('.chat-turn-outcome');
    check(visible('tool')&&!control.disabled&&control.nextElementSibling===node('thought')&&
      !control.textContent.includes('Native failure evidence')&&outcome.previousElementSibling===node('tool')&&
      outcome.textContent==='Native failure evidence','failure stays expanded with its reason next to the last step');
    control.click();
    check(!visible('tool')&&getComputedStyle(outcome).display==='none','manual collapse also hides the last-step outcome');
    control.click();
    check(getComputedStyle(outcome).display!=='none','manual reopen restores the outcome');
    apply([...failed,{...failed.at(-1),key:'error',role:'error',order:9,text:'Native failure evidence'}],true);
    outcome=container.querySelector('.chat-turn-outcome');
    check(outcome===node('error')&&outcome.previousElementSibling===node('tool')&&
      container.textContent.split('Native failure evidence').length===2,'native terminal error is placed once beside the last step');
    apply(complete.filter(row=>['input','answer'].includes(row.key)),true);
    check(!container.querySelector('.chat-turn-completion')&&visible('answer'),'a completed answer without steps has no elapsed footer');
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
