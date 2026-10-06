'use strict';

class SideChatPanel {
  static async pick(workbench, session) {
    const n=(...a)=>workbench.node(...a),b=(...a)=>workbench.button(...a);
    const dialog=n('dialog','picker wb-side-picker'),head=n('div','picker-head');
    head.append(n('h2','','选择侧边聊天'),b('×',()=>dialog.close(),'关闭'));
    const input=n('input');input.placeholder='搜索聊天或项目';input.setAttribute('aria-label','搜索侧边聊天');
    const list=n('div','wb-side-choices');let timer,controller;
    const search=async()=>{
      controller?.abort();const active=controller=new AbortController();list.textContent='正在读取聊天…';
      try {
        const result=await workbench.request('/api/sessions?q='+encodeURIComponent(input.value),undefined,active.signal);if(active.signal.aborted)return;
        list.replaceChildren();
        for(const row of result.sessions){
          if(row.id===session.id&&row.host===session.host)continue;
          const button=b('',()=>{dialog.close();session.sideChat?.dispose();session.sideChat=new SideChatPanel(workbench,session,row);},row.title);
          button.append(n('strong','',row.title),n('small','',row.projectName+' · '+(row.host==='local'?'此电脑':row.hostLabel||row.host)));list.append(button);
        }
        if(!list.children.length)list.textContent='没有其他聊天。可先从聊天列表新建，再在这里选择。';
      }catch(e){if(!active.signal.aborted)list.textContent=e.message;}
    };
    input.oninput=()=>{clearTimeout(timer);timer=setTimeout(search,250);};
    dialog.append(head,input,list);dialog.addEventListener('close',()=>{clearTimeout(timer);controller?.abort();dialog.remove();});document.body.append(dialog);dialog.showModal();search();
  }
  constructor(workbench,session,target) {
    Object.assign(this,{workbench,session,target});this.key='side-draft:'+target.host+'|'+target.id;this.pendingKey='side-pending:'+target.host+'|'+target.id;
    const n=(...a)=>workbench.node(...a),b=(...a)=>workbench.button(...a);
    this.content=n('div','wb-side-content');
    this.status=n('p','wb-side-status','正在读取聊天…');this.status.setAttribute('role','status');
    const actions=n('div','wb-side-actions'),main=b('在主区域打开',()=>{this.dispose();session.sideChat=null;workbench.openChat(target.id,target.host);});
    this.stop=b('停止任务',async()=>{try{await workbench.request(this.url('stop'),{});}catch(e){this.error.textContent=e.message;}});this.stop.hidden=true;
    actions.append(main,this.stop);
    const viewport=n('div','timeline wb-side-timeline'),container=n('div','wb-side-messages'),older=b('查看更早内容',()=>{}),newer=b('回到最新',()=>{});newer.hidden=true;
    viewport.append(older,container,newer);
    this.error=n('p','wb-side-error');this.error.setAttribute('role','alert');
    this.input=n('textarea');this.input.rows=2;this.input.placeholder='继续侧边聊天…';this.input.setAttribute('aria-label','侧边聊天消息');
    try{this.input.value=sessionStorage.getItem(this.key)||'';}catch{}
    this.input.oninput=()=>{try{sessionStorage.setItem(this.key,this.input.value);}catch{}};
    this.send=n('button','primary','发送');this.send.type='submit';this.send.disabled=true;
    const form=n('form','wb-side-form');form.append(this.input,this.send);form.onsubmit=e=>{e.preventDefault();this.submit();};
    this.input.onkeydown=e=>{if(e.key==='Enter'&&(e.metaKey||e.ctrlKey)){e.preventDefault();form.requestSubmit();}};
    this.content.append(actions,this.status,viewport,this.error,form);
    this.window=new FloatingPanel({host:workbench.sideHost,content:this.content,title:target.title,state:session.sideWindow ||= {},node:n,button:b,close:()=>{this.dispose();session.sideChat=null;}});
    this.window.element.classList.add('wb-side-chat');
    this.timeline=new ChatTimeline({url:action=>this.url(action),request:workbench.request,active:()=>workbench.current===session&&!document.hidden&&this.window.element.getClientRects().length>0,elements:{viewport,container,older,newer},
      renderText:(node,text,files,fullText)=>renderMarkdown(node,text,files,file=>this.url('files/'+file.id),{fullText}),
      renderMeta:view=>{
        this.view=view;this.status.textContent=[view.model,view.provider,view.requests?.length?'等待回应，请在主区域处理':view.status==='active'?'运行中':view.connected?'已连接':'历史记录'].filter(Boolean).join(' · ');
        this.stop.hidden=view.status!=='active'||!view.connected;
        this.send.disabled=this.sending||view.loadingHistory||view.activating||!!view.requests?.length;
      },status:text=>{this.status.textContent=text;}});
    this.timeline.start();
  }
  url(action){return '/api/sessions/'+this.target.id+'/'+action+'?host='+encodeURIComponent(this.target.host);}
  async submit(){
    const text=this.input.value;if(!text.trim()||this.sending||this.send.disabled)return;
    let pending;try{pending=JSON.parse(sessionStorage.getItem(this.pendingKey));}catch{}
    if(!pending||pending.text!==text)pending={id:uuid(),text,mode:this.view?.status==='active'?'queue':'send'};
    try{sessionStorage.setItem(this.pendingKey,JSON.stringify(pending));}catch{}
    this.sending=true;this.send.disabled=true;this.error.textContent='';
    try{
      const result=await this.workbench.request(this.url('send'),pending);
      if(['unknown','ignored','unconfirmed'].includes(result.status))throw Error('发送结果未确认，请在主区域检查。不会自动重发。');
      try{sessionStorage.removeItem(this.pendingKey);if(sessionStorage.getItem(this.key)===text)sessionStorage.removeItem(this.key);}catch{}
      if(this.input.value===text)this.input.value='';this.workbench.notify(result.status==='queued'?'已加入侧边聊天队列':'已发送到侧边聊天');
    }catch(e){this.error.textContent=e.message;}
    finally{this.sending=false;this.send.disabled=!!this.view?.requests?.length;}
  }
  dispose(){this.timeline?.dispose();this.window?.destroy();}
}
