// Run only in the loopback bootstrap fixture. No real client/API is accessed.
async function runDesktopHistoryBrowserChecks(){
  const checks=[],calls=[],notices=[];
  const check=(value,label)=>{if(!value)throw Error(label);checks.push(label);};
  const until=async predicate=>{const end=performance.now()+3000;while(!predicate()){
    if(performance.now()>end)throw Error('History fixture timed out');
    await new Promise(resolve=>setTimeout(resolve,10));
  }};
  document.getElementById('login').hidden=true;
  const root=document.createElement('div');root.className='app';
  root.style.cssText='position:fixed;inset:0;background:var(--bg);z-index:10';
  document.body.append(root);
  const select=document.createElement('select');
  for(const name of ['codex','claude','deepseek'])select.add(new Option(name,name));
  let finishOlder,failDetail=false;
  const frame=()=>new Promise(requestAnimationFrame);
  const message=i=>({id:String(i),role:'assistant',text:'History row '+i+'\n\n'+'Long visible text. '.repeat(30)});
  const recent={session:{id:'fixture',title:'History fixture',status:'active',requests:[],capabilities:{send:true}},
    connected:true,messages:Array.from({length:40},(_,i)=>message(40+i)),history:{hasMore:true,before:'older-fixture'},files:[]};
  const view=new DesktopSessionsView({root,select,notify:text=>notices.push(text),onSelect(){},csrf:()=>'',onUnauthorized(){},
    request:async(url,body)=>{
      calls.push({url,body});if(body)throw Error('Unexpected control request');
      if(url.includes('historyBefore='))return new Promise(resolve=>finishOlder=resolve);
      if(url.includes('/detail')){if(failDetail)throw Error('找不到此本机 ZCode 聊天');return {...recent,session:{...recent.session,id:new URL(url,location.href).searchParams.get('sessionId')}};}
      if(url.includes('/workspace'))return {entries:[],total:0,project:'/fixture'};
      return {sessions:[{id:'fixture',title:'History fixture',backend:'code'}]};
    }});
  try{
    await view.choose('claude');await view.open('fixture');clearTimeout(view.timer);
    check(view.lastMessages.length===40,'Recent page renders before any older read');
    view.input.value='Unsent draft';
    view.messages.scrollTop=0;
    const anchor=view.messages.querySelector('.message');
    const top=anchor.getBoundingClientRect().top;
    view.messages.querySelector('.ds-history-older').click();
    await until(()=>finishOlder);
    check(view.messages.querySelector('.ds-history-older').disabled,'Older page has a single in-flight request');
    check(view.state.status==='active'&&view.input.value==='Unsent draft','Pending history preserves state and draft');
    finishOlder({session:{id:'fixture',status:'idle',requests:[{id:'expired'}]},
      messages:Array.from({length:40},(_,i)=>message(i)),history:{hasMore:false,before:null},files:[{id:'old-file',path:'old.md',reference:'old.md'}]});
    await until(()=>!view.historyBusy);
    check(view.lastMessages.length===80,'Older messages prepend without duplicates');
    check(Math.abs(view.messageNodes.get('40').node.getBoundingClientRect().top-top)<3,'Prepending preserves the visible message position');
    check(view.state.status==='active'&&view.state.requests.length===0,'Old page cannot replace live state or approvals');
    check(view.sessionFiles.some(file=>file.id==='old-file'),'Old page file references remain available');
    view.applyDetail({...recent,messages:[...recent.messages,message(80)]});
    check(view.lastMessages.length===81&&view.lastMessages[0].id==='0','Recent refresh retains loaded older messages');
    check(view.input.value==='Unsent draft','History refresh does not overwrite draft');
    check(calls.every(call=>!call.body),'All fixture requests are read-only');
    check(notices.length===0,'No pagination errors');
    view.backToList();await view.choose('zcode');clearTimeout(view.timer);
    view.cacheDetail(JSON.stringify(['zcode','new']),{...recent,session:{...recent.session,id:'new'}});
    await view.open('new');await frame();await frame();
    const bottomGap=()=>view.messages.scrollHeight-view.messages.clientHeight-view.messages.scrollTop;
    check(view.messages.clientHeight>0&&view.messages.scrollHeight>view.messages.clientHeight*2,'Narrow or wide history really overflows');
    check(bottomGap()<3,'An unread cached chat starts at the latest message');
    const expandImage=async(id,height)=>{
      const image=document.createElement('img');
      image.style.display='block';view.messageNodes.get(id).node.append(image);
      image.src='data:image/svg+xml,'+encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="8" height="'+height+'"/>');
      await image.decode();await frame();await frame();
    };
    await expandImage('79',360);
    check(bottomGap()<3,'Late attachment layout keeps the latest message in view');
    view.messages.scrollTop-=420;await frame();await frame();
    const beforeResize=view.messageNodes.get('77').node.getBoundingClientRect().top;
    await expandImage('40',260);
    check(Math.abs(view.messageNodes.get('77').node.getBoundingClientRect().top-beforeResize)<3,'An image loading above the reader preserves the visible message');
    view.backToList();failDetail=true;await view.open('unavailable');
    check(!view.messages.textContent.includes('正在读取聊天')&&!view.title.textContent.includes('正在读取聊天'),'A failed read clears the loading placeholder');
    check(view.notice.textContent.includes('找不到此本机 ZCode 聊天')&&view.send.disabled,'Read failure is shown at the top, with writes disabled');
    failDetail=false;view.notice.querySelector('button').click();await until(()=>view.freshDetail);
    check(!view.notice.textContent.includes('找不到此本机 ZCode 聊天'),'Successful retry clears the read error');
    return {width:innerWidth,passed:checks.length,checks};
  }finally{view.clear();root.remove();}
}
