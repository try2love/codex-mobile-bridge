'use strict';

// A shared, gateway-owned ephemeral fork. Disposing a view never ends the chat.
class SideChatPanel {
  constructor(workbench, session, tab) {
    Object.assign(this, {workbench, session, tab});this.creationId=uuid();
    const n=(...args)=>workbench.node(...args),b=(...args)=>workbench.button(...args);
    const head=n('div','wb-side-head');
    this.createButton=b('新建侧边聊天',()=>this.create());
    this.stopButton=b('停止回复',()=>this.action('stop'));this.stopButton.hidden=true;
    this.endButton=b('结束聊天',async()=>{if(await this.end())this.load();});this.endButton.hidden=true;
    head.append(n('strong','','临时侧边聊天'),this.createButton,this.stopButton,this.endButton);
    this.help=n('p','wb-side-help','继承创建时的主聊天上下文。在手机、网页或电脑 Bridge 工作台继续；离开页面会保留，关闭聊天或重启网关后不再保留。');
    this.status=n('p','wb-status');this.status.setAttribute('role','status');
    this.messages=n('div','wb-side-messages');this.messages.setAttribute('aria-label','侧边聊天消息');
    this.approvals=n('div','wb-side-approvals');
    this.form=n('form','wb-side-input');this.input=n('textarea');this.input.rows=2;this.input.placeholder='问一个相关问题…';this.input.setAttribute('aria-label','侧边聊天输入');
    this.sendButton=n('button','','发送');this.sendButton.type='submit';this.sendButton.disabled=true;
    this.form.append(this.input,this.sendButton);this.form.onsubmit=e=>{e.preventDefault();this.send();};
    this.input.oninput=()=>this.controls();
    tab.body.append(head,this.help,this.status,this.messages,this.approvals,this.form);this.load();this.schedule();
  }
  url(){return '/api/sessions/'+this.session.id+'/side-chat?host='+encodeURIComponent(this.session.host);}
  request(body){return this.workbench.request(this.url(),body);}
  controls(){
    this.sendButton.disabled=!!this.busy||!this.state?.connected||this.state.status==='active'||!!this.state.requests?.length||!this.input.value.trim()||!!this.pending;
    this.createButton.hidden=!!this.state?.id;this.createButton.disabled=!!this.busy;
    this.stopButton.hidden=!this.state?.id||this.state.status!=='active';this.stopButton.disabled=!!this.busy;
    this.endButton.hidden=!this.state?.id;this.endButton.disabled=!!this.busy;
  }
  async load(){try{this.apply(await this.request());}catch(e){if(!this.disposed){this.status.textContent=e.message;if(this.state)this.state.connected=false;this.controls();}}}
  async create(){
    if(this.busy)return;this.busy=true;this.controls();this.status.textContent='正在创建临时侧边聊天…';
    try{this.apply(await this.request({action:'create',creationId:this.creationId}));}
    catch(e){if(!this.disposed)this.status.textContent=e.message;}
    finally{this.busy=false;this.controls();}
  }
  apply(state){
    if(this.disposed)return;
    if(this.state?.id&&this.state.id!==state.id){this.pending=null;this.creationId=uuid();this.input.value='';}
    this.state=state;
    if(this.pending&&state.submissions?.some(s=>s.id===this.pending.submissionId&&s.status==='accepted')){
      if(this.input.value===this.pending.text)this.input.value='';this.pending=null;
    }
    this.status.textContent=state.error||(!state.id?'尚未创建侧边聊天':this.pending?'发送结果未确认，请核对消息，避免重复发送。':state.requests?.length?'有请求需要处理':state.status==='active'?'正在回复…':'已连接 · '+(state.model||'沿用主聊天设置'));
    const stamp=JSON.stringify([state.id,state.connected,state.turns]);
    if(stamp!==this.stamp){
      this.stamp=stamp;const scroll=this.messages.scrollTop,follow=this.messages.scrollHeight-this.messages.clientHeight-scroll<60;this.messages.replaceChildren();
      for(const turn of state.turns||[]){for(const message of turn.messages||[]){
        const card=this.workbench.node('article','wb-side-message '+message.role);
        if(message.role==='user'||message.role==='assistant'){
          card.append(this.workbench.node('small','',message.role==='user'?'你':'Codex'));
          const text=this.workbench.node('div','message-body');renderMarkdown(text,message.text||'');card.append(text);
        }else{
          const details=this.workbench.node('details');details.append(this.workbench.node('summary','',message.title||message.kind||'工具活动'),this.workbench.node('pre','',message.output||message.text||''));card.append(details);
        }
        this.messages.append(card);
      }if(turn.error)this.messages.append(this.workbench.node('p','error','本次回复未完成，请检查模型接入。'));}
      if(!this.messages.children.length)this.messages.append(this.workbench.node('p','wb-empty',state.connected?'上下文已准备好，开始单独讨论。':'新建后即可在这里提问。'));
      this.messages.scrollTop=follow?this.messages.scrollHeight:scroll;
    }
    this.renderApprovals(state.requests||[]);this.controls();
  }
  renderApprovals(requests){
    const stamp=JSON.stringify(requests);if(stamp===this.approvalStamp)return;this.approvalStamp=stamp;this.approvals.replaceChildren();
    const n=(...args)=>this.workbench.node(...args);
    for(const request of requests){
      const card=n('form','wb-side-approval'),p=request.params||{},method=request.method,controls={};
      card.append(n('strong','','需要你的确认'));
      if(!request.supported){card.append(n('p','',p.message));this.approvals.append(card);continue;}
      for(const key of ['reason','command','cwd','message'])if(p[key])card.append(n(key==='command'?'pre':'p','',p[key]));
      if(p.changes)card.append(n('pre','',p.changes.map(c=>c.path+'\n'+(c.diff||'')).join('\n')));
      if(p.permissions)card.append(n('pre','',JSON.stringify(p.permissions,null,2)));
      const isInput=method.includes('requestUserInput'),isMcp=method==='mcpServer/elicitation/request';
      if(isInput)for(const q of p.questions||[]){
        const label=n('label','',q.question||q.header||q.id),input=n('textarea');input.rows=2;input.required=true;
        if(q.options?.length){const select=n('select');select.append(new Option('选择答案或自行填写',''));for(const option of q.options)select.append(new Option(option.label,option.label));select.onchange=()=>{input.value=select.value;};label.append(select);}
        label.append(input);card.append(label);controls[q.id]=()=>input.value;
      }
      if(isMcp)for(const [key,field] of Object.entries(p.requestedSchema?.properties||{})){
        const label=n('label','',field.title||key);let input;
        if(field.enum){input=n('select');input.append(new Option('请选择…',''));for(const value of field.enum)input.append(new Option(String(value),String(value)));}
        else{input=n('input');input.type=field.type==='boolean'?'checkbox':['number','integer'].includes(field.type)?'number':'text';if(input.type==='number')input.step=field.type==='integer'?'1':'any';}
        input.required=(p.requestedSchema.required||[]).includes(key)&&input.type!=='checkbox';label.append(input);card.append(label);
        controls[key]=()=>input.type==='checkbox'?input.checked:input.value===''?undefined:['number','integer'].includes(field.type)?Number(input.value):input.value;
      }
      const actions=n('div','wb-side-head'),error=n('p','error');
      const respond=async response=>{
        card.querySelectorAll('button').forEach(b=>b.disabled=true);
        try{this.apply(await this.request({action:'respond',id:this.state.id,requestId:request.id,response}));}
        catch(e){error.textContent=e.message;card.querySelectorAll('button').forEach(b=>b.disabled=false);}
      };
      const available=p.availableDecisions||['accept','decline'];
      const yes=n('button','',''+(isInput?'提交回复':'本次允许'));yes.type='submit';if(isInput||isMcp||available.includes('accept'))actions.append(yes);
      if(!isInput&&available.includes('decline'))actions.append(this.workbench.button('拒绝',()=>respond(isMcp?{action:'decline'}:{decision:'decline'})));
      card.onsubmit=e=>{e.preventDefault();if(isInput){const answers={};for(const [key,get] of Object.entries(controls)){const value=get();if(!value.trim()){error.textContent='请完整回答问题';return;}answers[key]=[value];}respond({answers});}
        else if(isMcp){const content={};for(const [key,get] of Object.entries(controls)){const value=get();if(value!==undefined)content[key]=value;}respond({action:'accept',content});}
        else respond({decision:'accept'});};
      card.append(actions,error);this.approvals.append(card);
    }
  }
  schedule(){
    clearTimeout(this.timer);if(this.disposed)return;
    this.timer=setTimeout(async()=>{try{if(!this.busy&&this.workbench.current===this.session&&this.session.active===this.tab.id&&!document.hidden)await this.load();}finally{this.schedule();}},1200);
  }
  async send(){
    if(this.sendButton.disabled)return;
    this.busy=true;const text=this.input.value;this.pending={text,submissionId:uuid()};this.controls();this.status.textContent='正在发送…';
    try{const result=await this.request({action:'send',id:this.state.id,...this.pending});if(this.disposed)return;
      if(result.status==='accepted'){this.pending=null;if(this.input.value===text)this.input.value='';await this.load();}
      else this.status.textContent='发送结果未确认，请核对消息，避免重复发送。';
    }catch(e){if(!this.disposed){if([400,401,403,404].includes(e.status))this.pending=null;this.status.textContent=e.message;}}
    finally{this.busy=false;this.controls();}
  }
  async action(action){if(this.busy)return;this.busy=true;this.controls();try{this.apply(await this.request({action,id:this.state.id}));}catch(e){this.status.textContent=e.message;}finally{this.busy=false;this.controls();}}
  async end(){
    if(this.busy)return false;if(!this.state?.id)return true;
    if(!confirm('结束此侧边聊天？正在运行的回复会停止，临时内容将清除，所有设备都无法继续此聊天。'))return false;
    this.busy=true;this.controls();
    try{await this.request({action:'close',id:this.state.id});this.apply({id:null,connected:false,turns:[],requests:[]});return true;}
    catch(e){this.status.textContent=e.message;return false;}
    finally{this.busy=false;this.controls();}
  }
  dispose(){this.disposed=true;clearTimeout(this.timer);this.input.value='';this.messages.replaceChildren();}
}
