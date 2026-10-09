'use strict';

class AgentsPanel {
  constructor(workbench,session,tab){Object.assign(this,{workbench,session,tab});this.node=(...a)=>workbench.node(...a);this.button=(...a)=>workbench.button(...a);this.windowState={};this.load();}
  url(id=''){return '/api/sessions/'+this.session.id+'/subagents?host='+encodeURIComponent(this.session.host)+(id?'&id='+encodeURIComponent(id):'');}
  async load(){
    this.controller?.abort();const controller=this.controller=new AbortController();
    if(!this.list){
      const head=this.node('div','wb-git-head');head.append(this.node('strong','',BridgeI18n.t('子智能体')),this.button(BridgeI18n.t('刷新'),()=>this.load()));
      this.status=this.node('p','wb-status','');this.status.setAttribute('role','status');this.list=this.node('div','wb-agents-list');
      this.tab.body.append(head,this.status,this.list);
    }
    try{
      const result=await this.workbench.request(this.url(),undefined,controller.signal);if(controller.signal.aborted)return;
      this.status.textContent=BridgeI18n.t('当前聊天派生的任务 · 已保存记录')+(result.limited?BridgeI18n.t(' · 显示前 128 项'):'');
      const stamp=JSON.stringify(result);if(stamp!==this.stamp){
        this.stamp=stamp;const scroll=this.list.scrollTop;this.list.replaceChildren();
        const children=new Map();for(const agent of result.agents){if(!children.has(agent.parentId))children.set(agent.parentId,[]);children.get(agent.parentId).push(agent);}
        const draw=(parent,depth)=>{for(const agent of children.get(parent)||[]){
          const row=this.button('',()=>this.show(agent),agent.title);row.className='wb-agent-card';row.style.paddingLeft=(12+Math.min(depth,6)*16)+'px';row.dataset.agent=agent.id;
          row.append(this.workbench.raw(this.node('strong','',agent.nickname||agent.title)),this.workbench.raw(this.node('span','',agent.title)),this.node('small','',[agent.role,agent.path,agent.archived?BridgeI18n.t('已归档'):BridgeI18n.t('已保存'),agent.id.slice(0,8)].filter(Boolean).join(' · ')));
          this.list.append(row);draw(agent.id,depth+1);
        }};draw(this.session.id,0);
        if(!result.agents.length)this.list.append(this.node('p','wb-empty',BridgeI18n.t('此聊天还没有已保存的子智能体任务。')));this.list.scrollTop=scroll;
      }
      if(this.selected)await this.readDetail();
    }catch(e){if(!controller.signal.aborted)this.status.textContent=BridgeI18n.t(e.message);}
    finally{clearTimeout(this.timer);if(!this.disposed)this.timer=setTimeout(()=>{if(this.workbench.isVisible(this.session,this.tab)&&!document.hidden)this.load();else this.schedule();},10000);}
  }
  schedule(){clearTimeout(this.timer);if(!this.disposed)this.timer=setTimeout(()=>{if(this.workbench.isVisible(this.session,this.tab)&&!document.hidden)this.load();else this.schedule();},10000);}
  show(agent){
    this.window?.destroy();this.detailController?.abort();this.selected=agent;this.detailStamp=null;
    this.detail=this.node('div','wb-agent-detail');this.detail.textContent=BridgeI18n.t('正在读取任务记录…');
    this.window=new FloatingPanel({host:this.tab.body,content:this.detail,title:agent.nickname||agent.title,state:this.windowState,node:this.node,button:this.button,close:()=>{this.detailController?.abort();this.selected=null;this.window=null;}});this.readDetail();
  }
  async readDetail(){
    this.detailController?.abort();const controller=this.detailController=new AbortController(),agent=this.selected;
    try{
      const result=await this.workbench.request(this.url(agent.id),undefined,controller.signal);if(controller.signal.aborted)return;
      const stamp=JSON.stringify(result);if(stamp===this.detailStamp)return;this.detailStamp=stamp;
      const scroll=this.detail.scrollTop,follow=this.detail.scrollHeight-this.detail.clientHeight-scroll<60;this.detail.replaceChildren();
      const latest=result.turns.at(-1),labels={inProgress:BridgeI18n.t('最近记录：运行中（非实时确认）'),completed:BridgeI18n.t('最近记录：已完成'),interrupted:BridgeI18n.t('最近记录：已中断'),failed:BridgeI18n.t('最近记录：失败')};
      this.detail.append(this.node('p','wb-status',labels[latest?.status]||BridgeI18n.t('尚无任务状态记录')));
      if(!result.historyComplete)this.detail.append(this.node('p','wb-status',BridgeI18n.t('仅展示最近 10 轮记录。')));
      for(const turn of result.turns)for(const message of turn.messages){
        const body=this.node('div','wb-agent-message');
        if(message.role==='assistant'||message.role==='user'){
          body.append(this.node('small','',message.role==='user'?BridgeI18n.t('任务输入'):BridgeI18n.t('回复')));const text=this.node('div','message-body');renderMarkdown(text,message.text||'',[],()=> '');body.append(text);
        }else{
          const details=this.node('details');details.append(this.node('summary','',message.name||message.kind||BridgeI18n.t('工具记录')),this.node('pre','',message.output||message.text||''));body.append(details);
        }
        this.detail.append(body);
      }
      if(!result.turns.length)this.detail.append(this.node('p','wb-empty',BridgeI18n.t('任务尚未写入聊天记录。')));
      this.detail.scrollTop=follow?this.detail.scrollHeight:scroll;
    }catch(e){if(!controller.signal.aborted)this.detail.textContent=BridgeI18n.t(e.message);}
  }
  dispose(){this.disposed=true;clearTimeout(this.timer);this.controller?.abort();this.detailController?.abort();this.window?.destroy();}
}
