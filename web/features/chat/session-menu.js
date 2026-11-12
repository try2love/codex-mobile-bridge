'use strict';

class CliConnectionsPanel {
  constructor({request,notify,translate=text=>text,identity=()=>'',currentClient=()=> 'codex',newId=()=>crypto.randomUUID()}){
    Object.assign(this,{request,notify,t:translate,identity,currentClient,newId});this.timer=null;
    document.addEventListener('bridge-logged-out',()=>this.close());
  }
  close(){clearTimeout(this.timer);this.dialog?.close();this.dialog?.remove();this.dialog=null;this.pending=null;this.readVersion=(this.readVersion||0)+1;}
  node(tag,text,cls){const node=document.createElement(tag);node.textContent=text===undefined?'':this.t(text);if(cls)node.className=cls;return node;}
  open(target=null){
    this.close();this.target=target;this.provider=target?.provider||this.currentClient();this.clientContext=this.currentClient();this.scope=this.identity();this.selected=new Set();this.error='';this.confirmation=null;this.revision=0;this.historyOpen=false;this.selectTarget=!!target?.sessionId;this.data={connections:[],operations:[]};this.rows=[];this.groups=[];
    const dialog=this.dialog=this.node('dialog',undefined,'picker cli-connections-panel');dialog.setAttribute('aria-label',this.t('CLI 连接管理'));
    dialog.addEventListener('close',()=>{clearTimeout(this.timer);dialog.remove();if(this.dialog===dialog)this.dialog=null;});
    this.header(dialog);dialog.append(this.node('p','正在读取 CLI 连接…','appearance-note'));document.body.append(dialog);dialog.showModal();this.refresh();
  }
  valid(dialog){return dialog===this.dialog&&dialog.open&&this.scope===this.identity()&&this.clientContext===this.currentClient();}
  matches(row){
    const family=ClientIdentity.family(this.provider);if(row.provider!==family)return false;
    if(this.provider!==family){if(ClientIdentity.entry(this.provider)?row.entry!==this.provider:row.host!==this.provider)return false;}
    else if(row.entry&&row.entry!==this.provider)return false;
    return !this.target?.sessionId||row.sessionId===this.target.sessionId&&(!this.target.host||row.host===this.target.host);
  }
  async refresh(){
    const dialog=this.dialog;if(!dialog||this.pending)return;clearTimeout(this.timer);const version=++this.readVersion;
    try{
      const data=await this.request('/api/cli-connections',undefined,undefined,()=>this.valid(dialog));if(!this.valid(dialog)||version!==this.readVersion)return;
      this.data=data;this.render();
      if(this.rows.some(row=>row.checking||['waiting','releasing'].includes(row.status)))this.timer=setTimeout(()=>{if(this.valid(dialog)&&!document.hidden)this.refresh();},5000);
    }catch(error){if(this.valid(dialog)&&version===this.readVersion){this.error=error.message;this.render();}}
  }
  button(parent,label,run,{disabled=false}={}){const button=this.node('button',label,'plain');button.type='button';button.disabled=disabled;button.onclick=run;parent.append(button);return button;}
  closeButton(parent){const button=this.button(parent,'关闭',()=>this.close());button.dataset.cliClose='true';return button;}
  header(dialog){const head=this.node('div',undefined,'picker-head');head.append(this.node('h2',this.target?.sessionId?'释放 CLI 连接':'CLI 连接管理'));this.closeButton(head);dialog.append(head);if(this.confirmation)this.button(dialog,'返回连接列表',()=>this.back());}
  back(){this.confirmation=null;this.revision++;this.render();}
  snapshot(){
    const latest=new Map(),checks=new Map(),history=[];
    for(const job of this.data.operations||[])for(const row of job.results||[])if(this.matches(row)){
      const value={...row,job};history.push(value);
      if(job.mode==='check')checks.set(row.key,{...value,observes:latest.get(row.key)?.job.id});else latest.set(row.key,value);
    }
    for(const [key,check] of checks){
      const control=latest.get(key);
      if(control&&(check.observes!==control.job.id||check.operationId&&check.operationId!==control.job.id||control.status==='released'))continue;
      const checking=['waiting','releasing'].includes(check.status);
      if(!control)latest.set(key,{...check,status:['released','detached'].includes(check.status)?check.status:'unknown',checking});
      else if(check.status==='released'||check.status==='detached'&&control.status==='unknown')latest.set(key,check);
      else latest.set(key,{...control,checking});
    }
    const rows=new Map();
    for(const row of this.data.connections||[])if(this.matches(row)){
      const last=latest.get(row.key);rows.set(row.key,{...row,status:last?.status||'connected',error:last?.error,notice:last?.notice,canCancel:last?.canCancel,checking:last?.checking,last,current:true});
    }
    for(const [key,row] of latest)if(!rows.has(key)&&['waiting','releasing','unknown','detached'].includes(row.status))rows.set(key,{...row,last:row,current:false});
    const groups=new Map();
    for(const row of rows.values()){
      const runtime=row.host==='local'||row.host?.startsWith('codex:')?'local':row.host?.startsWith('wsl:')||row.host?.startsWith('wsl-claude:')?'wsl':row.host?'ssh':'unknown';
      const kind=row.scopeKey&&row.scopeLabel&&row.sharedHome===true?'shared':row.configurationKey&&row.configurationLabel?'configuration':'connection';
      const key=JSON.stringify([runtime,row.host,kind,kind==='shared'?row.scopeKey:kind==='configuration'?row.configurationKey:row.key]);
      if(!groups.has(key))groups.set(key,{key,runtime,kind,scopeKey:kind==='shared'?row.scopeKey:null,label:kind==='shared'?row.scopeLabel:kind==='configuration'?row.configurationLabel:'配置范围待核验',rows:[]});groups.get(key).rows.push(row);
    }
    return {rows:[...rows.values()],groups:[...groups.values()].sort((a,b)=>['local','ssh','wsl','unknown'].indexOf(a.runtime)-['local','ssh','wsl','unknown'].indexOf(b.runtime)),history};
  }
  selectable(row,group){return row.current&&!['unknown','releasing','released'].includes(row.status)&&(row.status!=='waiting'||group.kind!=='shared');}
  status(row){
    if(row.checking||(row.last?.job||row.job)?.mode==='check'&&['waiting','releasing'].includes(row.status))return '正在核对结果';
    if((row.last?.job||row.job)?.mode==='check'&&row.status==='failed')return '核验未完成';
    return ({connected:'网关持有连接',waiting:'等待任务完成',releasing:'正在释放',released:'连接已释放',detached:'网关连接已释放，原生占用待核对',unknown:'结果待核对',failed:'尚未释放，可重试',superseded:'已由新的明确操作替代',cancelled:'已取消等待'})[row.status]||row.status;
  }
  render(){
    const dialog=this.dialog;if(!dialog)return;this.historyOpen=dialog.querySelector('details.cli-release-history')?.open??this.historyOpen;const top=dialog.scrollTop;
    const {rows,groups,history}=this.snapshot();this.rows=rows;this.groups=groups;
    const eligible=new Set(groups.flatMap(group=>group.rows.filter(row=>this.selectable(row,group)).map(row=>row.key)));this.selected=new Set([...this.selected].filter(key=>eligible.has(key)));
    if(this.selectTarget){for(const row of rows)if(eligible.has(row.key))this.selected.add(row.key);this.selectTarget=false;}
    dialog.replaceChildren();this.header(dialog);
    dialog.append(this.node('p','仅管理当前 Agent 的 CLI 连接。每个主机与配置独立释放，聊天历史保留。','appearance-note'));
    if(this.error){const error=this.node('p',this.error,'error');error.setAttribute('role','alert');dialog.append(error);}
    if(this.pending){const status=this.node('p',this.pending.label,'appearance-note');status.setAttribute('role','status');dialog.append(status);}
    if(this.confirmation){this.renderConfirmation();dialog.scrollTop=top;return;}
    if(!rows.length)dialog.append(this.node('p','当前 Agent 没有由网关持有或待核对的 CLI 连接'));
    let runtime;
    for(const group of groups){
      if(runtime!==group.runtime){runtime=group.runtime;dialog.append(this.node('h3',({local:'本机',ssh:'SSH',wsl:'WSL',unknown:'运行位置待核验'})[runtime]));}
      const section=this.node('section',undefined,'cli-release-group');section.dataset.cliGroup=group.key;section.append(this.node('h4',group.label));
      if(group.kind==='connection')section.append(this.node('p','配置范围尚未核验，仅支持逐连接操作。','appearance-note'));
      for(const row of group.rows){
        const article=this.node('div',undefined,'cli-release-row');article.dataset.cliConnection=row.key;
        const label=this.node('label',undefined,'display-option'),text=this.node('span');text.append(this.node('strong',row.title||row.sessionId),this.node('small',[!row.current?this.t('旧连接待核对'):'',row.location,row.sessionId].filter(Boolean).join(' · ')));
        const check=document.createElement('input');check.type='checkbox';check.disabled=!!this.pending||!this.selectable(row,group);check.checked=this.selected.has(row.key);check.onchange=()=>{if(check.checked)this.selected.add(row.key);else this.selected.delete(row.key);this.render();};label.append(text,check);article.append(label);
        const status=this.node('p',this.status(row),'appearance-note');status.setAttribute('role','status');if(row.error||row.notice)status.append(this.node('span',' · '+this.t(row.error||row.notice)));article.append(status);
        if(['unknown','detached'].includes(row.status))this.button(article,'核对结果',()=>this.submitLegacy('check',[row.key]),{disabled:!!this.pending||!!row.checking});
        else if(row.current&&row.canForce===true&&group.kind!=='shared'&&!['releasing','released'].includes(row.status))this.button(article,'强制释放此连接…',()=>this.previewForce(row),{disabled:!!this.pending});
        if(row.status==='detached'&&row.canUnload===true)this.button(article,'释放原生占用…',()=>this.confirmLegacy('unload',[row.key],group),{disabled:!!this.pending});
        section.append(article);
      }
      const current=group.rows.filter(row=>row.current),selected=current.filter(row=>this.selected.has(row.key)),waiting=group.rows.filter(row=>['waiting','releasing'].includes(row.status)&&row.canCancel===true&&row.last?.job.mode==='scopeWait');
      const cancels=new Map(waiting.map(row=>[row.last.job.id,row.last.job]));for(const [id] of cancels)this.button(section,'取消此范围等待',()=>this.perform({mode:'scopeCancel',id},'正在取消等待…'),{disabled:!!this.pending});
      const blocked=group.rows.some(row=>['waiting','unknown','releasing'].includes(row.status)),normal=selected.length&&!selected.some(row=>['waiting','unknown','releasing'].includes(row.status))&&!(group.kind==='shared'&&blocked);
      this.button(section,this.target?.sessionId?'释放连接…':'释放所选连接…',()=>this.previewRelease(group,selected.map(row=>row.key)),{disabled:!!this.pending||!normal});
      if(group.kind!=='connection'&&current.length){
        this.button(section,'释放此配置全部连接…',()=>this.previewRelease(group,current.map(row=>row.key),true),{disabled:!!this.pending||blocked||current.some(row=>!this.selectable(row,group)||row.status==='waiting')});
        if(group.kind==='shared'&&current.some(row=>row.canForce===true&&row.status!=='released'))this.button(section,'强制释放此范围…',()=>this.previewRelease(group,current.map(row=>row.key),true,true),{disabled:!!this.pending});
      }
      if(group.kind!=='shared'&&selected.length)this.button(section,'停止任务并释放所选…',()=>this.confirmLegacy('stop',selected.map(row=>row.key),group),{disabled:!!this.pending});
      dialog.append(section);
    }
    if(history.length){const details=this.node('details',undefined,'cli-release-history');details.open=this.historyOpen;details.append(this.node('summary',this.t('历史操作回执')+' · '+history.length));
      const load=()=>{if(!details.open||details.dataset.loaded)return;details.dataset.loaded='true';for(const row of [...history].reverse()){const line=this.node('p',undefined,'appearance-note');line.textContent=[row.title||row.sessionId,this.t(this.status(row)),row.job.id,row.error||row.notice?this.t(row.error||row.notice):''].filter(Boolean).join(' · ');details.append(line);}};details.addEventListener('toggle',load);load();dialog.append(details);}
    this.button(dialog,'刷新',()=>this.refresh(),{disabled:!!this.pending});dialog.scrollTop=top;
  }
  async perform(body,label,preview=null){
    const dialog=this.dialog;if(!this.valid(dialog)||this.pending)return;clearTimeout(this.timer);this.readVersion++;const revision=this.revision,token={label};this.pending=token;this.error='';this.render();
    try{
      const result=await this.request('/api/cli-connections',body,undefined,()=>this.valid(dialog));if(!this.valid(dialog))return;
      if(preview){if(revision===this.revision)this.confirmation=preview(result);}
      else{this.confirmation=null;this.revision++;this.selected.clear();if(result.results){const jobs=this.data.operations||[];this.data.operations=jobs.some(job=>job.id===result.id)?jobs.map(job=>job.id===result.id?result:job):[...jobs,result];}}
    }catch(error){if(this.valid(dialog)){this.error=error.message;this.confirmation=null;}}
    finally{if(this.pending===token)this.pending=null;if(this.valid(dialog)){this.render();if(!preview)await this.refresh();}}
  }
  previewRelease(group,keys,all=false,force=false){
    if(!keys.length)return;
    if(group.kind!=='shared')return this.confirmLegacy('wait',keys,group);
    return this.perform({mode:'scopePreview',scopeKey:group.scopeKey,...(all?{all:true}:{connections:keys})},'正在核对释放范围…',value=>{
      if(value.scopeKey!==group.scopeKey||typeof value.generation!=='string'||!value.generation||!Array.isArray(value.connections)||!value.scopeLabel||!Array.isArray(value.impact))throw Error(value.message||'CLI 范围尚未核验，请重新检查');
      return {mode:force?'scopeForce':'scopeWait',preview:value,group};
    });
  }
  previewForce(row){return this.perform({mode:'forcePreview',connections:[row.key]},'正在核对释放范围…',value=>{
    if(value.canForce!==true||typeof value.generation!=='string'||!value.generation||!value.scopeLabel||!Array.isArray(value.impact))throw Error(value.message||'当前无法核验此 CLI 连接及原生进程。');return {mode:'force',preview:value,keys:[row.key]};
  });}
  confirmLegacy(mode,keys,group){if(!this.valid(this.dialog)||this.pending)return;clearTimeout(this.timer);this.confirmation={mode,keys,group};this.revision++;this.render();}
  renderConfirmation(){
    const dialog=this.dialog,{mode,preview,keys,group}=this.confirmation,force=['scopeForce','force'].includes(mode),scope=mode.startsWith('scope');
    dialog.append(this.node('h3',force?'确认强制释放？':mode==='stop'?'停止任务并释放连接？':mode==='unload'?'释放原生占用？':'确认释放 CLI 连接？'));
    dialog.append(this.node('p',preview?.scopeLabel||group?.label,'appearance-note'));
    if(preview?.message)dialog.append(this.node('p',preview.message,'appearance-note'));
    dialog.append(this.node('p',force?'将中断运行中或状态未知的任务；范围可能包含未列出的会话。聊天历史保留。':scope||mode==='unload'?'此操作会退出此配置的整个共享 CLI 服务，包含其它连接。默认等待全部任务安全结束；期间请勿从其它客户端发起新任务。':mode==='stop'?'将停止所选连接的任务并逐条释放，聊天历史保留。':'逐条等待所选 CLI 任务安全结束后释放连接，不退出其它配置。','appearance-note'));
    if(mode==='wait')dialog.append(this.node('p','此操作暂不支持取消等待；关闭面板不会取消已提交的释放。','appearance-note'));
    const impact=this.node('ul');for(const item of preview?.impact||keys.map(key=>this.rows.find(row=>row.key===key)||{sessionId:key}))impact.append(this.node('li',item.title||item.sessionId));dialog.append(impact);
    const allowed=!preview||(force?preview.canForce===true:preview.canRelease===true),actions=this.node('div',undefined,'sm-confirm-actions'),confirm=this.node('button',force?'确认强制释放':mode==='stop'?'确认停止并释放':'确认释放','primary');confirm.type='button';confirm.disabled=!!this.pending||!allowed;confirm.onclick=()=>this.confirm();actions.append(confirm);this.button(actions,'返回连接列表',()=>this.back()).className='primary';dialog.append(actions);
  }
  confirm(){
    const value=this.confirmation;if(!value||this.pending||!this.valid(this.dialog))return;const {mode,preview,keys}=value;
    if(preview&&(mode==='scopeWait'?preview.canRelease!==true:preview.canForce!==true))return;
    const body=mode.startsWith('scope')?{mode,id:this.newId(),scopeKey:preview.scopeKey,connections:preview.connections,generation:preview.generation}:{mode,id:this.newId(),connections:keys,...(mode==='force'?{generation:preview.generation}:{})};
    return this.perform(body,'正在提交释放请求…');
  }
  submitLegacy(mode,keys){return this.perform({mode,id:this.newId(),connections:keys},mode==='check'?'正在核对结果':'正在提交释放请求…');}
}

class SessionMenu {
  constructor({translate=text=>text,notify=()=>{},newId=()=>crypto.randomUUID()}={}){
    this.t=translate;this.notify=notify;this.newId=newId;this.box=null;
    document.addEventListener('bridge-logged-out',()=>this.close({immediate:true}));
    document.addEventListener('visibilitychange',()=>{if(document.hidden)this.close({immediate:true});});
    window.addEventListener('resize',()=>this.position());
    window.visualViewport?.addEventListener('resize',()=>this.position());
  }
  node(tag,cls,text){const node=document.createElement(tag);node.className=cls||'';if(text!==undefined)node.textContent=text;return node;}
  icon(name){
    const paths={pin:'m8 3 8 0-1 6 3 3H6l3-3-1-6m4 9v8',pencil:'m4 16 12-12 4 4-12 12H4v-4m9-9 4 4',link:'m9 15 6-6m-7 3-2 2a4 4 0 0 0 6 6l3-3m-6-6 3-3a4 4 0 0 1 6 6l-2 2',folder:'M3 7V4h6l2 3h10v13H3V7',section:'M4 4h16v5H4zM4 14h16v6H4z',fork:'M6 3v12a5 5 0 0 0 5 5h7M6 8h7a5 5 0 0 0 5-5',list:'M8 5h12M8 12h12M8 19h12M3 5h1M3 12h1M3 19h1',info:'M12 8h.01M12 11v6M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0',archive:'M3 4h18v4H3zM5 8v12h14V8M10 12h4',copy:'M8 8h12v12H8zM16 8V4H4v12h4',back:'m14 5-7 7 7 7',chevron:'m9 5 7 7-7 7',close:'m6 6 12 12M18 6 6 18'};
    const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox','0 0 24 24');svg.setAttribute('aria-hidden','true');
    const path=document.createElementNS(svg.namespaceURI,'path');path.setAttribute('d',paths[name]||paths.info);svg.append(path);return svg;
  }
  close({immediate=false}={}){
    this.clearCliNetwork();
    this.finishClosing?.();
    const box=this.box;if(!box)return;
    this.box=null;
    const anchor=this.anchor;let timer,finished=false;
    const end=event=>{if(event.target===box&&!event.pseudoElement)finish();};
    const finish=()=>{
      if(finished)return;finished=true;clearTimeout(timer);box.removeEventListener('animationend',end);
      if(this.finishClosing===finish)this.finishClosing=null;
      box.close();box.remove();if(!this.box&&anchor?.element?.isConnected)anchor.element.focus();
    };
    if(immediate||!box.open||window.matchMedia('(prefers-reduced-motion: reduce)').matches){this.clearDrag?.();finish();return;}
    const visual=getComputedStyle(box);
    box.style.setProperty('--sm-exit-transform',visual.transform);box.style.setProperty('--sm-exit-opacity',visual.opacity);
    box.style.setProperty('--sm-exit-backdrop-opacity',getComputedStyle(box,'::backdrop').opacity);
    this.clearDrag?.();
    box.style.removeProperty('animation');
    box.inert=true;box.classList.add('sm-closing');this.finishClosing=finish;
    box.addEventListener('animationend',end);timer=setTimeout(finish,260);
  }
  back(){if(!this.box)return;if(this.view!=='main'){this.render();this.box.querySelector('.sm-row:not(:disabled),button')?.focus();}else this.close();}
  clearCliNetwork(){clearTimeout(this.cliNetwork?.timer);this.cliNetwork=null;}
  networkCurrent(state){return this.cliNetwork===state&&this.context===state.context&&this.box===state.box&&this.box?.open&&this.view==='cli-network'&&(!state.context.isCurrent||state.context.isCurrent())&&(!state.context.cliNetworkIsCurrent||state.context.cliNetworkIsCurrent());}
  openCliNetwork(){
    this.clearCliNetwork();this.cliNetwork={context:this.context,box:this.box,data:null,error:'',pending:null,confirmation:null,uncertain:null};
    this.render('cli-network');return this.requestCliNetwork();
  }
  async requestCliNetwork(body,kind='check'){
    const state=this.cliNetwork;if(!state||state.pending||!this.networkCurrent(state))return;
    clearTimeout(state.timer);state.pending=kind;state.error='';state.confirmation=null;this.render('cli-network');
    try{
      const data=await state.context.cliNetwork(body);if(!this.networkCurrent(state))return;
      if(kind==='forcePreview'){
        if(data.canForce===true&&typeof data.generation==='string'&&data.scopeLabel&&Array.isArray(data.impact))state.confirmation={kind:'force',preview:data};
        else state.error=data.message||'当前无法核验原 CLI 进程或配置；未强制退出。';
      }else{state.data=data;state.uncertain=null;}
    }catch(error){
      if(!this.networkCurrent(state))return;state.error=error.message;
      if(['recover','force','reconnect'].includes(kind))state.uncertain={operationId:body.requestId||null};
    }finally{
      state.pending=null;if(this.networkCurrent(state)){
        this.render('cli-network');
        if(!state.confirmation&&!state.error&&['waiting','recovering'].includes(state.data?.state)&&state.data.operationId)
          state.timer=setTimeout(()=>{if(this.networkCurrent(state)&&!document.hidden)this.requestCliNetwork({action:'status',operationId:state.data.operationId},'status');},5000);
      }
    }
  }
  confirmCliNetwork(kind,preview){
    const state=this.cliNetwork;if(!state||state.pending||!this.networkCurrent(state))return;
    clearTimeout(state.timer);state.confirmation={kind,preview};this.render('cli-network');
  }
  submitCliNetwork(){
    const state=this.cliNetwork,confirmation=state?.confirmation;if(!confirmation||state.pending||!this.networkCurrent(state))return;
    const {kind,preview}=confirmation;
    const body=kind==='force'?{action:'force',confirmed:true,generation:preview.generation,operationId:preview.operationId??null,requestId:this.newId()}:
      kind==='reconnect'?{action:'reconnect',confirmed:true,generation:preview.generation,requestId:this.newId()}:{confirmed:true,generation:preview.generation};
    return this.requestCliNetwork(body,kind);
  }
  renderCliNetwork(){
    const state=this.cliNetwork,box=this.box;if(!state)return;
    const head=this.node('div','sm-head'),name=this.node('div','sm-name'),close=this.node('button','sm-icon');close.type='button';close.dataset.cliNetworkClose='true';close.setAttribute('aria-label',this.t('关闭'));close.append(this.icon('close'));close.onclick=()=>this.close();name.append(this.node('strong','',this.t('检查 CLI 网络')));head.append(name,this.node('small','',state.context.title),close);box.append(head);
    const explain=text=>box.append(this.node('p','sm-explanation',this.t(text)));
    if(state.error){const error=this.node('p','sm-error',this.t(state.error));error.setAttribute('role','alert');box.append(error);}
    if(state.uncertain)explain('请求结果待核对；不会自动重试。请检查操作结果或重新检查网络。');
    if(state.pending){const labels={check:'正在检查 CLI 网络…',status:'正在核对恢复状态…',cancel:'正在取消等待…',forcePreview:'正在核对强制恢复范围…',recover:'正在提交 CLI 恢复…',force:'正在提交强制恢复…',reconnect:'正在提交重新连接…'},status=this.node('p','sm-explanation',this.t(labels[state.pending]));status.setAttribute('role','status');box.append(status);}
    const data=state.data,confirmation=state.confirmation;
    if(confirmation){
      const {kind,preview}=confirmation;explain(preview.message||'');
      if(kind==='force'){
        box.append(this.node('h3','sm-heading',this.t('确认强制恢复此配置？')));explain(preview.scopeLabel);
        explain('将中断此配置中正在运行或状态未知的任务，影响范围可能包含未列出的会话。聊天历史会保留。');
        const impact=this.node('ul','sm-explanation');for(const row of preview.impact)impact.append(this.node('li','',row.title||row.sessionId));box.append(impact);
      }else if(kind==='reconnect'){box.append(this.node('h3','sm-heading',this.t('从当前保存状态重新连接？')));explain('以前的接续回执仍未知；此操作不重放旧提供商设置或消息。');}
      else{box.append(this.node('h3','sm-heading',this.t('确认恢复共享服务？')));explain('将等待此配置的全部任务安全结束后恢复。恢复期间请勿从其他客户端发起新任务。');}
      const actions=this.node('div','sm-confirm-actions'),confirm=this.node('button','primary',this.t(kind==='force'?'确认强制恢复':kind==='reconnect'?'确认重新连接':'确认恢复')),cancel=this.node('button','primary',this.t('返回检查结果'));
      confirm.type=cancel.type='button';confirm.disabled=!!state.pending;confirm.onclick=()=>this.submitCliNetwork();cancel.onclick=()=>{state.confirmation=null;this.render('cli-network');};actions.append(confirm,cancel);box.append(actions);
    }else{
      if(data?.message||data?.notice)explain(data.message||data.notice);
      else if(typeof data?.connected==='boolean')explain(data.connected?'CLI 连接已恢复':'CLI 连接尚未确认，请继续原会话或重新检查。');
      if(data?.state){const labels={waiting:'等待任务安全结束',recovering:'正在恢复共享服务',cancelled:'已取消等待',complete:'恢复操作已结束',unknown:'操作结果待核对',failed:'恢复未完成',superseded:'此操作已被新的明确操作替代'};const status=this.node('p','sm-explanation',this.t(labels[data.state]||data.state));status.setAttribute('role','status');box.append(status);}
      if(data?.canRecover===true&&typeof data.generation==='string'&&!state.uncertain)this.row(data.action==='reconnect'?'从当前保存状态重新连接…':data.willWait||data.state==='waiting'?'等待任务结束后恢复…':'恢复 CLI 连接…','link',()=>this.confirmCliNetwork(data.action==='reconnect'?'reconnect':'recover',data),{disabled:!!state.pending});
      if(data?.canCancel===true&&data.operationId)this.row('取消等待','close',()=>this.requestCliNetwork({action:'cancel',operationId:data.operationId},'cancel'),{disabled:!!state.pending});
      const operationId=state.uncertain?.operationId||data?.operationId;if(operationId)this.row('核对操作结果','info',()=>this.requestCliNetwork({action:'status',operationId},'status'),{disabled:!!state.pending});
      this.row('重新检查网络','link',()=>this.requestCliNetwork(),{disabled:!!state.pending});
      this.row('检查强制恢复范围…','info',()=>this.requestCliNetwork({action:'forcePreview'},'forcePreview'),{disabled:!!state.pending,danger:true});
    }
    explain('关闭或返回只收起面板，不会取消已提交的恢复。');
  }
  bindDrag(box){
    let drag=null,timer;
    const reset=()=>{clearTimeout(timer);drag=null;box.classList.remove('sm-dragging','sm-returning');box.style.removeProperty('transform');};
    this.clearDrag=reset;
    box.addEventListener('pointerdown',event=>{
      if(event.pointerType==='mouse'||event.button!==0||!matchMedia('(max-width: 640px)').matches||!event.target.closest('.sm-handle,.sm-head,.sm-heading')||event.target.closest('button,input,a'))return;
      reset();drag={id:event.pointerId,x:event.clientX,y:event.clientY,distance:0};
      try{box.setPointerCapture(event.pointerId);}catch(_){/* Synthetic pointers do not own capture. */}
    });
    box.addEventListener('pointermove',event=>{
      if(!drag||drag.id!==event.pointerId)return;
      const y=event.clientY-drag.y,x=event.clientX-drag.x;
      if(!drag.distance&&Math.abs(x)>Math.max(8,Math.abs(y))){reset();return;}
      if(y<=8&&!drag.distance)return;
      drag.distance=Math.max(0,y);box.style.animation='none';box.classList.remove('sm-returning');box.classList.add('sm-dragging');box.style.transform=`translateY(${drag.distance}px)`;
    });
    const end=event=>{
      if(!drag||drag.id!==event.pointerId)return;
      const distance=drag.distance;drag=null;
      if(event.type==='pointerup'&&distance>=Math.min(96,Math.max(48,box.offsetHeight*.2))){this.close();return;}
      if(!distance){reset();return;}
      box.classList.add('sm-returning');box.classList.remove('sm-dragging');box.style.transform='translateY(0)';timer=setTimeout(reset,180);
    };
    box.addEventListener('pointerup',end);box.addEventListener('pointercancel',end);box.addEventListener('lostpointercapture',end);
    box.addEventListener('click',event=>{if(box.classList.contains('sm-returning')){event.preventDefault();event.stopPropagation();}},true);
  }
  show(context,anchor,view='main'){
    this.close({immediate:true});this.context=context;this.anchor=anchor;this.busy=false;
    const box=this.node('dialog','session-menu');this.box=box;box.setAttribute('aria-label',this.t('会话菜单'));
    box.addEventListener('close',()=>{box.remove();if(this.box===box){this.box=null;if(anchor?.element?.isConnected)anchor.element.focus();}});
    box.addEventListener('cancel',event=>{event.preventDefault();this.back();});
    this.bindDrag(box);
    box.addEventListener('keydown',event=>{
      if(!['ArrowDown','ArrowUp','Home','End'].includes(event.key)||event.target.matches('input,textarea'))return;
      const items=[...box.querySelectorAll('button:not(:disabled)')],index=items.indexOf(document.activeElement);
      const next=event.key==='Home'?0:event.key==='End'?items.length-1:(index+(event.key==='ArrowDown'?1:-1)+items.length)%items.length;
      items[next]?.focus();event.preventDefault();
    });
    document.body.append(box);this.render(view);box.showModal();this.position();
    box.querySelector('.sm-row:not(:disabled),button')?.focus();return box;
  }
  position(){
    if(!this.box?.open)return;
    const box=this.box;box.style.left='';box.style.top='';
    if(window.matchMedia('(max-width: 640px)').matches)return;
    const viewport=window.visualViewport,width=viewport?.width||window.innerWidth,height=viewport?.height||window.innerHeight;
    const rect=this.anchor?.element?.getBoundingClientRect(),x=this.anchor?.x??rect?.left??(width-box.offsetWidth)/2,y=this.anchor?.y??rect?.bottom??32;
    box.style.left=Math.max(8,Math.min(x,width-box.offsetWidth-8))+'px';box.style.top=Math.max(8,Math.min(y+4,height-box.offsetHeight-8))+'px';
  }
  row(label,icon,run,{end,disabled=false,danger=false}={}){
    const button=this.node('button','sm-row'+(danger?' sm-danger':''));button.type='button';button.disabled=disabled;
    button.append(this.icon(icon),this.node('span','',this.t(label)));if(end)button.append(this.icon('chevron'));
    button.onclick=()=>run(button);this.box.append(button);return button;
  }
  separator(){this.box.append(this.node('div','sm-divider'));}
  valid(){if(this.context.isCurrent&&!this.context.isCurrent())throw Error(this.t('连接或账号已变化，请重新打开会话菜单'));}
  async execute(run,button,{close=true,pendingMessage=null}={}){
    if(this.busy)return;const box=this.box;this.busy=true;if(button)button.disabled=true;
    let pending;if(pendingMessage){pending=this.node('p','sm-explanation',this.t(pendingMessage));pending.setAttribute('role','status');box.append(pending);this.position();}
    try{this.valid();const result=await run(button);if(this.box===box){if(close)this.close();else{this.render();if(result?.message){const message=this.node('p','sm-explanation',this.t(result.message));message.setAttribute('role','status');box.append(message);this.position();}}}}
    catch(error){if(box.isConnected){const message=box.querySelector('.sm-error')||this.node('p','sm-error');message.setAttribute('role','alert');message.textContent=this.t(error.message);box.append(message);this.position();}else this.notify(this.t(error.message));}
    finally{pending?.remove();if(this.box===box){this.busy=false;if(button?.isConnected)button.disabled=false;}}
  }
  render(view='main'){
    if(view!=='cli-network')this.clearCliNetwork();
    const c=this.context,box=this.box;this.clearDrag?.();this.view=view;box.replaceChildren();box.append(this.node('div','sm-handle'));
    if(view!=='main')this.row('返回会话菜单','back',()=>this.back());
    else{
      const head=this.node('div','sm-head'),line=this.node('div','sm-name'),title=this.node('strong','',c.title),close=this.node('button','sm-icon');
      close.type='button';close.setAttribute('aria-label',this.t('关闭'));close.append(this.icon('close'));close.onclick=()=>this.close();
      line.append(title);
      if(c.rename){const edit=this.node('button','sm-icon');edit.type='button';edit.setAttribute('aria-label',this.t('修改聊天名称'));edit.append(this.icon('pencil'));edit.onclick=()=>this.render('rename');line.append(edit);}
      head.append(line,this.node('small','',c.subtitle||''),close);box.append(head);
    }
    if(view==='cli-network'){
      this.renderCliNetwork();
    }else if(view==='rename'){
      if(c.renameScope==='bridge')box.append(this.node('p','sm-explanation',this.t('仅在 Bridge 中生效')));
      const form=this.node('form','sm-form'),input=this.node('input');input.value=c.title;input.maxLength=120;input.required=true;input.setAttribute('aria-label',this.t('聊天名称'));
      const save=this.node('button','primary',this.t('保存'));save.type='submit';form.append(input,save);box.append(form);
      form.onsubmit=event=>{event.preventDefault();const title=input.value.trim();if(title)this.execute(async()=>{await c.rename(title);c.title=title;},save,{close:false});};
      input.focus();input.select();
    }else if(view==='details'){
      const details=this.node('dl','sm-details');
      for(const [label,value,copy] of c.details||[]){if(value===undefined||value===null||value==='')continue;const line=this.node('div'),dd=this.node('dd','',String(value));line.append(this.node('dt','',this.t(label)),dd);
        if(copy){const button=this.node('button','sm-icon message-copy');button.type='button';button.setAttribute('aria-label',this.t('复制')+' '+this.t(label));button.append(this.icon('copy'));button.onclick=()=>this.copy(String(value),button);line.append(button);}details.append(line);}
      box.append(details);if(c.source)this.row('来源会话：'+c.source.title,'link',()=>{this.close();c.source.open();});
    }else if(view==='project'||view==='section'){
      box.append(this.node('h3','sm-heading',this.t(view==='project'?'项目':'分区')));
      this.loadOrganization(view,c,box);
    }else if(view==='delete'){
      box.append(this.node('h3','sm-heading',this.t('永久删除聊天？')),this.node('p','sm-explanation',c.subtitle||''));
      this.loadDeletion(c,box);
    }else if(view==='archive'){
      box.append(this.node('h3','sm-heading',this.t('停止任务并归档？')),this.node('p','sm-explanation',c.title),this.node('p','sm-explanation',c.subtitle||''),this.node('p','sm-explanation',this.t('将停止此聊天及其子任务，并在确认停止后归档。聊天记录会保留，未完成任务需要手动继续。')));
      const actions=this.node('div','sm-confirm-actions'),confirm=this.node('button','primary',this.t('确认归档')),cancel=this.node('button','primary',this.t('取消'));
      confirm.type=cancel.type='button';confirm.onclick=()=>this.execute(c.archive,confirm);cancel.onclick=()=>this.render();
      actions.append(confirm,cancel);box.append(actions);
      queueMicrotask(()=>cancel.focus());
    }else{
      this.row(c.pinned?'取消置顶':'置顶会话','pin',button=>this.execute(async()=>{await c.pin(!c.pinned);c.pinned=!c.pinned;},button,{close:false}));
      if(c.nativeOrganization){this.row('项目','folder',()=>this.render('project'),{end:true});this.row('分区','section',()=>this.render('section'),{end:true});}
      this.separator();
      // Native shells authorize copying in the capture phase of the first click.
      if(c.link)this.row(c.linkLabel||'复制深度链接','link',button=>this.copy(c.link,button)).classList.add('message-copy');
      else if(c.sessionId)this.row('复制 Session ID','copy',button=>this.copy(c.sessionId,button)).classList.add('message-copy');
      if(c.fork)this.row('创建聊天分支','fork',button=>this.execute(()=>{this.close();return c.fork();},button));
      this.separator();if(c.manage)this.row('发送管理','list',()=>{this.close();c.manage();});
      if(c.notifications)this.row('聊天通知设置','info',button=>this.execute(()=>{this.close();return c.notifications();},button));
      if(c.cliNetwork)this.row('检查 CLI 网络','link',()=>this.openCliNetwork());
      if(c.releaseCli)this.row('释放 CLI 连接','link',()=>{this.close();c.releaseCli();});
      this.row('会话详情','info',()=>this.render('details'),{end:true});
      if(c.archive){this.separator();this.row(c.archived?'恢复会话':'停止任务并归档…','archive',button=>c.archived?this.execute(c.archive,button):this.render('archive'),{danger:!c.archived});}
      if(c.deletion)this.row('永久删除…','close',()=>this.render('delete'),{danger:true});
    }
    this.position();
  }
  async loadDeletion(context,box){
    const pending=this.node('p','sm-explanation',this.t('正在核对删除范围…'));box.append(pending);
    const active=()=>this.box===box&&this.context===context&&this.view==='delete'&&pending.isConnected;
    try{
      this.valid();const snapshot=await context.deletion();
      if(!active())return;this.valid();pending.remove();
      box.append(this.node('p','sm-explanation',this.t('以下聊天及其子任务的历史与原生关联内容将永久删除，无法撤销。正在运行的任务须先停止。')));
      const list=this.node('ul','sm-explanation');for(const row of snapshot.sessions)list.append(this.node('li','',row.title));box.append(list);
      if(snapshot.subagentCount)box.append(this.node('p','sm-explanation',this.t('包含子任务：')+snapshot.subagentCount));
      let verified;
      if(snapshot.retry?.length){
        box.append(this.node('p','sm-explanation',this.t('此前删除未确认，以上为仍存在的聊天。不会自动重试；请先在原客户端核对，再决定是否重新删除。')));
        const label=this.node('label','sm-explanation');verified=document.createElement('input');verified.type='checkbox';
        label.append(verified,this.node('span','',this.t('已核对，重新删除以上仍存在的聊天')));box.append(label);
      }
      const actions=this.node('div','sm-confirm-actions'),confirm=this.node('button','primary',this.t('确认永久删除')),cancel=this.node('button','primary',this.t('取消'));
      if(verified){confirm.disabled=true;verified.onchange=()=>{confirm.disabled=!verified.checked;};}
      confirm.type=cancel.type='button';confirm.onclick=async()=>{
        if(verified&&!verified.checked)return;
        if(verified)verified.disabled=true;
        try{await this.execute(()=>context.deletion(snapshot),confirm);}
        finally{if(verified){verified.disabled=false;confirm.disabled=!verified.checked;}}
      };cancel.onclick=()=>this.render();
      actions.append(confirm,cancel);box.append(actions);this.position();cancel.focus();
    }catch(error){
      if(!active())return;pending.remove();const message=this.node('p','sm-error',this.t(error.message));message.setAttribute('role','alert');box.append(message);this.position();
    }
  }
  async loadOrganization(view,context,box){
    const pending=this.node('p','sm-explanation',this.t('正在读取原生侧边栏…'));box.append(pending);
    const active=()=>this.box===box&&this.context===context&&this.view===view&&pending.isConnected;
    try{
      this.valid();const snapshot=await context.organization();
      if(!active())return;this.valid();pending.remove();
      const scope=snapshot[view+'Scope']||snapshot.scope||context.organizationScope||'native',local=scope==='bridge';
      if(local)box.append(this.node('p','sm-explanation',this.t('仅在 Bridge 中生效')));
      const field=view==='project'?'projectId':'sectionId',selected=snapshot[field];
      const choices=[{id:null,name:this.t(view==='project'?'移出项目':'移出分区')},...snapshot[view==='project'?'projects':'sections']];
      for(const choice of choices){
        const requestId=this.newId();
        const label=choice.title||choice.name;
        const button=this.row(label,view==='project'?'folder':'section',button=>this.execute(async()=>{
          const result=await context.organization({id:requestId,kind:view,targetId:choice.id,expectedId:selected});
          if(result.status!=='confirmed')throw Error(this.t('分类结果待核对，请刷新会话列表'));
          this.notify(this.t(local?'已更新 Bridge 分类':'已同步到原生侧边栏'));
        },button),{disabled:choice.id===selected});
        button.querySelector('span').textContent=label;
        button.setAttribute('aria-pressed',String(choice.id===selected));
        if(choice.id===selected)button.append(this.node('span','sm-selected','✓'));
      }
      if(local){
        const form=this.node('form','sm-form'),input=this.node('input'),add=this.node('button','primary',this.t('添加'));
        const label=this.t(view==='project'?'新项目名称':'新分区名称');input.maxLength=120;input.required=true;input.placeholder=label;input.setAttribute('aria-label',label);add.type='submit';form.append(input,add);box.append(form);
        let request=null;
        form.onsubmit=event=>{event.preventDefault();const title=input.value.trim();if(!title)return;
          if(!request||request.title!==title)request={id:this.newId(),kind:'create-'+view,title};
          this.execute(async()=>{const result=await context.organization(request);if(result.status!=='confirmed')throw Error(this.t('分类结果待核对，请刷新会话列表'));this.notify(this.t('已更新 Bridge 分类'));},add);
        };
      }else if(choices.length===1)box.append(this.node('p','sm-explanation',this.t(view==='project'?'暂无原生项目，请先在原客户端中创建':'暂无原生分区，请先在原客户端中创建')));
      this.position();
    }catch(error){
      if(!active())return;pending.remove();const message=this.node('p','sm-error',this.t(error.message));message.setAttribute('role','alert');box.append(message);this.position();
    }
  }
  copy(text,button){
    const box=this.box;
    const copied=message=>{if(box&&this.box===box)this.close({immediate:true});this.notify(message);};
    try{this.valid();return Promise.resolve(window.BridgeClipboard.copyWithFeedback(()=>text,button,copied)).catch(error=>this.notify(this.t(error.message)));}
    catch(error){this.notify(this.t(error.message));}
  }
  bind(element,context){
    let timer=null,start=null,opened=false,releaseTimer=null;
    const clear=()=>{clearTimeout(timer);timer=null;start=null;};
    const show=anchor=>this.show(context(),{element,...anchor});
    element.addEventListener('contextmenu',event=>{
      event.preventDefault();const touch=event.pointerType==='touch'||!!start;clear();
      if(!opened){opened=touch;show(touch?{}:{x:event.clientX,y:event.clientY});}
    });
    element.addEventListener('pointerdown',event=>{clear();if(event.pointerType==='mouse'||event.button>0)return;clearTimeout(releaseTimer);opened=false;start={x:event.clientX,y:event.clientY};timer=setTimeout(()=>{if(element.isConnected){opened=true;show();}},550);});
    element.addEventListener('pointermove',event=>{if(start&&Math.hypot(event.clientX-start.x,event.clientY-start.y)>10)clear();});
    for(const name of ['pointerup','pointercancel','pointerleave','lostpointercapture'])element.addEventListener(name,()=>{clear();if(opened)releaseTimer=setTimeout(()=>{opened=false;},800);});
    element.addEventListener('click',event=>{if(opened){event.preventDefault();event.stopImmediatePropagation();opened=false;}},true);
    element.addEventListener('keydown',event=>{if(event.key==='ContextMenu'||event.key==='F10'&&event.shiftKey){event.preventDefault();show();}});
  }
}
if(typeof module!=='undefined')module.exports={SessionMenu};
