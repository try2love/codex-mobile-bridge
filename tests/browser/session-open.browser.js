// Run in an isolated gateway browser page; all requests use synthetic fixtures.
async function runSessionOpenTests(){
  const checks=[],originalFetch=window.fetch;
  const check=(value,label)=>{if(!value)throw Error(label);checks.push(label);};
  const until=async fn=>{const end=performance.now()+4500;while(!fn()){if(performance.now()>end)throw Error('Timed out waiting for fixture');await new Promise(resolve=>setTimeout(resolve,20));}};
  const first='11111111-1111-4111-8111-111111111111',second='22222222-2222-4222-8222-222222222222';
  $('login').hidden=true;$('app').hidden=false;
  const calls=[],activations=[];let failInitial=false,reads=0;
  const response=(body,status=200)=>({ok:status===200,status,json:async()=>body});
  window.fetch=async(url,options)=>{
    const path=new URL(url,location.origin),id=path.pathname.split('/')[3];calls.push({path,options});
    if(path.pathname.endsWith('/reconnect'))return new Promise(resolve=>activations.push({id,resolve,options}));
    if(path.pathname.endsWith('/changes'))return new Promise((resolve,reject)=>options.signal.addEventListener('abort',()=>reject(new DOMException('aborted','AbortError')),{once:true}));
    if(path.pathname.endsWith('/notifications'))return response({available:false});
    if(path.pathname.endsWith('/timeline')){
      reads++;
      if(failInitial){failInitial=false;return response({error:'Temporary database unavailable'},409);}
      return response({sequence:1,epoch:id,before:id+'.k0',hasMore:false,files:[],
        meta:{id,title:id,host:'local',requests:[],submissions:[],connected:false,loadingHistory:false,historyComplete:true,status:'idle'},
        rows:Array.from({length:20},(_,i)=>({key:'k'+i,order:i,role:'assistant',version:'v1',text:'Saved message '+i}))});
    }
    throw Error('Unexpected request: '+url);
  };
  try{
    await openChat(first);
    await until(()=>activations.length===1);
    check(activations[0].id===first&&JSON.parse(activations[0].options.body).activate===true,'Opening a chat automatically requests owner activation');
    check($('messages').children.length===20,'First 20 saved messages render while activation is pending');
    check(calls.every(c=>!c.path.pathname.endsWith('/send')),'Opening a chat sends no model message');
    await openChat(second,'remote:test');await until(()=>activations.length===2);
    check(activations[0].options.signal.aborted,'Switching chat aborts the old activation request');
    check(calls.find(c=>c.path.pathname.includes(second)&&c.path.pathname.endsWith('/reconnect')).path.searchParams.get('host')==='remote:test','Auto activation keeps the selected SSH host');
    $('toast').hidden=true;
    activations[0].resolve(response({error:'Old chat failed'},409));await new Promise(resolve=>setTimeout(resolve,20));
    check($('chat-title').textContent===second&&$('toast').hidden,'Late activation error cannot overwrite the selected chat or show a stale toast');
    activations[1].resolve(response({ok:true,connected:true}));
    failInitial=true;const before=reads;
    await openChat(first);await until(()=>activations.length===3);
    activations[2].resolve(response({ok:true,connected:true}));
    await until(()=>reads>=before+2&&$('messages').children.length===20);
    check(currentId===first,'A transient first read failure recovers automatically without a new chat');
    check(activations.length===3,'History retries do not repeat desktop activation');
    return {passed:checks.length,checks};
  }finally{chatTimeline?.dispose();chatTimeline=null;window.fetch=originalFetch;}
}
