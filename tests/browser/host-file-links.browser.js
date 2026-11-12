// Run on the isolated production GatewayServer used by host-file-navigation.
window.runHostFileLinkChecks=async({files,text})=>{
 await BridgeFeatures.load('chat');
 const check=(ok,label)=>{if(!ok)throw Error(label);checks.push(label);},checks=[],calls=[];
 const chat=document.createElement('section');chat.className='chat';document.body.append(chat);
 const session={id:'11111111-1111-4111-8111-111111111111',host:'local'};
 const request=async url=>{calls.push(url);const response=await fetch(url),value=await response.json();if(!response.ok)throw Error(value.error);return value;};
 let located,previewed;
 const wb={current:session,request,size:n=>String(n),notify:()=>{},endpoint:Workbench.prototype.endpoint,url:Workbench.prototype.url,
  locate:async(s,path)=>{located=path;},preview:async(s,info)=>{previewed=await request(wb.url(s,'preview',info.path));}};
 BridgeFileActions.register(chat,wb);
 const timeline=Object.assign(Object.create(ChatTimeline.prototype),{url:action=>wb.endpoint(session,action)});
 const wait=async predicate=>{const end=Date.now()+3000;while(!predicate()){if(Date.now()>end)throw Error('File action timed out');await new Promise(r=>setTimeout(r,20));}};
 const button=text=>[...document.querySelectorAll('dialog[open] button')].find(n=>n.textContent===text);
 try{
  renderMarkdown(chat,text,files,file=>timeline.fileUrl(file));
  check(chat.querySelectorAll('a').length===2&&!chat.querySelector('img'),'outside file and image are explicit links without automatic image requests');
  check(calls.length===0,'rendering does not read file metadata');
  const anchor=chat.querySelector('a');anchor.click();await wait(()=>button('预览文件'));
  check(calls.length===1&&new URL(calls[0],location.href).searchParams.get('scope')==='host','click requests scoped metadata on the selected host');
  check(!!document.querySelector('dialog[open] a[download]')&&!!button('定位并打开所在文件夹'),'original download and locate actions remain available');
  button('预览文件').click();await wait(()=>previewed);
  check(previewed.text==='outside preview','actual sibling file is previewed through the shared action');
  anchor.click();await wait(()=>button('定位并打开所在文件夹'));button('定位并打开所在文件夹').click();await wait(()=>located);
  check(located===files[0].path,'locate receives the exact absolute target');
  return checks;
 }finally{chat.remove();document.querySelectorAll('dialog[open]').forEach(d=>d.close());}
};
