'use strict';
const actionText=source=>typeof BridgeI18n==='undefined'?source:BridgeI18n.t(source);
window.BridgeClipboard={
  async copy(getText,button){
    if(button.disabled)return;
    const label=button.textContent;button.disabled=true;button.textContent=actionText('正在读取…');
    try{
      const text=await getText();
      try{
        if(!navigator.clipboard?.writeText)throw Error('Clipboard unavailable');
        await navigator.clipboard.writeText(text);
        button.textContent=actionText('已复制');
        setTimeout(()=>{button.textContent=label;},1500);
      }catch{
        const dialog=document.createElement('dialog');dialog.className='picker copy-dialog';
        const heading=document.createElement('h2');heading.textContent=actionText('复制内容');
        const help=document.createElement('p');help.className='muted';help.textContent=actionText('浏览器限制自动复制，请长按选中文本复制，或使用系统复制快捷键。');
        const input=document.createElement('textarea');input.readOnly=true;input.value=text;input.setAttribute('aria-label',actionText('复制内容'));
        const close=document.createElement('button');close.className='primary';close.textContent=actionText('完成');close.onclick=()=>dialog.close();
        dialog.append(heading,help,input,close);document.body.append(dialog);
        dialog.onclose=()=>{dialog.remove();button.focus();};dialog.showModal();input.focus();input.select();input.setSelectionRange(0,text.length);
        button.textContent=label;
      }
    }catch(error){button.textContent=label;document.dispatchEvent(new CustomEvent('bridge-message-error',{detail:error.message}));}
    finally{button.disabled=false;}
  }
};

class MessageActions {
  constructor({request,openChat,refresh,uuid,notify,busy}){
    Object.assign(this,{request,openChat,refresh,uuid,notify,busy});
    this.dialog=document.getElementById('message-action-dialog');
    this.title=document.getElementById('message-action-heading');
    this.description=document.getElementById('message-action-description');
    this.input=document.getElementById('message-action-text');
    this.error=document.getElementById('message-action-error');
    this.submit=document.getElementById('message-action-submit');
    this.form=document.getElementById('message-action-form');
    this.form.onsubmit=event=>{event.preventDefault();this.send();};
    this.dialog.addEventListener('cancel',event=>{if(this.pending)event.preventDefault();});
    this.dialog.addEventListener('close',()=>{if(!this.pending)this.target=null;});
    this.input.oninput=()=>{if(this.target)sessionStorage.setItem(this.draftKey(),this.input.value);};
  }
  relabel(){if(!this.target)return;for(const node of [this.title,this.description,this.submit,this.error])node.textContent=actionText(node.textContent);}
  draftKey(){return 'message-edit:'+this.target.url+':'+this.target.row.key;}
  async open(action,row,timeline){
    if(this.pending)return;
    const target=this.target={action,row:{...row},timeline,url:timeline.url('message-action')};
    this.error.textContent='';this.input.value='';this.input.hidden=action==='fork';this.input.required=action!=='fork';
    this.title.textContent=actionText(action==='fork'?'从这里分支':action==='edit'?'编辑并重新发送':'编辑并新建分支');
    this.submit.textContent=this.title.textContent;
    this.description.textContent=actionText(action==='edit'?'将替换这条消息并重新生成回答。已经执行的文件修改和命令不会撤销。':action==='fork'?'保留截至这一轮的上下文，创建后等待你继续输入。新会话与原会话共享工作目录。':'保留原会话，在新分支中修改并重新生成这一轮。新会话与原会话共享工作目录。');
    this.submit.disabled=true;this.dialog.showModal();
    try{
      const text=action==='fork'?'':await timeline.fullText(row);
      if(this.target!==target||!this.dialog.open)return;
      this.input.value=sessionStorage.getItem(this.draftKey())??text;
      this.submit.disabled=false;if(action!=='fork')this.input.focus();
    }catch(error){if(this.target===target)this.error.textContent=actionText(error.message);}
  }
  async send(){
    const target=this.target;if(!target||this.pending)return;
    const {action,row,timeline,url}=target;
    if(timeline.abort.signal.aborted){this.error.textContent=actionText('聊天已切换，请重新选择消息');return;}
    const text=action==='fork'?null:this.input.value;
    const key='message-action-attempt:'+url+':'+row.key;
    let attempt;try{attempt=JSON.parse(sessionStorage.getItem(key));}catch{}
    const identity={action,key:timeline.epoch+'.'+row.key,version:row.version,text};
    if(!attempt||Object.keys(identity).some(k=>attempt[k]!==identity[k]))attempt={id:this.uuid(),...identity};
    sessionStorage.setItem(key,JSON.stringify(attempt));
    this.pending=true;this.busy(true);this.error.textContent='';this.submit.textContent=actionText('正在提交…');
    this.dialog.querySelectorAll('button,textarea').forEach(n=>n.disabled=true);
    try{
      const result=await this.request(url,attempt);
      if(timeline.abort.signal.aborted)return;
      if(result.status==='unknown'){
        this.error.textContent=actionText('操作结果尚未确认，请先检查聊天记录。再次提交不会重复执行。');
        if(result.id!==result.source.id)this.error.textContent+=' '+actionText('分支已创建，可在聊天列表查看。');
        this.refresh();return;
      }
      sessionStorage.removeItem(key);sessionStorage.removeItem(this.draftKey());
      this.dialog.close();this.target=null;
      if(action!=='edit'){
        this.refresh();
        if(result.draft!==null&&result.draft!==undefined){
          // The fork exists but desktop activation failed before the edit. Keep
          // the replacement as an edit draft; never append it as a normal turn.
          const pending={sourceTurnId:result.source.turnId,text:result.draft};
          sessionStorage.setItem('fork-edit:'+result.host+'|'+result.id,JSON.stringify(pending));
          this.notify('分支已创建，修改尚未发送。请连接后编辑最后一条消息。');
        }else this.notify(action==='fork'?'已创建分支':'已在分支中重新发送');
        await this.openChat(result.id,result.host);
      }else this.notify('已重新发送');
    }catch(error){if(this.dialog.open)this.error.textContent=actionText(error.message);else this.notify(error.message);}
    finally{
      this.pending=false;this.busy(false);this.dialog.querySelectorAll('button,textarea').forEach(n=>n.disabled=false);
      this.submit.textContent=actionText(action==='fork'?'从这里分支':action==='edit'?'编辑并重新发送':'编辑并新建分支');
    }
  }
}
