// Synthetic UI only. Native children enter the normal session controller; they
// do not create another composer, attachment scope, or approval implementation.
async function runSideChatTests(){
 const wb=window.BridgeWorkbench,checks=[],check=(ok,label)=>{if(!ok)throw Error(label);checks.push(label);};
 const wait=async f=>{for(let i=0;i<100;i++){if(f())return;await new Promise(r=>setTimeout(r,10));}throw Error('Native side directory did not settle');};
 const originals={request:wb.request,openChat:wb.openChat,menu:wb.openSessionMenu,confirm:window.confirm,cleanup:wb.temporaryCleanup};
 const calls=[],opened=[],menus=[],parent='aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee',child='11111111-2222-3333-4444-555555555555';
 let failCreation=true,temporary=false;
 const temporaryId='22222222-3333-4444-5555-666666666666';
 try{
  wb.open(parent,'local');
  wb.openChat=(id,host)=>opened.push({id,host});
  wb.openSessionMenu=(chat)=>menus.push(chat);
  wb.request=async(url,body)=>{
   check(url.includes('/'+parent+'/side-chat?host=local&view=native'),'Directory requests retain exact parent and host');
   calls.push({url,body});
   if(body?.action==='create-temporary'){temporary=true;return{id:temporaryId,host:'local',temporary:true,parentId:parent};}
   if(body?.action==='close'){check(body.id===temporaryId,'Ending is scoped to the temporary child');temporary=false;wb.current=null;return{closed:true};}
   if(body){if(failCreation){failCreation=false;throw Error('Response lost');}return{id:child,host:'local',parentId:parent};}
   return{parentId:parent,chats:[...(temporary?[{id:temporaryId,host:'local',title:'临时侧边聊天',temporary:true}]:[]),{id:child,host:'local',title:'原生侧边聊天',attachedParentId:parent,archived:false},{id:'archived-child',host:'local',title:'已归档的侧边聊天',archived:true}]};
  };
  document.querySelector('#message').value='parent draft';
  wb.sideChat();let panel=wb.current.files.find(t=>t.id==='sidechat').sideChat;
  await wait(()=>panel.chats);
  check(panel.list.querySelectorAll('.wb-side-chat-row').length===2,'Native active and archived children remain discoverable');
  check(!panel.tab.body.querySelector('textarea,form'),'Directory does not maintain a duplicate chat stack');
  panel.list.querySelector('button').click();
  check(opened[0].id===child&&opened[0].host==='local','Selecting a child enters the standard full chat route with its native identity');
  panel.list.querySelector('[aria-label="聊天菜单"]').click();
  check(menus[0].id===child&&menus[0].attachedParentId===parent,'Child lifecycle uses the shared native session menu');
  await panel.create();await panel.create();
  const writes=calls.filter(call=>call.body);
  check(writes.length===2&&writes[0].body.creationId===writes[1].body.creationId,'Unknown create response retries the same durable operation identity');
  check(writes.every(call=>call.body.action==='create'),'No hidden send, stop, archive or delete mutation');
  check(opened.at(-1).id===child,'Creation opens the returned native child');
  const beforeClose=calls.length;
  await wb.close('sidechat');
  check(calls.length===beforeClose,'Closing the directory sends no lifecycle mutation');
  check(document.querySelector('#message').value==='parent draft','Closing the view preserves parent draft');
  wb.sideChat();panel=wb.current.files.find(t=>t.id==='sidechat').sideChat;await wait(()=>panel.chats);
  check(panel.chats[0].id===child,'Reopening discovers the same persistent child');
  await wb.close('sidechat');
  wb.open(child,'local',{rootId:parent,title:'原生侧边聊天'});
  check(wb.current.id===parent&&wb.current.chatId===child,'Native child retains the parent workspace');
  wb.tabs.querySelector('[data-tab="chat"] button').click();
  await wb.close('child:'+child);
  check(opened.slice(-2).every(row=>row.id===parent),'Main tab and closing the child return to the native parent');
  check(calls.filter(call=>call.body).length===2,'Leaving a child never stops or destroys its history');
  wb.open(parent,'local');wb.sideChat();await wait(()=>wb.current.files.find(t=>t.id==='sidechat').sideChat.chats);
  panel=wb.current.files.find(t=>t.id==='sidechat').sideChat;
  await panel.create(true);
  check(opened.at(-1).id===temporaryId,'Temporary child uses the same normal chat navigation');
  check(!!panel.list.querySelector('[aria-label="结束临时侧聊"]'),'Temporary lifecycle is explicit');
  window.confirm=()=>true;
  let cleaned=false;
  wb.temporaryCleanup=(id,host)=>{check(id===temporaryId&&host==='local','Cleanup captures original identity before close');return()=>{cleaned=true;};};
  await panel.end(panel.chats.find(chat=>chat.temporary));
  check(cleaned,'Successful close cleans the original scope even after leaving the workspace');
  wb.open(parent,'local');wb.select('sidechat');
  check(!panel.chats.some(chat=>chat.temporary),'Ending removes only the temporary entry');
  check(calls.filter(call=>call.body?.action==='close').length===1,'Ending is submitted exactly once');
  return{checks,passed:checks.length};
 }finally{wb.request=originals.request;wb.openChat=originals.openChat;wb.openSessionMenu=originals.menu;window.confirm=originals.confirm;wb.temporaryCleanup=originals.cleanup;}
}
