'use strict';
const ClientLifecycle=(()=>{
  let sequence=0;
  function chooseDisable(client){
    return new Promise(resolve=>{
      const dialog=document.createElement('dialog');dialog.className='client-disable-dialog';
      const node=(tag,text)=>{const element=document.createElement(tag);element.dataset.i18n=text;element.textContent=BridgeI18n.t(text);return element;};
      const title=node('h2','停用手机接入'),description=node('p','选择是否同时退出电脑上的应用。');
      const identity=document.createElement('p');identity.textContent=client.name||client.id;
      const id='client-disable-'+(++sequence);title.id=id+'-title';description.id=id+'-description';
      dialog.setAttribute('aria-labelledby',title.id);dialog.setAttribute('aria-describedby',description.id);
      const actions=document.createElement('div');actions.className='client-disable-options';let choice=null;
      const finish=value=>{choice=value;dialog.close();};
      for(const [label,note,value] of [
        ['仅停用手机接入','保留电脑 App 和现有任务。',false],
        ['同时退出电脑 App','仅在所有任务结束且没有待确认操作时退出。',true]
      ]){
        const button=document.createElement('button');button.type='button';button.dataset.quitDesktop=String(value);
        button.append(node('strong',label),node('small',note));button.onclick=()=>finish(value);actions.append(button);
      }
      const cancel=node('button','取消');cancel.type='button';cancel.onclick=()=>finish(null);actions.append(cancel);
      dialog.append(title,identity,description,actions);
      dialog.addEventListener('cancel',event=>{event.preventDefault();finish(null);});
      dialog.addEventListener('close',()=>{dialog.remove();resolve(choice);},{once:true});
      document.body.append(dialog);dialog.showModal();cancel.focus();
    });
  }
  return {chooseDisable};
})();
if(typeof module!=='undefined')module.exports=ClientLifecycle;
