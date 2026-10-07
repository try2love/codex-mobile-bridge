'use strict';

class CommandTerminalPanel {
  constructor(workbench, session, tab) {
    Object.assign(this, {workbench, session, tab}); this.disposed=false; this.cursor=0; this.output='';
    this.key='terminal:'+session.key;
    try { this.id=sessionStorage.getItem(this.key); } catch {}
    const n=(...args)=>workbench.node(...args), b=(...args)=>workbench.button(...args);
    const head=n('div','wb-terminal-head'); head.append(n('strong','','终端'));
    this.stop=b('停止',()=>this.cancel());this.stop.disabled=true;
    head.append(b('清屏',()=>{this.output='';this.screen.textContent='';}),this.stop);
    this.location=n('p','wb-terminal-location','正在读取项目…');
    this.status=n('p','wb-status');this.status.setAttribute('role','status');
    this.screen=n('pre','wb-terminal-output');this.screen.tabIndex=0;this.screen.setAttribute('aria-label','命令输出');
    this.form=n('form','wb-terminal-form');this.command=n('textarea');this.command.rows=1;this.command.placeholder='输入命令…';this.command.setAttribute('aria-label','终端命令');this.command.spellcheck=false;
    this.command.setAttribute('autocapitalize','off');this.command.setAttribute('autocorrect','off');
    this.prompt=n('span','wb-terminal-prompt','$');this.prompt.setAttribute('aria-hidden','true');
    this.run=n('button','','运行 ↵');this.run.type='submit';this.run.disabled=true;
    this.form.append(this.prompt,this.command,this.run);this.form.onsubmit=e=>{e.preventDefault();this.start();};
    this.command.onkeydown=e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing&&!document.documentElement.classList.contains('bridge-mobile')&&innerWidth>720){e.preventDefault();this.form.requestSubmit();}};
    const help=n('details','wb-terminal-help');help.append(n('summary','','命令终端 · 使用说明'),n('p','','每条命令从项目目录运行，沿用当前系统用户权限。Enter 运行，Shift+Enter 换行。支持脚本与构建输出，暂不支持 vim、密码输入等交互式程序。单次最多运行 30 分钟。'));
    tab.body.append(head,this.location,this.screen,this.form,this.status,help);
    this.load();
  }
  url(extra='') {return '/api/sessions/'+this.session.id+'/terminal?host='+encodeURIComponent(this.session.host)+extra;}
  async load() {
    try {
      const info=await this.workbench.request(this.url());if(this.disposed)return;
      this.location.textContent=(info.host==='local'?'此电脑':info.host)+' · '+info.cwd+' · '+info.shell;
      this.cwd=info.cwd;this.run.disabled=false;
      this.prompt.textContent=/cmd(?:\.exe)?$/i.test(info.shell)?'>':'$';
      if(this.id){this.running=true;this.run.disabled=true;await this.poll();}
    } catch(e){if(!this.disposed)this.status.textContent=e.message;}
  }
  remember(){try{if(this.id)sessionStorage.setItem(this.key,this.id);else sessionStorage.removeItem(this.key);}catch{}}
  async start() {
    const command=this.command.value.trim();if(!command||this.running||this.run.disabled)return;
    this.running=true;this.run.disabled=true;this.stop.disabled=true;this.id=uuid();this.remember();this.cursor=0;
    this.output='';this.commandText=this.prompt.textContent+' '+command+'\n';this.screen.textContent=this.commandText;this.status.textContent='正在启动…';
    try {
      const result=await this.workbench.request(this.url(),{action:'start',id:this.id,command});
      if(this.disposed)return;this.command.value='';this.apply(result);if(this.running)this.schedule();
    } catch(e){if(!this.disposed){this.status.textContent=e.message+' · 正在查询执行状态，不会重复执行。';this.schedule();}}
  }
  apply(result) {
    const follow=this.screen.scrollHeight-this.screen.clientHeight-this.screen.scrollTop<48;
    // Render output as text; strip terminal escape sequences, including OSC links.
    const text=result.output.replace(/\x1b\][^\x07]*(?:\x07|\x1b\\)/g,'').replace(/\x1b\[[0-?]*[ -/]*[@-~]/g,'');
    this.output=((result.reset?'':this.output)+text).slice(-262144);this.cursor=result.cursor;
    this.screen.textContent=(this.commandText||'')+this.output;if(follow)this.screen.scrollTop=this.screen.scrollHeight;
    this.running=result.running;this.run.disabled=this.running;this.stop.disabled=!this.running;
    this.status.textContent=(result.running?'运行中':result.message||'已结束 · 退出码 '+result.exitCode)+(result.truncated?' · 仅保留最近输出':'');
  }
  schedule(){clearTimeout(this.timer);if(!this.disposed)this.timer=setTimeout(()=>this.poll(),document.hidden?5000:900);}
  async poll() {
    try {
      const result=await this.workbench.request(this.url('&id='+this.id+'&after='+this.cursor));
      if(this.disposed)return;this.apply(result);if(this.running)this.schedule();
    } catch(e){
      if(this.disposed)return;
      this.status.textContent=e.message;
      if(e.status===404){this.running=false;this.run.disabled=false;this.stop.disabled=true;this.id=null;this.remember();}
      else this.schedule();
    }
  }
  async cancel() {
    if(!this.id)return;this.stop.disabled=true;
    try {const result=await this.workbench.request(this.url(),{action:'stop',id:this.id});if(!this.disposed){this.apply({...result,output:'',cursor:this.cursor});this.schedule();}}
    catch(e){if(!this.disposed){this.status.textContent=e.message;this.stop.disabled=false;}}
  }
  dispose(){this.disposed=true;clearTimeout(this.timer);}
}
