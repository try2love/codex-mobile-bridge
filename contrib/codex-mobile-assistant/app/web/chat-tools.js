'use strict';

function describeChatError(value){
  const raw=typeof value==='string'?value:JSON.stringify(value,null,2)||'';
  let message=value;
  for(let depth=0;depth<8;depth++){
    if(typeof message==='string'){
      try{const parsed=JSON.parse(message);if(parsed===message)break;message=parsed;continue;}catch{break;}
    }
    if(!message||typeof message!=='object')break;
    const next=message.error??message.message;
    if(next===undefined)break;message=next;
  }
  const source=raw.toLowerCase();
  let title='任务暂时没有完成',hint='可以点击“继续完成当前任务”重试；如果仍然失败，请展开技术详情并反馈。';
  if(/invalid_encrypted_content/.test(source)){
    title='部分历史内容无法识别';hint='当前模型服务无法校验这条对话的部分历史内容。可以先尝试继续；若反复失败，请切换回原来的模型服务，或新建对话并说明任务进度。';
  }else if(/context_length_exceeded|maximum context|context window|too many tokens|prompt is too long/.test(source)){
    title='对话内容超过模型容量';hint='请压缩或总结历史内容，或新建对话并带上任务摘要后继续。';
  }else if(/insufficient_quota|quota.*exceed|credit.*(low|insufficient)|balance/.test(source)){
    title='模型服务额度不足';hint='请检查模型服务的余额或额度，恢复后再继续任务。';
  }else if(/invalid_api_key|authentication|unauthorized|\b401\b/.test(source)){
    title='模型服务登录校验失败';hint='请在电脑上检查模型服务的登录状态或 API 密钥，再继续任务。';
  }else if(/permission_denied|forbidden|\b403\b/.test(source)){
    title='当前账号没有访问权限';hint='请检查账号是否有权使用当前模型或资源，必要时在“模型设置”中更换模型。';
  }else if(/rate_limit|too many requests|\b429\b/.test(source)){
    title='请求过于频繁';hint='模型服务暂时限制了请求次数，稍等片刻再继续任务。';
  }else if(/model_not_found|model.*does not exist|unsupported model/.test(source)){
    title='当前模型不可用';hint='请在“模型设置”中选择服务支持的模型，然后继续任务。';
  }else if(/timed?\s*out|timeout|超时/.test(source)){
    title='等待模型回复超时';hint='模型服务或网络响应较慢，请稍后继续任务。';
  }else if(/\b50[0234]\b|overloaded|service unavailable|internal.server.error/.test(source)){
    title='模型服务暂时异常';hint='服务端未能完成本次请求，可以稍后继续任务。';
  }else if(/network|connection.*(reset|refused|closed)|failed to fetch|dns|连接.*断开/.test(source)){
    title='连接中断';hint='请检查电脑网络和模型服务连接，恢复后再继续任务。';
  }else if(/invalid_request|bad response status code 400|\b400\b/.test(source)){
    title='模型服务未接受这次请求';hint='可能是模型设置或历史内容不兼容。可以先尝试继续；若反复失败，请检查模型设置。';
  }else if(typeof message==='string'&&message.length<=160&&/[\u4e00-\u9fff]/.test(message)&&!/[{}]/.test(message)){
    title=message;
  }
  return {title,hint,raw};
}

function renderChatError(container,value){
  const info=describeChatError(value),expanded=!!container.querySelector('.error-details')?.open;
  container.replaceChildren(el('strong','error-title',info.title),el('p','error-help',info.hint));
  const details=el('details','error-details');details.open=expanded;
  details.append(el('summary','','查看技术详情'),el('pre','',info.raw));container.append(details);
}

async function continueCurrentTask(button,id,host,turnId){
  if(sending||preparingImages){toast('请等待当前消息发送完成');return;}
  if(currentId!==id||currentHost!==host){toast('请先打开这条对话');return;}
  const storageKey='continue-task:'+chatKey(id,host)+':'+turnId;
  let pending;
  try{pending=JSON.parse(sessionStorage.getItem(storageKey)||'null');}catch{}
  if(pending?.status==='accepted'||pending?.status==='queued'){toast('继续请求已发送，请等待回复');return;}
  sending=true;button.disabled=true;button.textContent='正在发送…';$('send').disabled=true;
  try{
    const live=state?.connected?state:await openDesktopAndReconnect(id,host);
    if(currentId!==id||currentHost!==host)throw Error('对话已切换，请回到原对话继续');
    if(!pending)pending={id:uuid(),text:'继续完成当前任务',mode:live.status==='active'?'queue':'send',skills:[],images:[],files:[]};
    sessionStorage.setItem(storageKey,JSON.stringify(pending));
    const result=await api(sessionUrl(id,'send',host),{id:pending.id,text:pending.text,mode:pending.mode,skills:[],images:[],files:[]});
    pending.status=result.status;sessionStorage.setItem(storageKey,JSON.stringify(pending));
    if(result.status==='unknown'){toast('请求可能已送达，请先查看对话；再次点击不会重复发送。');return;}
    readTaskReminders(id,host);
    toast(result.status==='queued'?'继续请求已排队，当前任务结束后发送':'已发送：继续完成当前任务');
  }catch(error){
    const message=String(error.message||'');
    toast(message.length<=160&&/[\u4e00-\u9fff]/.test(message)&&!/[{}]/.test(message)?message:describeChatError(message).title);
  }finally{
    sending=false;button.textContent='继续完成当前任务';button.disabled=false;
    $('send').disabled=!state?.connected;renderImagePreviews();
  }
}

async function copyChatText(text){
  try{await navigator.clipboard.writeText(text);toast('已复制');}
  catch{
    let dialog=document.getElementById('copy-text-dialog');
    if(!dialog){
      dialog=el('dialog','picker');dialog.id='copy-text-dialog';
      const title=el('h2','','复制内容'),hint=el('p','muted','自动复制不可用，请长按或全选下面的文字复制。');
      title.id='copy-text-title';dialog.setAttribute('aria-labelledby',title.id);
      const box=el('textarea');box.readOnly=true;box.rows=10;box.setAttribute('aria-label','待复制内容');
      const close=el('button','primary','关闭');close.type='button';close.onclick=()=>dialog.close();
      dialog.append(title,hint,box,close);document.body.append(dialog);
    }
    dialog.querySelector('textarea').value=text;dialog.showModal();
    dialog.querySelector('textarea').select();
  }
}

function editChatMessage(text,hasAttachments){
  if(sending||preparingImages){toast('请等待当前消息发送或图片处理完成');return;}
  if(text.length>100000){toast('这条消息太长，请复制后分段发送');return;}
  if(($('message').value.trim()||(imageDrafts.get(chatKey())||[]).length)&&
     !window.confirm('输入框中还有未发送的内容。要用这条历史消息替换草稿吗？'))return;
  imageDrafts.set(chatKey(),[]);persistImageDraft();renderImagePreviews();
  $('message').value=text;saveDraft();resizeMessage();
  $('send-error').textContent='';$('send-retry').hidden=true;
  $('edit-message-note').hidden=false;
  $('edit-message-note').textContent='修改后将作为新消息发送，原记录保留。'+(hasAttachments?' 请重新添加原消息的附件。':'');
  $('message').focus();
}

const chatSearch={query:'',keys:[],selected:null,timer:null};
function resetChatSearch(){
  clearTimeout(chatSearch.timer);
  chatSearch.query='';chatSearch.keys=[];chatSearch.selected=null;
  if(!$('chat-search-panel'))return;
  $('chat-search-input').value='';$('chat-search-panel').hidden=true;
  $('chat-search-toggle').setAttribute('aria-expanded','false');
}
function refreshChatSearch(jump=false){
  if(!$('chat-search-panel')||$('chat-search-panel').hidden||!chatTimeline)return;
  const query=$('chat-search-input').value.trim().toLocaleLowerCase();
  chatSearch.query=query;
  chatSearch.keys=[...chatTimeline.rows.values()].map(entry=>entry.row).sort((a,b)=>a.order-b.order)
    .filter(row=>query&&String(chatTimeline.details.get(row.key)?.text??row.text??'').toLocaleLowerCase().includes(query))
    .map(row=>row.key);
  if(!chatSearch.keys.includes(chatSearch.selected))chatSearch.selected=chatSearch.keys[0]||null;
  for(const [key,entry] of chatTimeline.nodes)entry.node.classList.toggle('search-current',key===chatSearch.selected);
  const index=chatSearch.keys.indexOf(chatSearch.selected);
  $('chat-search-count').textContent=!query?'输入关键词':index<0?'已加载内容中无匹配':`${index+1} / ${chatSearch.keys.length} 条消息`;
  $('chat-search-prev').disabled=$('chat-search-next').disabled=!chatSearch.keys.length;
  $('chat-search-older').hidden=!chatTimeline.hasMore;
  $('chat-search-older').disabled=chatTimeline.busy;
  if(jump&&chatSearch.selected)jumpChatSearch(0);
}
function jumpChatSearch(direction){
  if(!chatTimeline||!chatSearch.keys.length)return;
  const index=chatSearch.keys.indexOf(chatSearch.selected);
  chatSearch.selected=chatSearch.keys[(index+direction+chatSearch.keys.length)%chatSearch.keys.length];
  const node=chatTimeline.nodes.get(chatSearch.selected)?.node;if(!node)return;
  chatTimeline.following=false;
  if(node.tagName==='DETAILS')node.open=true;
  chatTimeline.viewport.scrollTop+=node.getBoundingClientRect().top-chatTimeline.viewport.getBoundingClientRect().top-12;
  chatTimeline.lastTop=chatTimeline.viewport.scrollTop;
  chatTimeline.anchor=chatTimeline.capture();
  refreshChatSearch();
}

function initChatTools(){
  const toggle=el('button','tool-button');toggle.innerHTML='<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/></svg>';toggle.title='搜索当前聊天';toggle.id='chat-search-toggle';toggle.type='button';
  toggle.setAttribute('aria-label','搜索当前聊天');toggle.setAttribute('aria-expanded','false');toggle.setAttribute('aria-controls','chat-search-panel');
  $('chat-menu').before(toggle);
  const panel=el('section','chat-search-panel');panel.id='chat-search-panel';panel.hidden=true;panel.setAttribute('aria-label','聊天内搜索');
  const line=el('div','chat-search-line'),input=el('input');input.id='chat-search-input';input.type='search';
  input.placeholder='搜索当前聊天';input.setAttribute('aria-label','聊天搜索关键词');
  const count=el('output');count.id='chat-search-count';count.setAttribute('aria-live','polite');
  line.append(input);
  for(const [id,label,action] of [
    ['chat-search-prev','上一条',()=>jumpChatSearch(-1)],
    ['chat-search-next','下一条',()=>jumpChatSearch(1)],
    ['chat-search-close','关闭',()=>{
      panel.hidden=true;toggle.setAttribute('aria-expanded','false');
      for(const entry of chatTimeline?.nodes.values()||[])entry.node.classList.remove('search-current');
      toggle.focus();
    }],
  ]){const button=el('button','plain',label);button.id=id;button.type='button';button.onclick=action;line.append(button);}
  const foot=el('div','chat-search-foot'),older=el('button','plain','加载更早内容');older.id='chat-search-older';older.type='button';
  older.onclick=async()=>{const timeline=chatTimeline;older.disabled=true;await timeline?.loadOlder(100);if(timeline===chatTimeline)refreshChatSearch();};
  foot.append(count,older);
  panel.append(line,foot,el('small','muted','仅搜索已加载的正文；长消息可先展开完整内容。'));
  $('chat').querySelector('.chat-head').after(panel);
  toggle.onclick=()=>{panel.hidden=!panel.hidden;toggle.setAttribute('aria-expanded',String(!panel.hidden));
    if(!panel.hidden){refreshChatSearch();input.focus();}else for(const entry of chatTimeline?.nodes.values()||[])entry.node.classList.remove('search-current');};
  input.oninput=()=>{clearTimeout(chatSearch.timer);chatSearch.timer=setTimeout(()=>refreshChatSearch(true),180);};
  input.onkeydown=event=>{if(event.key==='Enter'){event.preventDefault();clearTimeout(chatSearch.timer);refreshChatSearch();jumpChatSearch(event.shiftKey?-1:1);}if(event.key==='Escape')$('chat-search-close').click();};
  const retry=el('button','plain','重试发送');retry.id='send-retry';retry.type='button';retry.hidden=true;
  retry.onclick=()=>$('composer').requestSubmit();$('send-error').after(retry);
  const note=el('p','muted');note.id='edit-message-note';note.hidden=true;$('message').before(note);
}
