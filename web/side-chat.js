'use strict';

// Attach to an existing native ephemeral chat. Never create a saved substitute.
class SideChatPanel {
  constructor(workbench, session, tab) {
    Object.assign(this, {workbench, session, tab});
    this.connectionId=uuid();
    const n=(...args)=>workbench.node(...args),b=(...args)=>workbench.button(...args);
    const head=n('div','wb-side-head');
    this.connectButton=b('连接电脑端侧边聊天',()=>this.connect());
    this.disconnectButton=b('断开',()=>this.disconnect());this.disconnectButton.hidden=true;
    head.append(n('strong','','原生侧边聊天'),this.connectButton,this.disconnectButton);
    this.help=n('p','wb-side-help','本地实验：先在电脑的当前聊天中创建侧边聊天（Mac：⌘⌥S），再点击连接。关闭此标签只断开手机连接；结束临时聊天请在电脑端关闭。');
    this.status=n('p','wb-status');this.status.setAttribute('role','status');
    this.messages=n('div','wb-side-messages');this.messages.setAttribute('aria-label','侧边聊天消息');
    this.form=n('form','wb-side-input');this.input=n('textarea');this.input.rows=2;this.input.placeholder='问一个相关问题…';this.input.setAttribute('aria-label','侧边聊天输入');
    this.sendButton=n('button','','发送');this.sendButton.type='submit';this.sendButton.disabled=true;
    this.form.append(this.input,this.sendButton);this.form.onsubmit=e=>{e.preventDefault();this.send();};
    this.input.oninput=()=>this.controls();
    tab.body.append(head,this.help,this.status,this.messages,this.form);
  }
  url(){return '/api/sessions/'+this.session.id+'/side-chat?host='+encodeURIComponent(this.session.host)+'&connectionId='+this.connectionId;}
  request(body){return this.workbench.request(this.url(),body===undefined?undefined:{...body,connectionId:this.connectionId});}
  controls(){this.sendButton.disabled=!!this.busy||!this.state?.connected||this.state.status==='active'||!!this.state.requests?.length||!this.input.value.trim()||!!this.pending;}
  async connect(){
    if(this.busy)return;this.busy=true;this.connectButton.disabled=true;this.status.textContent='正在寻找电脑端已打开的侧边聊天…';
    try{const state=await this.request({action:'connect'});if(this.disposed){await this.request({action:'disconnect'});return;}this.apply(state);this.schedule();}
    catch(e){if(!this.disposed)this.status.textContent=e.message;}
    finally{this.busy=false;this.connectButton.disabled=false;this.controls();}
  }
  apply(state){
    if(this.disposed)return;if(this.state&&this.state.id!==state.id)this.pending=null;this.state=state;
    this.connectButton.hidden=!!state.connected;this.disconnectButton.hidden=false;
    this.status.textContent=state.error||(this.pending?'发送结果未确认，请先在电脑端查看。':state.requests?.length?'请在电脑端处理确认请求':state.status==='active'?'正在回复…':'已连接 · '+(state.model||'沿用电脑设置'));
    const stamp=JSON.stringify(state.turns);
    if(stamp!==this.stamp){
      this.stamp=stamp;const scroll=this.messages.scrollTop,follow=this.messages.scrollHeight-this.messages.clientHeight-scroll<60;this.messages.replaceChildren();
      for(const turn of state.turns||[])for(const message of turn.messages||[]){
        const card=this.workbench.node('article','wb-side-message '+message.role);
        if(message.role==='user'||message.role==='assistant'){
          card.append(this.workbench.node('small','',message.role==='user'?'你':'Codex'));
          const text=this.workbench.node('div','message-body');renderMarkdown(text,message.text||'');card.append(text);
        }else{
          const details=this.workbench.node('details');details.append(this.workbench.node('summary','',message.title||message.kind||'工具活动'),this.workbench.node('pre','',message.output||message.text||''));card.append(details);
        }
        this.messages.append(card);
      }
      if(!this.messages.children.length)this.messages.append(this.workbench.node('p','wb-empty',state.connected?'继承当前聊天的上下文，单独讨论一个问题。':'侧边聊天已断开。'));
      this.messages.scrollTop=follow?this.messages.scrollHeight:scroll;
    }
    this.controls();
  }
  schedule(){
    clearTimeout(this.timer);if(this.disposed)return;
    this.timer=setTimeout(async()=>{
      try{if(!this.busy&&this.workbench.current===this.session&&this.session.active===this.tab.id&&!document.hidden)this.apply(await this.request());}
      catch(e){if(!this.disposed){this.status.textContent=e.message;if(this.state)this.state.connected=false;this.connectButton.hidden=false;this.controls();}}
      finally{this.schedule();}
    },3000);
  }
  async send(){
    if(this.sendButton.disabled)return;
    this.busy=true;const text=this.input.value;this.pending={text,submissionId:uuid()};this.controls();this.status.textContent='正在发送…';
    try{
      const result=await this.request({action:'send',...this.pending});
      if(this.disposed)return;
      if(result.status==='accepted'){this.pending=null;if(this.input.value===text)this.input.value='';this.apply(await this.request());}
      else this.status.textContent='发送结果未确认，请先在电脑端查看，避免重复发送。';
    }catch(e){if(!this.disposed){if([400,401,403,404].includes(e.status))this.pending=null;this.status.textContent=e.message;}}
    finally{this.busy=false;this.controls();}
  }
  async disconnect(){
    if(this.busy)return;this.busy=true;this.controls();clearTimeout(this.timer);
    try{await this.request({action:'disconnect'});this.state=null;this.pending=null;this.stamp=null;this.messages.replaceChildren();this.input.value='';this.status.textContent='手机连接已断开。电脑端的临时聊天仍保持打开。';this.connectButton.hidden=false;this.disconnectButton.hidden=true;}
    catch(e){this.status.textContent=e.message;}
    finally{this.busy=false;this.controls();}
  }
  dispose(){this.disposed=true;clearTimeout(this.timer);this.input.value='';this.messages.replaceChildren();this.request({action:'disconnect'}).catch(()=>{});}
}
