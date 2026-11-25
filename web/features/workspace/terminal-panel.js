'use strict';

class TerminalPanel {
  constructor(workbench, session, tab) {
    Object.assign(this, {workbench, session, tab});this.disposed=false;this.cursor=0;this.running=false;this.operations=[];this.composing=false;this.completionVersion=0;this.inputMode='direct';
    this.visibility=()=>{clearTimeout(this.timer);if(document.hidden)this.readController?.abort();else if(this.running){if(this.polling)this.resumeRead=true;else this.poll();}};
    document.addEventListener('visibilitychange',this.visibility);
    this.key='pty:'+session.key;
    try{this.id=sessionStorage.getItem(this.key);}catch{}
    this.hadId=!!this.id;this.id ||= uuid();this.save();
    this.tab.name=BridgeI18n.t('终端 · 连接中');this.workbench.paint();
    this.load();
  }
  save(){try{sessionStorage.setItem(this.key,this.id);}catch{}}
  url(extra=''){return this.workbench.endpoint(this.session,'terminal')+'&mode=pty'+extra;}
  async load(){
    try{
      const info=await this.workbench.request(this.url());if(this.disposed)return;
      if(info.mode==='command'){
        this.fallback=new CommandTerminalPanel(this.workbench,this.session,this.tab);return;
      }
      this.render(info);
      if(this.hadId){
        try{
          const result=await this.workbench.request(this.url('&id='+this.id+'&after='+this.cursor+'&waitMs=0'));
          if(this.disposed)return;
          this.apply(result);this.startDuplex();this.schedule();this.focusOnSelect();return;
        }catch(e){
          if(this.disposed)return;
          if(e.status===404&&e.code==='terminal_missing'){
            this.hadId=false;this.id=uuid();this.save();
          }else if(![401,403,404].includes(e.status)){
            this.running=true;this.status.textContent=BridgeI18n.t(e.message);this.schedule(true);return;
          }else throw e;
        }
      }
      const result=await this.workbench.request(this.url(),{action:'open',id:this.id,cols:this.term.cols,rows:this.term.rows});
      if(this.disposed){if(!this.hadId)this.workbench.request(this.url(),{action:'close',id:this.id}).catch(()=>{});return;}
      this.apply(result);this.startDuplex();this.schedule();this.focusOnSelect();
    }catch(e){if(!this.disposed){if(!this.status)this.render({});this.status.textContent=BridgeI18n.t(e.message);this.reopen.hidden=false;this.tab.name=BridgeI18n.t('终端 · 未连接');this.workbench.paint();}}
  }
  render(info){
    const n=(...a)=>this.workbench.node(...a),b=(...a)=>this.workbench.button(...a);
    this.tab.body.replaceChildren();this.tab.body.classList.add('wb-pty');
    const head=n('div','wb-terminal-head wb-terminal-font-head');
    this.reopen=b(BridgeI18n.t('重新打开'),()=>this.restart());this.reopen.hidden=true;
    const font=TerminalFont.control(this.workbench);this.fontControl=font;
    this.modeButton=b('',()=>this.toggleInputMode());this.updateInputMode();
    head.append(this.modeButton,font.label,b(BridgeI18n.t('清屏'),()=>this.clearScreen()));
    this.shellHint=n('small','wb-terminal-shell-hint',BridgeI18n.t('跨盘切换示例：cd /d D:\\（进入 D 盘根目录）。'));this.shellHint.hidden=!/(^|[\\/])cmd(?:\.exe)?$/i.test(info.shell||'');
    this.screen=n('div','wb-terminal-screen');this.screen.setAttribute("data-i18n-aria-label",'交互终端');this.screen.setAttribute('aria-label',BridgeI18n.t('交互终端'));
    this.status=n('p','wb-status');this.status.setAttribute('role','status');
    this.location=n('small','wb-terminal-shell-location',info.shell||BridgeI18n.t('连接终端…'));this.location.title=info.shell||'';
    const footer=n('div','wb-terminal-footer');footer.append(this.status,this.location,this.reopen);
    const keys=n('div','wb-terminal-keys');
    for(const [label,value] of [['Ctrl+C','\x03'],['Tab','\t'],['Enter','\r'],['Esc','\x1b'],['↑','\x1b[A'],['↓','\x1b[B'],['←','\x1b[D'],['→','\x1b[C']])keys.append(b(label,()=>this.shortcut(label,value)));
    this.form=n('form','wb-terminal-input');this.input=n('textarea');this.input.rows=2;this.input.setAttribute("data-i18n-placeholder",'输入命令或回复，换行键可换行');this.input.placeholder=BridgeI18n.t('输入命令或回复，换行键可换行');this.input.setAttribute("data-i18n-aria-label",'终端输入');this.input.setAttribute('aria-label',BridgeI18n.t('终端输入'));this.input.setAttribute('autocapitalize','off');this.input.setAttribute('autocorrect','off');this.input.spellcheck=false;
    this.send=n('button','',BridgeI18n.t('发送到终端'));this.send.type='submit';this.send.disabled=true;
    this.form.append(this.input,this.send);this.form.onsubmit=e=>{e.preventDefault();this.submit();};
    this.input.addEventListener('compositionstart',()=>{this.composing=true;this.clearCompletion();});
    this.input.addEventListener('compositionend',()=>{this.composing=false;this.clearCompletion();});
    this.input.addEventListener('input',()=>this.clearCompletion());
    this.input.addEventListener('click',()=>this.clearCompletion());
    this.input.addEventListener('select',()=>this.selectionChanged());
    this.input.addEventListener('keydown',e=>this.handleInputKeydown(e));
    this.completions=n('div','wb-terminal-completions');this.completions.hidden=true;
    // Enter in the mobile composer ALWAYS inserts a newline. Submit is explicit.
    this.retry=b(BridgeI18n.t('重试未确认输入'),()=>{this.inputError=false;this.retry.hidden=true;this.flush();});this.retry.hidden=true;
    this.tab.body.append(head,this.shellHint,this.screen,keys,this.form,this.completions,footer,this.retry);
    this.term=new Terminal({fontSize:TerminalFont.size(),fontFamily:'Menlo, Consolas, monospace',cursorBlink:false,scrollback:2000,
      theme:{background:'#11151b',foreground:'#e2e8f0',cursor:'#ffffff',cursorAccent:'#11151b'},cursorStyle:'block',cursorInactiveStyle:'block',allowProposedApi:false,
      linkHandler:{activate:()=>{}},disableStdin:true});
    // Terminal output must never write to or read the system clipboard (OSC 52).
    this.term.parser.registerOscHandler(52,()=>true);
    this.fit=new FitAddon.FitAddon();this.term.loadAddon(this.fit);this.term.open(this.screen);
    this.term.onWriteParsed(()=>this.syncTouchMode());
    this.term.attachCustomWheelEventHandler(e=>{this.beginWheelInput(e);return true;});
    this.screen.addEventListener('touchstart',e=>this.touchStart(e),{capture:true,passive:true});
    this.screen.addEventListener('touchmove',e=>this.touchMove(e),{capture:true,passive:false});
    this.screen.addEventListener('touchend',e=>this.touchEnd(e),{capture:true,passive:true});
    this.screen.addEventListener('touchcancel',e=>this.touchEnd(e),{capture:true,passive:true});
    this.term.onData(data=>this.terminalData(data));
    this.term.onResize(({cols,rows})=>this.queueResize(cols,rows));
    this.observer=new ResizeObserver(()=>this.layout());this.observer.observe(this.screen);this.layout();
    TerminalFont.register(this);
  }
  applyFontSize(size){
    TerminalFont.paint(this.fontControl,size);this.input.style.fontSize=Math.max(16,size)+'px';
    this.term.options.fontSize=size;this.layout();
  }
  mobile(){return BridgeHost.hasNativeLayout()||!!(navigator.userAgentData?.mobile||/\b(?:iPhone|iPad|iPod|Android|Mobile)\b/i.test(navigator.userAgent||'')||(navigator.platform==='MacIntel'&&navigator.maxTouchPoints>1));}
  terminalData(data){
    if(this.submitting||this.inputMode==='direct'||(this.wheelInput&&this.term.modes.mouseTrackingMode!=='none'))this.enqueue(data);
  }
  beginWheelInput(e){
    if(this.inputMode!=='draft'||!this.running||this.inputError||this.term.modes.mouseTrackingMode==='none')return;
    this.wheelEvent=e;this.wheelInput=true;this.wheelRestore=this.term.options.disableStdin;this.term.options.disableStdin=false;
    // Restore after xterm finishes encoding this wheel event, including late mouse-protocol listeners.
    queueMicrotask(()=>this.endWheelInput(e));
  }
  endWheelInput(e){
    if(this.wheelEvent!==e)return;
    this.term.options.disableStdin=this.wheelRestore;this.wheelInput=false;this.wheelEvent=null;
  }
  syncTouchMode(){
    if(this.term?.element)this.term.element.style.touchAction=this.term.modes.mouseTrackingMode==='none'?'':'none';
  }
  touchStart(e){
    this.syncTouchMode();
    this.touchPoint=e.touches.length===1?{id:e.touches[0].identifier,y:e.touches[0].pageY}:null;
  }
  touchMove(e){
    const point=this.touchPoint;
    if(!point||e.touches.length!==1){this.touchPoint=null;return;}
    const touch=e.touches[0];
    if(touch.identifier!==point.id){this.touchPoint=null;return;}
    const deltaY=point.y-touch.pageY;point.y=touch.pageY;
    if(!deltaY||this.term.modes.mouseTrackingMode==='none'||this.term.hasSelection()||window.getSelection?.()?.toString())return;
    // Let xterm encode wheel input using the application's active mouse protocol.
    this.term.element.dispatchEvent(new WheelEvent('wheel',{bubbles:true,cancelable:true,deltaY,deltaMode:WheelEvent.DOM_DELTA_PIXEL,clientX:touch.clientX,clientY:touch.clientY}));
    e.preventDefault();e.stopPropagation();
  }
  touchEnd(e){
    if(e.touches.length!==1||Array.from(e.changedTouches).some(touch=>touch.identifier===this.touchPoint?.id))this.touchPoint=null;
  }
  updateInputMode(){
    if(this.modeButton){this.modeButton.textContent=BridgeI18n.t(this.inputMode==='draft'?'编写后发送模式':'终端直接输入模式');this.modeButton.title=BridgeI18n.t(this.inputMode==='draft'?'切换为终端直接输入模式':'切换为编写后发送模式');}
    if(this.input){this.input.placeholder=BridgeI18n.t(this.mobile()?'输入命令或回复，换行键可换行':'编写后发送模式：Enter 发送，Shift+Enter 换行；密码请用终端直接输入模式');}
    this.tab.body.classList.toggle('wb-terminal-draft',this.inputMode==='draft');
    this.tab.body.classList.toggle('wb-terminal-direct',this.inputMode==='direct');
    if(this.term?.textarea){this.term.textarea.readOnly=this.inputMode==='draft';this.term.textarea.tabIndex=this.inputMode==='draft'?-1:0;if(this.inputMode==='draft'&&document.activeElement===this.term.textarea)this.term.blur();}
  }
  toggleInputMode(){
    this.inputMode=this.inputMode==='draft'?'direct':'draft';
    this.clearCompletion();this.updateInputMode();this.layout();this.focusOnSelect();
  }
  handleInputKeydown(e){
    if(e.isComposing||this.composing)return;
    if(e.key==='Tab'){e.preventDefault();this.complete();return;}
    if(e.key==='Enter'&&!this.mobile()&&!e.shiftKey){e.preventDefault();if(!e.repeat)this.submit();return;}
    this.clearCompletion();
  }
  moveDraftCursor(label){
    const text=this.input.value,start=this.input.selectionStart,end=this.input.selectionEnd;
    let position=start;
    if(label==='←')position=start===end?Array.from(text.slice(0,start)).slice(0,-1).join('').length:start;
    if(label==='→')position=start===end?end+(Array.from(text.slice(end))[0]?.length||0):end;
    if(label==='↑'||label==='↓'){
      const lineStart=text.lastIndexOf('\n',start-1)+1,column=Array.from(text.slice(lineStart,start)).length;
      if(label==='↑'){
        const previousEnd=lineStart-1,previousStart=previousEnd<0?0:text.lastIndexOf('\n',previousEnd-1)+1;
        position=previousEnd<0?start:previousStart+Array.from(text.slice(previousStart,previousEnd)).slice(0,column).join('').length;
      }else{
        const nextStart=text.indexOf('\n',start)+1;
        if(nextStart>0){const nextEnd=text.indexOf('\n',nextStart);position=nextStart+Array.from(text.slice(nextStart,nextEnd<0?text.length:nextEnd)).slice(0,column).join('').length;}
        else position=start;
      }
    }
    this.input.setSelectionRange(position,position);this.clearCompletion();
  }
  shortcut(label,value){
    if(label==='Enter'){
      if(!this.running||this.disposed||this.restarting||this.composing||this.inputError)return;
      this.enqueue('\r');this.focusOnSelect();return;
    }
    if(this.inputMode==='draft'){
      if(label==='Tab')this.complete();
      else if(label==='Ctrl+C')this.enqueue(value);
      else if(label==='Esc'){this.input.value='';this.clearCompletion();}
      else this.moveDraftCursor(label);
      this.focusOnSelect();return;
    }
    this.enqueue(value);this.focusOnSelect();
  }
  clearScreen(){this.term.clear();this.focusOnSelect();}
  layout(){
    if(this.disposed||!this.screen?.clientHeight||!this.screen.clientWidth)return;
    this.tab.body.classList.toggle('wb-terminal-touch',this.mobile());this.updateInputMode();this.term.options.disableStdin=!this.running||this.inputMode==='draft'||!!this.inputError;
    const size=this.fit.proposeDimensions?.();if(!size||!Number.isFinite(size.cols)||!Number.isFinite(size.rows))return;
    const cols=Math.max(2,Math.min(500,Math.floor(size.cols))),rows=Math.max(2,Math.min(300,Math.floor(size.rows)));
    if(cols!==this.term.cols||rows!==this.term.rows)this.term.resize(cols,rows);
  }
  focusOnSelect(){
    if(this.disposed||!this.running||this.mobile()||!this.workbench.isVisible(this.session,this.tab)||document.hidden||this.composing)return;
    if(document.querySelector('dialog[open]')||window.getSelection?.()?.toString())return;
    const active=document.activeElement;
    if(active&&active!==document.body&&!this.tab.body.contains(active)&&active.matches?.('input,textarea,[contenteditable="true"]'))return;
    this.layout();
    if(this.inputMode==='draft')this.input.focus();else this.term.focus();
  }
  selectionChanged(){
    const state=this.completionSnapshot;
    if(state&&state.id===this.id&&state.text===this.input.value&&state.start===this.input.selectionStart&&state.end===this.input.selectionEnd)return;
    this.clearCompletion();
  }
  clearCompletion(){this.completionVersion++;this.completionSnapshot=null;if(this.completions){this.completions.hidden=true;this.completions.replaceChildren();}}
  async complete(){
    if(!this.running||this.disposed||this.inputMode!=='draft'||this.composing||this.inputError)return;
    if(!this.input.value){this.clearCompletion();this.enqueue('\t');return;}
    if(this.completing&&this.completing.version===this.completionVersion&&this.completing.id===this.id&&this.completing.text===this.input.value&&this.completing.start===this.input.selectionStart&&this.completing.end===this.input.selectionEnd)return;
    this.clearCompletion();const version=this.completionVersion,id=this.id,text=this.input.value,selectionStart=this.input.selectionStart,selectionEnd=this.input.selectionEnd;
    if(selectionStart!==selectionEnd)return;
    const pending=this.completing=this.completionSnapshot={version,id,text,start:selectionStart,end:selectionEnd};
    this.completions.append(this.workbench.node('span','',BridgeI18n.t('正在补全…')));this.completions.hidden=false;
    try{
      const result=await this.workbench.request(this.url(),{action:'complete',id,text,selectionStart});
      if(this.disposed||id!==this.id||version!==this.completionVersion||text!==this.input.value||selectionStart!==this.input.selectionStart||selectionEnd!==this.input.selectionEnd||this.inputMode!=='draft'||!this.workbench.isVisible(this.session,this.tab))return;
      this.completions.replaceChildren();
      const candidates=result.candidates||[];
      const apply=candidate=>{
        if(version!==this.completionVersion||text!==this.input.value||selectionStart!==this.input.selectionStart||selectionEnd!==this.input.selectionEnd||id!==this.id||!this.workbench.isVisible(this.session,this.tab))return;
        this.input.setRangeText(candidate.insertText,result.replaceStart,result.replaceEnd,'end');this.clearCompletion();this.input.focus();
      };
      if(candidates.length===1&&!result.truncated){apply(candidates[0]);return;}
      if(!candidates.length){this.completions.append(this.workbench.node('span','',BridgeI18n.t('没有匹配的路径')));this.completions.hidden=false;return;}
      if(!result.truncated&&result.commonPrefixInsertText){apply({insertText:result.commonPrefixInsertText});return;}
      this.completions.append(this.workbench.node('span','',BridgeI18n.t('选择路径补全')));
      for(const candidate of candidates){const button=this.workbench.button(candidate.label,()=>apply(candidate));button.type='button';this.completions.append(button);}
      if(result.truncated)this.completions.append(this.workbench.node('small','',BridgeI18n.t('仅显示部分匹配项')));
      this.completions.hidden=false;
    }catch(e){if(!this.disposed&&id===this.id&&version===this.completionVersion&&text===this.input.value&&selectionStart===this.input.selectionStart&&selectionEnd===this.input.selectionEnd&&this.inputMode==='draft'&&this.workbench.isVisible(this.session,this.tab)){this.completions.replaceChildren();this.completions.append(this.workbench.node('span','',BridgeI18n.t(e.message)));this.completions.hidden=false;}}
    finally{if(this.completing===pending)this.completing=null;}
  }
  async submit(){
    if(!this.running||this.send.disabled||this.composing||this.inputMode!=='draft')return;
    const text=this.input.value;
    // xterm may wrap bracketed paste in two six-byte markers before onData.
    if(this.inputBytes(text.replace(/\r?\n/g,'\r'))+13+this.operations.reduce((size,entry)=>size+this.inputBytes(entry.data),0)>262144){
      this.status.textContent=BridgeI18n.t('终端输入过长或队列已满；草稿仍在，请稍后重试');return;
    }
    // xterm honours bracketed-paste mode set by the active shell/application.
    if(text){this.term.options.disableStdin=false;this.submitting=true;try{this.term.paste(text);}finally{this.submitting=false;this.layout();}}
    this.enqueue('\r');this.input.value='';this.clearCompletion();this.input.focus();
  }
  enqueue(data){
    if(!this.running||this.disposed)return;
    if(this.inputBytes(data)+this.operations.reduce((size,entry)=>size+this.inputBytes(entry.data),0)>262144){
      this.queueFull=true;this.status.textContent=BridgeI18n.t('终端输入队列已满；这部分输入未发送');return;
    }
    // Keep each Unicode character intact while staying below the server input cap.
    let chunk='';for(const char of data){if(chunk.length+char.length>3000){this.queueChunk(chunk);chunk='';}chunk+=char;}
    if(chunk)this.queueChunk(chunk);
    if(!this.flushTimer)this.flushTimer=setTimeout(()=>{this.flushTimer=null;this.flush();},12);
  }
  inputBytes(data){return typeof TextEncoder==='function'?new TextEncoder().encode(data).length:data.length*4;}
  queueChunk(data){
    const tail=this.operations[this.operations.length-1];
    if(tail&&!tail.sent&&tail.data.length+data.length<=3000)tail.data+=data;
    else this.operations.push({id:uuid(),data,sent:false});
  }
  queueResize(cols,rows){
    if(!this.running||this.disposed||this.restarting)return;
    this.pendingResize={cols,rows};clearTimeout(this.resizeTimer);
    this.resizeTimer=setTimeout(()=>{this.resizeTimer=null;if(this.duplexReady)this.flushDuplex();else if(!this.duplexConnecting)this.flushResize().catch(e=>{if(!this.disposed)this.status.textContent=BridgeI18n.t(e.message);});},120);
  }
  async flushResize(){
    clearTimeout(this.resizeTimer);this.resizeTimer=null;
    while(!this.disposed&&!this.restarting){
      if(this.resizeFlight){await this.resizeFlight;continue;}
      if(!this.pendingResize)return;
      const size=this.pendingResize,id=this.id;this.pendingResize=null;
      const request=this.resizeFlight=this.workbench.request(this.url(),{action:'resize',id,cols:size.cols,rows:size.rows});
      try{await request;}
      catch(e){if(this.id===id&&!this.pendingResize)this.pendingResize=size;throw e;}
      finally{if(this.resizeFlight===request)this.resizeFlight=null;}
    }
  }
  async flush(){
    if(this.flushing||this.inputError||this.disposed||this.restarting)return;
    clearTimeout(this.flushTimer);this.flushTimer=null;
    if(this.duplexReady){this.layout();this.flushDuplex();return;}
    if(this.duplexConnecting)return;
    this.flushing=true;const id=this.id;
    try{
      while(this.operations.length&&!this.disposed&&!this.restarting&&!this.duplexReady&&this.id===id){
        this.layout();
        await this.flushResize();
        if(this.disposed||this.restarting||this.id!==id)break;
        const entry=this.operations[0];entry.sent=true;
        await this.workbench.request(this.url(),{action:'input',id:this.id,inputId:entry.id,data:entry.data});
        if(this.disposed||this.restarting||this.id!==id)break;
        this.operations.shift();
        this.clearQueueNotice();
      }
    }catch(e){if(!this.disposed&&!this.restarting&&this.id===id){this.inputError=true;this.status.textContent=BridgeI18n.t(e.message)+BridgeI18n.t(this.operations[0]?.sent?' · 输入结果未确认；重试不会重复输入。':' · 终端尺寸未同步；输入尚未发送。');this.retry.hidden=false;this.send.disabled=true;this.term.options.disableStdin=true;}}
    finally{this.flushing=false;if(!this.disposed&&!this.inputError){this.send.disabled=!this.running;this.layout();this.flushDuplex();if(this.hadDuplex&&!this.duplex&&!this.duplexUnsupported&&!this.reconnectTimer)this.startDuplex();}}
  }
  startDuplex(){
    if(window.BridgeCloudWeb||window.BridgeChannelLink?.alternate||this.disposed||!this.running||this.duplexUnsupported||this.duplex||typeof WebSocket!=='function'||!this.workbench.csrf?.())return;
    let socket;
    try{
      const address=new URL(this.url('&id='+encodeURIComponent(this.id)+'&after='+this.cursor),location.href);
      address.protocol=address.protocol==='https:'?'wss:':'ws:';
      socket=new WebSocket(address.href,['terminal.v1',this.workbench.csrf()]);
    }catch{return;}
    this.duplex=socket;this.duplexConnecting=true;
    const id=this.id;
    const timeout=setTimeout(()=>{if(socket.readyState===WebSocket.CONNECTING)socket.close();},5000);
    socket.onopen=()=>{
      clearTimeout(timeout);
      if(this.disposed||this.restarting||this.duplex!==socket||id!==this.id){socket.close();return;}
      this.duplexConnecting=false;this.duplexReady=true;
      this.hadDuplex=true;
      this.readController?.abort();clearTimeout(this.timer);
      if(!this.flushing)this.flushDuplex();
    };
    socket.onmessage=event=>{
      if(this.disposed||this.duplex!==socket||id!==this.id)return;
      let message;try{message=JSON.parse(event.data);}catch{socket.close();return;}
      if(message.type==='state'&&message.value){
        const acknowledged=()=>{if(this.duplex===socket&&socket.readyState===WebSocket.OPEN)socket.send(JSON.stringify({action:'rendered',seq:message.seq}));};
        if(message.value.cursor>this.cursor||(message.value.reset&&message.value.cursor!==this.cursor))this.apply(message.value,acknowledged);
        else if(message.value.cursor===this.cursor)this.apply({...message.value,output:'',reset:false},acknowledged);
        else acknowledged();
      }else if(message.type==='ack'){
        if(message.action==='input'){
          const entry=this.operations[0];
          if(!entry||!entry.sent||entry.id!==message.id){socket.close();return;}
          this.operations.shift();this.flushDuplex();
          this.clearQueueNotice();
        }
      }else if(message.type==='error'){
        this.status.textContent=BridgeI18n.t(message.message||'终端连接失败');socket.close();
      }
    };
    socket.onclose=()=>{
      clearTimeout(timeout);
      if(this.duplex!==socket)return;
      const wasReady=this.duplexReady;
      this.duplex=null;this.duplexReady=false;this.duplexConnecting=false;
      if(!wasReady)this.duplexUnsupported=true;
      if(this.disposed||id!==this.id||this.restarting)return;
      if(this.lastDuplexResize&&!this.pendingResize)this.pendingResize=this.lastDuplexResize;
      const uncertain=this.operations.some(entry=>entry.sent);
      if(wasReady&&!uncertain&&this.running&&(this.duplexReconnects||0)<3){
        this.duplexReconnects=(this.duplexReconnects||0)+1;
        this.reconnectTimer=setTimeout(()=>{this.reconnectTimer=null;this.startDuplex();},1000*this.duplexReconnects);
      }
      if(uncertain){
        this.inputError=true;this.status.textContent=BridgeI18n.t('终端连接中断 · 输入结果未确认；重试不会重复输入。');
        this.retry.hidden=false;this.send.disabled=true;this.term.options.disableStdin=true;
      }else this.flush();
      this.schedule(true);
    };
    socket.onerror=()=>{};
  }
  flushDuplex(){
    const socket=this.duplex;
    if(!this.duplexReady||!socket||this.disposed||this.restarting||this.inputError||this.flushing)return;
    const fail=()=>{try{socket.close();}catch{}socket.onclose?.();};
    if(socket.readyState!==WebSocket.OPEN){fail();return;}
    if(socket.bufferedAmount>131072){clearTimeout(this.flushTimer);this.flushTimer=setTimeout(()=>{this.flushTimer=null;this.flushDuplex();},50);return;}
    if(this.pendingResize){
      const size=this.pendingResize;this.pendingResize=null;this.lastDuplexResize=size;
      if(socket.readyState!==WebSocket.OPEN){fail();return;}
      try{socket.send(JSON.stringify({action:'resize',requestId:uuid(),...size}));}catch{fail();return;}
    }
    let inFlight=this.operations.filter(entry=>entry.sent).length;
    for(const entry of this.operations){
      if(entry.sent)continue;
      if(socket.bufferedAmount>131072||inFlight>=32)break;
      if(socket.readyState!==WebSocket.OPEN){fail();return;}
      entry.sent=true;
      try{socket.send(JSON.stringify({action:'input',inputId:entry.id,data:entry.data}));}catch{fail();return;}
      inFlight++;
    }
    if(inFlight<32&&this.operations.some(entry=>!entry.sent)){
      clearTimeout(this.flushTimer);this.flushTimer=setTimeout(()=>{this.flushTimer=null;this.flushDuplex();},50);
    }
  }
  clearQueueNotice(){
    if(this.queueFull&&this.operations.reduce((size,entry)=>size+this.inputBytes(entry.data),0)<131072){
      this.queueFull=false;
      if(!this.inputError)this.status.textContent=BridgeI18n.t('连续会话 · 关闭标签将结束终端');
    }
  }
  apply(result,rendered){
    if(result.reset)this.term.reset();
    if(result.output)this.term.write(result.output,rendered);else rendered?.();
    this.cursor=result.cursor;this.running=result.running;
    const label=BridgeI18n.t(result.running?'终端 · 已连接':'终端 · 已退出');if(this.tab.name!==label){this.tab.name=label;this.workbench.paint();}
    this.location.textContent=result.shell||BridgeI18n.t('终端');this.location.title=result.shell||'';
    if(!this.inputError&&!this.queueFull)this.status.textContent=result.running?BridgeI18n.t('连续会话 · 关闭标签将结束终端'):result.message||BridgeI18n.t('终端已退出');
    this.send.disabled=!this.running||this.inputError;this.reopen.hidden=this.running;this.layout();
  }
  schedule(error=false,idle=false){clearTimeout(this.timer);if(!this.disposed&&this.running&&!document.hidden&&!this.restarting&&!this.duplexReady&&!this.duplexConnecting)this.timer=setTimeout(()=>this.poll(),error?750:(!this.workbench.isVisible(this.session,this.tab)?500:idle?35:0));}
  async poll(){
    if(this.disposed||document.hidden||this.polling||!this.running||this.restarting||this.duplexReady||this.duplexConnecting)return;
    this.polling=true;clearTimeout(this.timer);const id=this.id,controller=this.readController=new AbortController();
    let error=false,idle=false;
    try{const result=await this.workbench.request(this.url('&id='+this.id+'&after='+this.cursor+'&waitMs=1000'),undefined,controller.signal);if(!this.disposed&&!controller.signal.aborted&&this.id===id){idle=!result.output&&result.cursor===this.cursor;this.apply(result);if(this.running&&!this.duplex&&!this.duplexUnsupported)this.startDuplex();}}
    catch(e){if(!this.disposed&&!controller.signal.aborted&&this.id===id){error=true;this.status.textContent=BridgeI18n.t(e.message);if([401,403,404].includes(e.status)){this.running=false;this.send.disabled=true;this.reopen.hidden=false;}}}
    finally{
      this.polling=false;this.readController=null;
      const resume=this.resumeRead;this.resumeRead=false;
      if(resume&&!this.disposed&&!document.hidden)this.poll();else this.schedule(error,idle);
    }
  }
  async restart(){
    if(this.restarting)return;this.restarting=true;clearTimeout(this.reconnectTimer);this.reconnectTimer=null;this.duplex?.close();this.duplex=null;this.duplexReady=false;this.duplexConnecting=false;this.duplexUnsupported=false;this.duplexReconnects=0;this.readController?.abort();clearTimeout(this.timer);clearTimeout(this.resizeTimer);this.pendingResize=null;
    try{
      // Close the previous id even after a lost open response; never orphan a shell.
      try{await this.workbench.request(this.url(),{action:'close',id:this.id});}catch(e){if(e.status!==404)throw e;}
      this.id=uuid();this.save();this.cursor=0;this.operations=[];this.inputError=false;this.retry.hidden=true;this.clearCompletion();this.term.reset();
      const result=await this.workbench.request(this.url(),{action:'open',id:this.id,cols:this.term.cols,rows:this.term.rows});if(!this.disposed){this.apply(result);this.startDuplex();this.schedule();}
    }catch(e){if(!this.disposed)this.status.textContent=BridgeI18n.t(e.message);}
    finally{this.restarting=false;this.schedule();}
  }
  dispose(){
    this.disposed=true;clearTimeout(this.reconnectTimer);this.duplex?.close();TerminalFont.unregister(this);document.removeEventListener('visibilitychange',this.visibility);this.readController?.abort();clearTimeout(this.timer);clearTimeout(this.flushTimer);clearTimeout(this.resizeTimer);this.pendingResize=null;this.observer?.disconnect();this.term?.dispose();this.fallback?.dispose();
    if(!this.fallback)this.workbench.request(this.url(),{action:'close',id:this.id}).catch(()=>{});
    try{sessionStorage.removeItem(this.key);}catch{}
  }
}
