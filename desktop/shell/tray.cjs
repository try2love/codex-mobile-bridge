'use strict';

function primaryUrl(urls=[]){
  const usable=urls.filter(url=>{
    try{const parsed=new URL(url);return ['http:','https:'].includes(parsed.protocol)&&!parsed.username&&!parsed.password;}
    catch{return false;}
  });
  return usable.find(url=>url.startsWith('https:'))||usable.find(url=>!['127.0.0.1','localhost','[::1]'].includes(new URL(url).hostname))||usable[0];
}

function createTray({Tray,Menu,icon,show,worker,open,copy,quit,onError,t=text=>text,setTimer=setInterval,clearTimer=clearInterval}){
  const tray=new Tray(icon);
  let snapshot,busy=false,refreshing=false,disposed=false;
  async function action(callback){
    if(busy||disposed)return;
    busy=true;render();
    try{await callback();}
    catch(error){onError(error);}
    finally{busy=false;await refresh();}
  }
  function render(){
    if(disposed)return;
    const running=Boolean(snapshot?.runtime.running);
    const url=running?primaryUrl(snapshot.urls):null;
    const status=!snapshot?'正在读取状态':running?'网关运行中':snapshot.runtime.portOccupied?'端口已占用':'网关未启动';
    tray.setToolTip(t('Codex 手机网关')+' · '+t(status));
    tray.setContextMenu(Menu.buildFromTemplate([
      {label:'打开控制面板',click:show},
      {label:status,enabled:false},
      {type:'separator'},
      {label:'打开手机访问地址',enabled:Boolean(url)&&!busy,click:()=>action(()=>open(url))},
      {label:'复制手机访问地址',enabled:Boolean(url)&&!busy,click:()=>action(()=>copy(url))},
      {type:'separator'},
      {label:'停止网关并退出',enabled:running&&!busy,click:()=>action(async()=>{await worker('stop');quit();})},
      {label:'退出控制面板（保留网关）',enabled:!busy,click:quit},
    ].map(item=>item.label?{...item,label:t(item.label)}:item)));
  }
  async function refresh(){
    if(refreshing||disposed)return;
    refreshing=true;
    try{snapshot=await worker('snapshot');}
    catch{snapshot=undefined;}
    finally{refreshing=false;render();}
  }
  tray.on('double-click',show);
  render();
  const timer=setTimer(refresh,5000);
  refresh();
  return {refresh,relabel:render,dispose(){disposed=true;clearTimer(timer);tray.destroy();}};
}

module.exports={createTray,primaryUrl};
