// Run only in an isolated gateway browser page; no real chat requests are sent.
function runI18nTests(){
  const checks=[];const check=(value,label)=>{if(!value)throw Error(label);checks.push(label);};
  const previous=BridgeI18n.language(),originalState=state,originalDraft=$('message').value;
  const messageContent=()=>[...$('messages').querySelectorAll('.message-body')].map(n=>n.innerHTML).join('');
  const content=messageContent(),originalFetch=window.fetch;let requests=0;
  const fixture={host:'local',connected:true,status:'idle',historyComplete:true,title:'中文聊天内容',cwd:'/test',requests:[{id:'i18n-question',supported:true,method:'item/tool/requestUserInput',params:{questions:[{id:'choice',question:'原问题保持',options:[{label:'原选项保持'}]},{id:'text',question:'原自由输入问题'}]}}]};
  const change=language=>{$('phone-language').value=language;$('phone-language').dispatchEvent(new Event('change'));};
  try{
    window.fetch=()=>{requests++;throw Error('Language change must not send a request');};
    renderState(fixture);
    const select=$('approvals').querySelector('select');select.value='__custom__';select.dispatchEvent(new Event('change'));
    const fields=$('approvals').querySelectorAll('textarea');fields[0].value='保留自定义输入';fields[1].value='保留自由输入';$('message').value='保留消息草稿';
    change('en');
    check($('approvals').querySelector('h3').textContent==='Codex needs your reply','Approval controls switch to English');
    check($('approvals').querySelector('select').value==='__custom__','Custom selection survives');
    check(!$('approvals').querySelector('textarea').hidden,'Custom answer stays visible');
    check([...$('approvals').querySelectorAll('textarea')].map(n=>n.value).join('|')==='保留自定义输入|保留自由输入','Both answers survive');
    check($('chat-title').textContent==='中文聊天内容'&&$('approvals').textContent.includes('原问题保持'),'Chat title and question are not translated');
    check($('message').value==='保留消息草稿','Message draft survives');
    check(messageContent()===content,'Markdown and formulas are not rerendered');
    submittedRequestIds.add('i18n-question');change('zh');
    check(BridgeI18n.t('')==='', 'Empty UI text stays empty');
    check($('approvals').querySelector('h3').textContent==='Codex 需要你的回复','Controls switch back to Chinese');
    check($('approvals').querySelector('button').disabled,'Submitted approval stays disabled after language change');
    check(requests===0,'Switching languages sends no requests');
  }finally{
    window.fetch=originalFetch;submittedRequestIds.delete('i18n-question');change(previous);$('message').value=originalDraft;if(originalState){approvalStamp='';renderState(originalState);}
  }
  return {passed:checks.length,checks};
}
