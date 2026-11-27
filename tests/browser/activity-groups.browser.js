// Production renderers with synthetic native rows; no real client or model call.
async function runActivityGroupBrowserChecks(){
  await BridgeFeatures.load('chat');
  const checks=[],frame=()=>new Promise(requestAnimationFrame),check=(value,label)=>{if(!value)throw Error(label);checks.push(label);};
  const appearance={reasoning:document.documentElement.dataset.showReasoning,process:document.documentElement.dataset.showProcess};
  document.documentElement.dataset.showReasoning='true';document.documentElement.dataset.showProcess='true';
  try{for(const provider of ['codex','claude','deepseek','zcode','antigravity']){
    const root=document.createElement('section');root.className='app';root.style.cssText='position:fixed;inset:0;z-index:40;background:var(--bg)';document.body.append(root);
    const row=(id,order,role,text,more={})=>({id,key:id,order,role,text,version:'1',turnId:'turn',turnStatus:'inProgress',...more});
    let messages=[row('input',0,'user','Initial request'),row('reason',1,'reasoning','Reasoning summary'),
      row('command',2,'activity','echo fixture',{kind:'commandExecution',title:'Command'}),
      row('progress',3,'assistant','Progress explanation',{phase:'commentary'}),
      row('supplement',4,'user','Additional instruction'),row('tool',5,'activity','Native tool result',{title:'Tool'}),
      row('final',6,'assistant','Final answer',{phase:'final_answer'})];
    let timeline,view,container,viewport,sequence=0,reads=0,apply;
    const detail=()=>({connected:true,session:{id:'flat',title:'Flat native chat',status:'active',requests:[]},messages,files:[],history:{hasMore:false}});
    try{
      if(provider==='codex'){
        viewport=document.createElement('div');viewport.className='timeline';viewport.style.cssText='height:100%;overflow:auto';container=document.createElement('div');viewport.append(container);
        const older=document.createElement('button'),newer=document.createElement('button');root.append(viewport,older,newer);
        timeline=new ChatTimeline({elements:{viewport,container,older,newer},url:action=>'/synthetic/'+action+'?',renderMeta(){},renderText:(node,text)=>node.textContent=text,status(){},request:async url=>{
          reads++;const key=new URL(url,location.href).searchParams.get('key')?.split('.').at(-1),item=messages.find(row=>row.id===key);
          return {key,version:'1',text:item?.text||'',offset:0,next:null};
        }});
        apply=(cached=false)=>timeline.apply({epoch:'flat',sequence:++sequence,rows:messages.map(message=>({...message,role:message.role==='reasoning'?'activity':message.role,kind:message.kind||message.role})),files:[],meta:{status:'active',requests:[]},hasMore:false},sequence===1,false,cached);
      }else{
        const select=document.createElement('select');for(const id of ['codex',provider])select.add(new Option(id,id));
        view=new DesktopSessionsView({root,select,notify(){},onSelect(){},csrf:()=>'',onUnauthorized(){},request:async(url,body)=>{
          if(body)throw Error('Unexpected native write');reads++;if(url.includes('/detail'))return detail();if(url.includes('/workspace'))return {entries:[],total:0,project:'/synthetic'};return {sessions:[detail().session]};
        }});await view.choose(provider);await view.open('flat');clearTimeout(view.timer);container=view.messageList;viewport=view.messages;
        apply=(cached=false)=>view.applyDetail(detail(),cached);
      }
      const node=id=>container.querySelector('[data-key="'+id+'"]');apply();await frame();await frame();
      check(!container.querySelector('.chat-activity-group,.chat-process-page'),provider+' has no turn or batch disclosure');
      check(messages.every(message=>node(message.id)?.parentElement===container),provider+' records remain direct chronological siblings');
      check([...container.children].filter(node=>node.dataset.key&&!node.classList.contains('chat-turn-completion')).map(node=>node.dataset.key).join('|')===messages.map(row=>row.id).join('|'),provider+' ordering retains progress and user supplement');
      check(node('progress').getBoundingClientRect().height>0&&node('final').getBoundingClientRect().height>0,provider+' progress and final reply stay visible');
      const reasoning=node('reason');
      check(reasoning.matches('details.activity:not([open])')&&/思考摘要|Thinking summary/i.test(reasoning.querySelector('summary').textContent),
        provider+' reasoning is an independent collapsed summary');
      reasoning.querySelector('summary').click();
      await frame();
      check(reasoning.open&&reasoning.querySelector('.activity-body').textContent==='Reasoning summary',
        provider+' clicking the summary reveals the body');
      check(node('progress').matches('.message.assistant')&&node('final').matches('.message.assistant')&&
        getComputedStyle(node('progress').querySelector('.message-body')).color===getComputedStyle(node('final').querySelector('.message-body')).color,
        provider+' commentary and final answer share normal message color');
      const progress=view?.progress||document.createElement('div');
      renderChatProgress(progress,{agentName:provider,active:true,onInput(){throw Error('Retired shortcut');}});
      check(!progress.querySelector('button')&&progress.dataset.state==='working',provider+' progress contains no shortcut');
      const command=node('command');command.open=true;await frame();await frame();
      const count=reads;messages=messages.map(message=>({...message,turnStatus:'completed'}));apply();await frame();await frame();
      check(node('command').open,provider+' completion does not collapse an individual command');
      check(![...container.querySelectorAll('summary')].some(summary=>/^用时 /.test(summary.textContent)),provider+' no generated task duration disclosure');
      check(reads===count,provider+' refresh never requests a process directory');
      check(node('supplement').parentElement===container,provider+' supplement never enters a task wrapper');
      if(view){view.input.value='Unsent '+provider;apply();check(view.input.value==='Unsent '+provider,provider+' draft survives refresh');}
      document.documentElement.dataset.showReasoning='false';document.documentElement.dataset.showProcess='false';
      if(timeline)timeline.setVisibility({showReasoning:false,showProcess:false});else apply();await frame();await frame();
      check(node('reason').hidden&&node('command').hidden&&node('progress').hidden,provider+' display preferences hide only matching rows');
      check(!node('input').hidden&&!node('supplement').hidden&&!node('final').hidden,provider+' user and final answer stay visible');
      document.documentElement.dataset.showReasoning='true';document.documentElement.dataset.showProcess='true';
    }finally{view?.clear();timeline?.dispose();root.remove();}
  }}finally{for(const [key,value] of [['showReasoning',appearance.reasoning],['showProcess',appearance.process]])if(value===undefined)delete document.documentElement.dataset[key];else document.documentElement.dataset[key]=value;}
  return {passed:checks.length,width:innerWidth,checks};
}
