'use strict';

class TerminalPanel {
  constructor(workbench, session, tab) {
    Object.assign(this, {workbench, session, tab});this.disposed=false;this.cursor=0;this.running=false;this.operations=[];
    this.key='pty:'+session.key;
    try{this.id=sessionStorage.getItem(this.key);}catch{}
    this.hadId=!!this.id;this.id ||= uuid();this.save();
    this.load();
  }
  save(){try{sessionStorage.setItem(this.key,this.id);}catch{}}
  url(extra=''){return '/api/sessions/'+this.session.id+'/terminal?host='+encodeURIComponent(this.session.host)+'&mode=pty'+extra;}
  async load(){
    try{
      const info=await this.workbench.request(this.url());if(this.disposed)return;
      if(info.mode==='command'){
        this.fallback=new CommandTerminalPanel(this.workbench,this.session,this.tab);return;
      }
      this.render(info);
      const result=this.hadId?await this.workbench.request(this.url('&id='+this.id+'&after=0')):
        await this.workbench.request(this.url(),{action:'open',id:this.id,cols:this.term.cols,rows:this.term.rows});
      if(this.disposed){if(!this.hadId)this.workbench.request(this.url(),{action:'close',id:this.id}).catch(()=>{});return;}
      this.apply(result);this.schedule();
    }catch(e){if(!this.disposed){if(!this.status)this.render({});this.status.textContent=BridgeI18n.t(e.message);this.reopen.hidden=false;}}
  }
  render(info){
    const n=(...a)=>this.workbench.node(...a),b=(...a)=>this.workbench.button(...a);
    this.tab.body.replaceChildren();this.tab.body.classList.add('wb-pty');
    const head=n('div','wb-terminal-head');this.location=n('strong','',info.shell||BridgeI18n.t('连接终端…'));
    this.reopen=b(BridgeI18n.t('重新打开'),()=>this.restart());this.reopen.hidden=true;
    head.append(this.location,b(BridgeI18n.t('清屏'),()=>this.term.clear()),this.reopen);
    this.screen=n('div','wb-terminal-screen');this.screen.setAttribute("data-i18n-aria-label",'交互终端');this.screen.setAttribute('aria-label',BridgeI18n.t('交互终端'));
    this.status=n('p','wb-status');this.status.setAttribute('role','status');
    const keys=n('div','wb-terminal-keys');
    for(const [label,value] of [['Ctrl+C','\x03'],['Tab','\t'],['Esc','\x1b'],['↑','\x1b[A'],['↓','\x1b[B'],['←','\x1b[D'],['→','\x1b[C']])keys.append(b(label,()=>this.enqueue(value)));
    this.form=n('form','wb-terminal-input');this.input=n('textarea');this.input.rows=2;this.input.setAttribute("data-i18n-placeholder",'输入命令或回复，换行键可换行');this.input.placeholder=BridgeI18n.t('输入命令或回复，换行键可换行');this.input.setAttribute("data-i18n-aria-label",'终端输入');this.input.setAttribute('aria-label',BridgeI18n.t('终端输入'));this.input.setAttribute('autocapitalize','off');this.input.setAttribute('autocorrect','off');this.input.spellcheck=false;
    this.send=n('button','',BridgeI18n.t('发送到终端'));this.send.type='submit';this.send.disabled=true;
    this.form.append(this.input,this.send);this.form.onsubmit=e=>{e.preventDefault();this.submit();};
    // Enter in the mobile composer ALWAYS inserts a newline. Submit is explicit.
    this.retry=b(BridgeI18n.t('重试未确认输入'),()=>{this.inputError=false;this.retry.hidden=true;this.flush();});this.retry.hidden=true;
    this.tab.body.append(head,this.screen,keys,this.form,this.status,this.retry);
    this.term=new Terminal({fontSize:14,fontFamily:'Menlo, Consolas, monospace',cursorBlink:true,scrollback:2000,
      theme:{background:'#11151b',foreground:'#e2e8f0',cursor:'#7ee0b6'},allowProposedApi:false,
      linkHandler:{activate:()=>{}},disableStdin:true});
    // Terminal output must never write to or read the system clipboard (OSC 52).
    this.term.parser.registerOscHandler(52,()=>true);
    this.fit=new FitAddon.FitAddon();this.term.loadAddon(this.fit);this.term.open(this.screen);
    this.term.onData(data=>this.enqueue(data));
    this.term.onResize(({cols,rows})=>{clearTimeout(this.resizeTimer);this.resizeTimer=setTimeout(()=>{if(this.running&&!this.disposed)this.workbench.request(this.url(),{action:'resize',id:this.id,cols,rows}).catch(e=>this.status.textContent=BridgeI18n.t(e.message));},120);});
    this.observer=new ResizeObserver(()=>this.layout());this.observer.observe(this.screen);this.layout();
  }
  mobile(){return document.documentElement.classList.contains('bridge-mobile')||matchMedia('(pointer:coarse)').matches||matchMedia('(max-width:720px)').matches;}
  layout(){
    if(this.disposed||!this.screen?.clientHeight||!this.screen.clientWidth)return;
    this.fit.fit();this.tab.body.classList.toggle('wb-terminal-touch',this.mobile());this.term.options.disableStdin=!this.running||this.mobile()||this.inputError;
  }
  async submit(){
    if(!this.running||this.send.disabled)return;
    const text=this.input.value;
    // xterm honours bracketed-paste mode set by the active shell/application.
    if(text){this.term.options.disableStdin=false;this.term.paste(text);this.layout();}
    this.enqueue('\r');this.input.value='';this.input.focus();
  }
  enqueue(data){
    if(!this.running||this.disposed)return;
    // Keep each Unicode character intact while staying below the server input cap.
    let chunk='';for(const char of data){if(chunk.length+char.length>3000){this.operations.push({id:uuid(),data:chunk});chunk='';}chunk+=char;}
    if(chunk)this.operations.push({id:uuid(),data:chunk});this.flush();
  }
  async flush(){
    if(this.flushing||this.inputError||this.disposed)return;
    this.flushing=true;
    try{
      while(this.operations.length&&!this.disposed){
        const entry=this.operations[0];
        await this.workbench.request(this.url(),{action:'input',id:this.id,inputId:entry.id,data:entry.data});
        this.operations.shift();
      }
    }catch(e){if(!this.disposed){this.inputError=true;this.status.textContent=BridgeI18n.t(e.message)+BridgeI18n.t(' · 输入结果未确认；重试不会重复输入。');this.retry.hidden=false;this.send.disabled=true;this.term.options.disableStdin=true;}}
    finally{this.flushing=false;if(!this.disposed&&!this.inputError){this.send.disabled=!this.running;this.layout();}}
  }
  apply(result){
    if(result.reset)this.term.reset();
    if(result.output)this.term.write(result.output);
    this.cursor=result.cursor;this.running=result.running;
    this.location.textContent=result.shell||BridgeI18n.t('终端');this.location.title=result.cwd||'';
    if(!this.inputError)this.status.textContent=result.running?BridgeI18n.t('连续会话 · 关闭标签将结束终端'):result.message||BridgeI18n.t('终端已退出');
    this.send.disabled=!this.running||this.inputError;this.reopen.hidden=this.running;this.layout();
  }
  schedule(){clearTimeout(this.timer);if(!this.disposed&&this.running)this.timer=setTimeout(()=>this.poll(),document.hidden||!this.workbench.isVisible(this.session,this.tab)?2000:150);}
  async poll(){
    try{const result=await this.workbench.request(this.url('&id='+this.id+'&after='+this.cursor));if(!this.disposed)this.apply(result);}
    catch(e){if(!this.disposed){this.status.textContent=BridgeI18n.t(e.message);if([401,403,404].includes(e.status)){this.running=false;this.send.disabled=true;this.reopen.hidden=false;}}}
    this.schedule();
  }
  async restart(){
    if(this.restarting)return;this.restarting=true;
    try{
      // Close the previous id even after a lost open response; never orphan a shell.
      try{await this.workbench.request(this.url(),{action:'close',id:this.id});}catch(e){if(e.status!==404)throw e;}
      this.id=uuid();this.save();this.cursor=0;this.operations=[];this.inputError=false;this.retry.hidden=true;this.term.reset();
      const result=await this.workbench.request(this.url(),{action:'open',id:this.id,cols:this.term.cols,rows:this.term.rows});if(!this.disposed){this.apply(result);this.schedule();}
    }catch(e){if(!this.disposed)this.status.textContent=BridgeI18n.t(e.message);}
    finally{this.restarting=false;}
  }
  dispose(){
    this.disposed=true;clearTimeout(this.timer);clearTimeout(this.resizeTimer);this.observer?.disconnect();this.term?.dispose();this.fallback?.dispose();
    if(!this.fallback)this.workbench.request(this.url(),{action:'close',id:this.id}).catch(()=>{});
    try{sessionStorage.removeItem(this.key);}catch{}
  }
}
