'use strict';
// In-memory stores keep the public demo separate from every real gateway session.
for(const key of ['localStorage','sessionStorage']){
 const data=new Map();Object.defineProperty(window,key,{value:{getItem:k=>data.get(String(k))??null,setItem:(k,v)=>data.set(String(k),String(v)),removeItem:k=>data.delete(String(k)),clear:()=>data.clear()}});
}
localStorage.setItem('bridge-language',new URLSearchParams(location.search).get('lang')==='en'?'en':'zh');
localStorage.setItem('bridge-appearance',JSON.stringify({theme:'light'}));
window.demoText=(zh,en)=>localStorage.getItem('bridge-language')==='en'?en:zh;
window.demoNotice=()=>alert(demoText('这是示例数据演示，此操作不会连接电脑或修改真实配置。','This sample-data demo does not connect to a computer or change real settings.'));
// Fail closed: unknown API calls must never reach the network.
window.fetch=async()=>new Response(JSON.stringify({error:demoText('此操作未在演示中开放。','This operation is not available in the demo.')}),{status:400});
window.EventSource=class{close(){}};
XMLHttpRequest.prototype.open=function(){throw Error(demoText('演示不会上传真实文件。','The demo does not upload real files.'));};
document.addEventListener('click',event=>{const a=event.target.closest('a');if(!a||!a.href||a.getAttribute('href')==='codexbridge://home')return;event.preventDefault();demoNotice();},true);
