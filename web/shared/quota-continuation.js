'use strict';
// One controller for browser, mobile WebView, and the desktop overview.
class QuotaContinuationToggle {
  constructor({input,error,status,read,write,visible=()=>true}){
    Object.assign(this,{input,error,status,read,write,visible});
    this.busy=false;this.reading=false;this.revision=0;
    input.onchange=()=>this.change();
  }
  paint(value){
    this.input.checked=!!value.enabled;this.error.textContent=value.error||'';
    if(!this.status)return;
    const t=typeof BridgeI18n==='undefined'?text=>text:BridgeI18n.t;
    const labels={disabled:'自动继续已关闭',waitingGateway:'等待网关启动',monitoring:'正在监测额度中断的任务',
      waitingConnection:'等待连接 Codex 聊天，请确认 Codex App 正在运行',waitingQuota:'已登记任务，等待额度可用',
      unknown:'继续结果待确认，请查看原聊天',error:'检查暂不可用，将自动重试'};
    let text=t(labels[value.state||(value.enabled?'monitoring':'disabled')]||'正在监测额度中断的任务');
    text+=' · '+t('待继续')+' '+(value.pending||0)+' '+t('个');
    text+=' · '+t('已继续')+' '+(value.resumed||0)+' '+t('个');
    text+=' · '+t('结果待确认')+' '+(value.unknown||0)+' '+t('个');
    if(value.checkedAt)text+=' · '+t('上次检查')+' '+new Date(value.checkedAt*1000).toLocaleTimeString();
    this.status.textContent=text;
  }
  async refresh(){
    if(this.busy||this.reading||!this.visible())return;
    this.reading=true;const revision=this.revision;
    try{
      const value=await this.read();
      if(revision!==this.revision||!this.visible())return;
      this.paint(value);this.input.disabled=false;
    }catch(error){
      if(revision===this.revision&&this.visible()){this.input.disabled=true;this.error.textContent=error.message;}
    }finally{this.reading=false;}
  }
  async change(){
    if(this.busy)return;
    const enabled=this.input.checked;this.revision++;this.busy=true;this.input.disabled=true;
    try{
      const value=await this.write(enabled);
      this.paint(value);
    }catch(error){this.input.checked=!enabled;this.error.textContent=error.message;}
    finally{this.busy=false;this.input.disabled=false;await this.refresh();}
  }
}
if(typeof module!=='undefined')module.exports={QuotaContinuationToggle};
