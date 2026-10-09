// Browser integration checks. All send/respond calls are synthetic.
async function runModeTests(){
  const checks=[],writes=[],original=window.fetch;
  const id='11111111-1111-4111-8111-111111111111';
  let fail=false,hold=null;
  const meta={id,title:'Modes fixture',host:'local',connected:true,loadingHistory:false,historyComplete:true,status:'idle',model:'fixture-model',provider:'fixture',requests:[],submissions:[],collaborationMode:'default'};
  const response=(data,status=200)=>({ok:status===200,status,json:async()=>data});
  const check=(value,label)=>{if(!value)throw Error(label);checks.push(label);};
  window.fetch=async(url,options)=>{
    if(url.includes('/reconnect'))return response({ok:true});
    if(url.includes('/notifications'))return response({available:false});
    if(url.includes('/timeline'))return response({sequence:1,epoch:'test',rows:[],hasMore:false,before:'',files:[],meta});
    if(url.includes('/changes'))return new Promise((resolve,reject)=>options.signal.addEventListener('abort',()=>reject(new DOMException('aborted','AbortError')),{once:true}));
    if(/\/(send|respond)\?/.test(url)){
      writes.push({url,body:JSON.parse(options.body)});
      if(hold)return new Promise(resolve=>hold(resolve));
      return fail?response({error:'fixture failure'},409):response({status:'accepted'});
    }
    throw Error('Unexpected fixture request: '+url);
  };
  try{
    await openChat(id,'remote:fixture');
    const select=$('work-mode');select.value='plan';select.onchange();$('message').value='Plan fixture';fail=true;
    await $('composer').onsubmit({preventDefault(){}});
    check(writes[0].body.workMode==='plan','Plan selected in the real composer reaches send API');
    check(writes[0].url.includes('host=remote%3Afixture'),'Mode sends retain SSH host');
    check($('message').value==='Plan fixture'&&$('send-error').textContent==='fixture failure','Failed mode send preserves draft and error');
    fail=false;await $('composer').onsubmit({preventDefault(){}});
    check(writes[0].body.id===writes[1].body.id,'Retry retains the submission id');
    check($('message').value==='','Successful mode send clears only the sent draft');
    select.value='goal';select.onchange();$('message').value='Goal fixture';
    await $('composer').onsubmit({preventDefault(){}});
    check(writes[2].body.workMode==='goal'&&select.value==='default','Goal reaches send API and next message returns to ordinary mode');
    check(writes[2].body.uiLocale==='zh-CN','Goal sends retain the active UI locale');
    renderState({...meta,status:'active',host:'remote:fixture'});$('send-mode').value='steer';$('send-mode').onchange();$('message').value='Additional context';
    await $('composer').onsubmit({preventDefault(){}});
    check(writes[3].body.workMode===null&&writes[3].body.mode==='steer','Steering never silently changes the active mode');
    const request={id:'implement-plan:test',method:'item/plan/requestImplementation',supported:true,params:{planContent:'# Fixture plan\n\nReply OK.',turnId:'test'}};
    renderState({...meta,host:'remote:fixture',requests:[request]});
    check($('approvals').textContent.includes('执行计划')&&!$('approvals').textContent.includes('请在桌面 App'),'Plan confirmation offers a working action instead of desktop fallback');
    let card=$('approvals').firstElementChild;await card.querySelector('.secondary').onclick();
    check(writes.length===4&&card.querySelector('.error').textContent.includes('修改意见'),'Revision requires feedback without submitting an empty request');
    card.querySelector('textarea').value='Shorter please';await card.querySelector('.secondary').onclick();
    check(writes[4].body.response.action==='revise'&&writes[4].body.response.text==='Shorter please','Revision submits the actual requested changes');
    check(select.value==='plan','Revision continues in plan mode');
    renderApprovals([{...request,id:'implement-plan:next'}]);card=$('approvals').firstElementChild;
    await card.querySelector('.primary').onclick();await card.querySelector('.primary').onclick();
    check(writes.length===6&&writes[5].body.response.action==='implement','Repeated plan clicks submit one implementation');
    check(select.value==='default','Implementation returns composer to ordinary mode');
    renderState({...meta,goalSubmission:{status:'pending'}});
    check($('goal-state').textContent.includes('等待桌面确认')&&!$('goal-state').textContent.includes('目标进行中'),'Accepted goal request does not imply an active native goal');
    renderState({...meta,goal:{objective:'Real goal from desktop',status:'active',tokensUsed:7}});
    check($('goal-state').textContent.includes('Real goal from desktop')&&$('goal-state').textContent.includes('目标进行中'),'Native state controls the visible active goal');
    const goal=state.goal,writesBeforeHide=writes.length;
    $('goal-state').querySelector('.goal-close').click();
    check($('goal-state').hidden&&!$('goal-toggle').hidden&&writes.length===writesBeforeHide&&goal.status==='active','Hiding only changes visibility and makes the restore action available');
    renderState({...meta,goal:{...goal,tokensUsed:10},model:'kimi-k3-local-w8a8',effort:'xhigh'});
    check($('goal-state').hidden&&!$('goal-toggle').hidden,'Goal updates preserve the hidden preference');
    check($('model-name').textContent==='kimi-k3-local-w8a8'&&$('model-effort').textContent==='· xhigh','Model name and reasoning effort render separately');
    check($('model-button').title==='kimi-k3-local-w8a8 · xhigh'&&$('model-current').textContent.includes('kimi-k3-local-w8a8'),'Full model ID remains available in the tooltip and model panel');
    BridgeI18n.setLanguage('en');BridgeI18n.apply();workModes.render();
    check($('goal-toggle').title==='Goal progress','Hidden-goal restore action translates to English');
    $('goal-toggle').click();
    check(!$('goal-state').hidden&&$('goal-toggle').hidden&&writes.length===writesBeforeHide,'Restoring shows the original goal without sending a message');
    check($('send-mode').value==='auto'&&$('send-mode').options[0].textContent==='默认'&&$('send-mode').options[1].textContent==='引导','Mode selectors use compact centered labels');
    renderState({...meta,status:'active'});$('message').value='Queue fixture';
    await $('composer').onsubmit({preventDefault(){}});
    check(writes.at(-1).body.mode==='queue'&&writes.at(-1).body.workMode==='default','Default mode queues when a task is active');
    check(writes.at(-1).body.uiLocale==='en-US','Queued messages retain the active UI locale');
    renderState({...meta,status:'idle'});$('message').value='Send fixture';
    await $('composer').onsubmit({preventDefault(){}});
    check(writes.at(-1).body.mode==='send'&&writes.at(-1).body.workMode==='default','Default mode sends immediately when idle');
    BridgeI18n.setLanguage('zh');BridgeI18n.apply();
    check($('goal-state').textContent.includes('Goal active')&&$('work-mode').options[1].textContent==='计划','Mode and native goal controls translate to localized labels');
    return {passed:checks.length,checks};
  }finally{chatTimeline?.dispose();chatTimeline=null;window.fetch=original;}
}
