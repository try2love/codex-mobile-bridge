'use strict';

// A shared picker for existing UI actions. Never turn a local command into a model prompt.
class ComposerCommands {
  static serial=0;
  static known=new Set(['model','reasoning','skills','permissions','context','compact','default','plan','goal','status','rename','archive','delete','fork','new','side']);
  static controls({model,skills,permissions,context,workMode}){
    const rows=[];
    for(const [id,label,button] of [['model','模型',model],['reasoning','思考强度',model],['skills','Skill',skills],['permissions','权限',permissions]]){
      if(button&&!button.hidden)rows.push({id,label,enabled:!button.disabled,run:()=>button.click()});
    }
    if(context?.state){
      rows.push({id:'context',label:'上下文使用量',enabled:true,run:()=>context.open()});
      if(context.state.capabilities?.compact===true)rows.push({id:'compact',label:'压缩上下文',enabled:!!context.state.connected&&!context.state.busy,run:()=>context.open()});
    }
    for(const [id,label] of [['default','普通模式'],['plan','计划模式'],['goal','目标模式']]){
      const option=workMode?.querySelector('[value='+id+']');
      if(option&&!workMode.hidden)rows.push({id,label,enabled:!workMode.disabled&&!option.disabled,args:true,
        run:()=>{workMode.value=id;workMode.dispatchEvent(new Event('change',{bubbles:true}));}});
    }
    return rows;
  }
  static session(context,open){
    if(!context)return [];
    const rows=[{id:'status',label:'会话详情',enabled:true,run:()=>open('details')}];
    for(const [id,label,capability,view] of [['rename','修改聊天名称','rename','rename'],['archive','归档','archive',context.archived?'main':'archive'],['delete','永久删除聊天','deletion','delete'],['fork','创建聊天分支','fork','main']]){
      if(context[capability])rows.push({id,label,enabled:true,run:()=>open(view)});
    }
    return rows;
  }
  constructor({input,scope,actions,onError}){
    Object.assign(this,{input,scope,actions,onError});this.index=0;this.rows=[];
    this.menu=document.createElement('div');this.menu.className='composer-commands';this.menu.id='composer-commands-'+(++ComposerCommands.serial);this.menu.hidden=true;
    this.menu.setAttribute('role','listbox');this.menu.setAttribute('aria-label',BridgeI18n.t('聊天指令'));input.before(this.menu);
    input.setAttribute('aria-controls',this.menu.id);input.setAttribute('aria-autocomplete','list');input.setAttribute('aria-expanded','false');
    input.addEventListener('input',()=>{this.dismissed=null;this.index=0;this.refresh();});
    input.addEventListener('focus',()=>this.refresh());
    input.addEventListener('blur',()=>this.close());
    input.addEventListener('keydown',event=>this.keydown(event),true);
    input.form?.addEventListener('submit',event=>{
      if(this.consume()){event.preventDefault();event.stopImmediatePropagation();}
    },true);
  }
  query(){return /^\/([^\s/]*)$/.exec(this.input.value)?.[1]??null;}
  close(){if(this.menu.hidden)return;this.menu.hidden=true;this.rows=[];this.input.setAttribute('aria-expanded','false');this.input.removeAttribute('aria-activedescendant');}
  refresh(){
    const query=this.query();
    if(query===null||this.input.disabled||document.activeElement!==this.input||this.dismissed===this.input.value){this.close();return;}
    const scope=this.scope();if(!scope){this.close();return;}
    this.openScope=scope;
    const lower=query.toLocaleLowerCase();
    this.rows=this.actions().filter(row=>[row.id,BridgeI18n.t(row.label),row.description||''].some(value=>String(value).toLocaleLowerCase().includes(lower)));
    if(!this.rows.length){this.close();return;}
    this.index=Math.min(this.index,this.rows.length-1);this.menu.replaceChildren();
    this.rows.forEach((row,index)=>{
      const button=document.createElement('button');button.type='button';button.id=this.menu.id+'-'+index;button.setAttribute('role','option');button.setAttribute('aria-disabled',String(!row.enabled));button.tabIndex=-1;
      const name=document.createElement('span'),label=document.createElement('span');name.textContent='/'+row.id;
      const prefix=row.native&&row.category==='skill'?BridgeI18n.t('Skill')+' · ':row.native&&row.category==='workflow'?BridgeI18n.t('工作流')+' · ':'';
      label.textContent=prefix+BridgeI18n.t(row.label);button.append(name,label);
      button.onpointerdown=event=>event.preventDefault();button.onclick=()=>this.choose(row.id,scope);this.menu.append(button);
    });
    this.menu.hidden=false;this.input.setAttribute('aria-expanded','true');this.highlight();
  }
  highlight(){
    [...this.menu.children].forEach((node,index)=>node.setAttribute('aria-selected',String(index===this.index)));
    const selected=this.menu.children[this.index];if(selected)this.input.setAttribute('aria-activedescendant',selected.id);
  }
  keydown(event){
    if(event.isComposing||this.menu.hidden)return;
    if(event.key==='Escape'){event.preventDefault();event.stopImmediatePropagation();this.dismissed=this.input.value;this.close();return;}
    if(['ArrowUp','ArrowDown'].includes(event.key)){
      event.preventDefault();event.stopImmediatePropagation();this.index=(this.index+(event.key==='ArrowDown'?1:-1)+this.rows.length)%this.rows.length;this.highlight();this.menu.children[this.index]?.scrollIntoView({block:'nearest'});return;
    }
    if((event.key==='Enter'&&!event.shiftKey)||event.key==='Tab'){
      event.preventDefault();event.stopImmediatePropagation();this.choose(this.rows[this.index]?.id,this.openScope);
    }
  }
  choose(id,scope,remainder=''){
    if(!scope||scope!==this.scope()){this.close();return false;}
    const action=this.actions().find(row=>row.id===id);
    if(!action?.enabled)return false;
    this.close();
    if(action.native){
      const next='/'+action.id+' '+remainder;
      this.input.value=next;
      this.input.dispatchEvent(new Event('input',{bubbles:true}));
      if(typeof this.input.setSelectionRange==='function')this.input.setSelectionRange(next.length,next.length);
      this.input.focus?.();
      return true;
    }
    this.input.value=remainder;this.input.dispatchEvent(new Event('input',{bubbles:true}));
    try{Promise.resolve(action.run()).catch(error=>this.onError?.(error.message));}catch(error){this.onError?.(error.message);}
    return true;
  }
  consume(){
    const match=/^\/([A-Za-z][A-Za-z0-9_-]*)(?:[ \t\r\n]+([\s\S]*))?$/.exec(this.input.value);
    if(!match)return false;
    const action=this.actions().find(row=>row.id===match[1]);
    if(!action&&!ComposerCommands.known.has(match[1]))return false;
    if(!action?.enabled||(match[2]&&!action.args&&!action.native)){this.onError?.(BridgeI18n.t(action?.disabledReason||'此指令当前不可用，请使用菜单中的可用选项。'));return true;}
    if(action.native)return false;
    // An unavailable known command must remain a draft, never fall through to send.
    this.choose(action.id,this.scope(),match[2]||'');return true;
  }
}

if(typeof module!=='undefined')module.exports={ComposerCommands};
