'use strict';

class WatchPanel {
  constructor({root,change}){this.root=root;this.change=change;this.revision=0;this.busy=false;this.value=null;this.error='';}
  clear(){this.revision++;this.busy=false;this.value=null;this.error='';this.root.replaceChildren();}
  render(value,revision=this.revision){
    if(revision!==this.revision||this.busy)return;
    this.value=value;this.root.replaceChildren();
    const t=BridgeI18n.t,node=(tag,text,cls)=>{const el=document.createElement(tag);if(text)el.textContent=text;if(cls)el.className=cls;return el;};
    if(this.error){const error=node('p',t(this.error),'error');error.setAttribute('role','alert');this.root.append(error);}
    if(!value.watches.length){this.root.append(node('p',t('暂无关注聊天。请在手机打开聊天并开启提醒。'),'hint'));return;}
    const enabled=value.notifications.enabled||value.notifications.barkEnabled;
    this.root.append(node('p',t(!value.runtime.running?'网关未运行，启动后继续监控。':!enabled?'通知通道未开启，保存的监控暂不发送提醒。':'正在监控以下会话的待确认请求。'),'hint'));
    for(const watch of value.watches){
      const row=node('section',null,'watch-row');row.dataset.id=watch.id;row.dataset.host=watch.host;
      const info=node('div',null,'watch-info');info.append(node('strong',watch.title||t('名称暂不可用')));
      info.append(node('small',[watch.host==='local'?t('此电脑'):watch.hostLabel||watch.host,watch.cwd].filter(Boolean).join(' · ')));
      info.append(node('small',watch.id,'watch-id'));
      const actions=node('div',null,'watch-actions'),label=node('label',null,'check'),input=node('input');input.type='checkbox';input.checked=!!watch.notifyOnCompletion;
      input.onchange=event=>{event.stopPropagation();this.submit(watch,{action:'update',notifyOnCompletion:input.checked});};
      label.append(input,node('span',t('运行完成后通知')));
      const remove=node('button',t('删除监控'),'watch-remove');remove.type='button';remove.onclick=()=>this.submit(watch,{action:'remove'});
      actions.append(label,remove);row.append(info,actions);this.root.append(row);
    }
  }
  async submit(watch,action){
    if(this.busy)return;
    const revision=++this.revision;this.busy=true;this.error='';
    this.root.querySelectorAll('button,input').forEach(el=>el.disabled=true);
    try{
      const result=await this.change({id:watch.id,host:watch.host,...action});
      if(revision===this.revision)this.value={...this.value,watches:result.watches};
    }catch(error){if(revision===this.revision)this.error=error.message.replace(/^Error invoking remote method '[^']+': (?:Error: )?/,'');}
    finally{if(revision===this.revision){this.busy=false;this.revision++;this.render(this.value);}}
  }
}

if(typeof module!=='undefined')module.exports={WatchPanel};
