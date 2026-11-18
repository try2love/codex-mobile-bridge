// Use the production chat renderers with a synthetic native turn and process endpoints.
async function runProcessDisclosureChecks(){
  await BridgeFeatures.load('chat');
  const checks=[],frame=()=>new Promise(requestAnimationFrame);
  const check=(condition,label)=>{if(!condition)throw Error(label);checks.push(label);};
  const wait=async predicate=>{for(let i=0;i<90&&!predicate();i++)await frame();};
  const start='2026-11-18T10:00:00Z',end='2026-11-18T10:02:05Z';
  const row=(id,order,role,extras={})=>({id,key:id,order,role,version:'1',turnId:'turn',turnStartedAt:start,text:'',...extras});
  const process=(id,order)=>row(id,order,'activity',{kind:'processPage',processCursor:id});
  const first=[row('input',0,'user',{text:'Initial input'}),process('before',1)];
  const live=[...first,row('supplement',2,'user',{text:'Keep this supplement visible',kind:'steeringUserMessage'}),process('after',3)];
  const answer=row('final',4,'assistant',{phase:'final_answer',text:'Final answer'});
  const files=row('files',5,'fileChanges',{fileSummary:{count:1,added:2,removed:1,files:[{path:'fixture.txt',added:2,removed:1}]}});
  // Leave enough space after the anchor even when the process is expanded.
  const tail=Array.from({length:10},(_,i)=>row('tail'+i,6+i,'assistant',{turnId:'tail',phase:'final_answer',text:'Following message '+i}));
  for(const provider of ['codex','claude']){
    const root=document.createElement('section');root.className='app';root.style.cssText='position:fixed;inset:0;z-index:40';document.body.append(root);
    const assert=(condition,label)=>check(condition,provider+' '+label);
    const reads=[];
    let timeline,view,viewport,container,sequence=0,apply;
    const detail=(messages,active,connected=true)=>({connected,session:{id:'task',title:'Fixture',status:active?'active':'idle',capabilities:{},requests:[]},messages,files:[],history:{hasMore:false}});
    const request=async(url,body)=>{
      if(body)throw Error('Unexpected write in process fixture');
      const query=new URL(url,'http://fixture').searchParams;
      const cursor=query.get('cursor')||query.get('historyProcess');
      const tool=query.get('key')||query.get('historyDetail');
      if(cursor==='long'){
        const after=query.get('after')||query.get('historyAfter');
        reads.push(after||'first');
        const older=!after;
        return {rows:[{key:older?'old-progress':'recent-progress',role:'assistant',
          text:older?'Earlier commentary':'Latest commentary',version:'1'}],
          next:older?'old-progress':null,hasEarlier:after==='latest'};
      }
      if(cursor){
        reads.push(cursor);
        return {rows:[
          {key:cursor+'-comment',role:'assistant',text:cursor+' commentary',version:'1'},
          {key:cursor+'-tool',role:'activity',kind:'commandExecution',title:'Command',detailCursor:cursor+'-tool',hasDetail:true,version:'1'},
        ],next:null};
      }
      if(tool){reads.push(tool);return {version:'1',text:'Synthetic command output',offset:0,next:null};}
      if(url.includes('/detail'))return detail([],false);
      if(url.includes('/workspace'))return {entries:[],total:0,project:'/synthetic'};
      return {sessions:[detail([],false).session]};
    };
    if(provider==='codex'){
      viewport=document.createElement('div');viewport.style.cssText='height:300px;overflow:auto';container=document.createElement('div');
      const older=document.createElement('button'),newer=document.createElement('button');viewport.append(container);root.append(viewport,older,newer);
      timeline=new ChatTimeline({elements:{viewport,container,older,newer},url:action=>'/synthetic/'+action+'?',request,renderMeta(){},renderText:(node,text)=>node.textContent=text,status(){}});
      apply=(rows,active,{reset=false,cached=false}={})=>timeline.apply({epoch:'task',sequence:++sequence,rows,meta:{requests:[],status:active?'active':'idle'},files:[],hasMore:false},sequence===1||reset,false,cached);
    }else{
      const select=document.createElement('select');for(const id of ['codex',provider])select.add(new Option(id,id));
      view=new DesktopSessionsView({root,select,notify(){},onSelect(){},csrf:()=>'',onUnauthorized(){},request});
      await view.choose(provider);await view.open('task');clearTimeout(view.timer);
      container=view.messageList;viewport=view.messages;viewport.style.cssText='height:300px;max-height:300px;overflow:auto';
      apply=(rows,active,{reset=false,cached=false}={})=>{if(reset)view.prepareChat('task');view.applyDetail(detail(rows,active,!cached),cached);};
    }
    const node=key=>container.querySelector('[data-key="'+key+'"]');
    const visible=element=>Boolean(element?.getBoundingClientRect().height);
    const duration=()=>[...container.querySelectorAll('summary')].filter(el=>el.textContent==='用时 2 分钟 5 秒'&&visible(el));
    const status=(rows,value,withClock=true)=>rows.map(message=>({...message,turnStatus:value,...(value==='completed'&&withClock?{turnCompletedAt:end}:{})}));
    try{
      apply(status(first,'inProgress'),true);
      await wait(()=>visible(node('before-comment')));
      assert(visible(node('before-comment')),'current native process commentary is visible while running');
      assert(container.textContent.includes('运行了命令'),'running commands have a semantic group');
      assert(reads.join(',')==='before','live preview only reads its directory');

      apply(status(live,'inProgress'),true);
      await wait(()=>visible(node('after-comment')));
      assert(visible(node('before-comment'))&&visible(node('after-comment')),'new progress is visible on both sides of the supplement');
      assert(reads.join(',')==='before,after','a supplement adds just the new bounded directory read');

      apply(status(live,'completed'),false);
      await frame();
      assert(duration().length===1,'completion shows one native duration');
      assert(!visible(node('before-comment'))&&!visible(node('after-comment')),'completion collapses both fragments');
      apply([...status([...live,answer,files],'completed'),...tail],false);
      await frame();
      const supplement=node('supplement'),final=node('final'),fileSummary=node('files');
      assert([supplement,final,fileSummary].every(el=>el?.parentElement===container&&visible(el)),'supplement, eventual answer and file summary stay visible');
      assert((supplement.compareDocumentPosition(final)&Node.DOCUMENT_POSITION_FOLLOWING)&&(final.compareDocumentPosition(fileSummary)&Node.DOCUMENT_POSITION_FOLLOWING),'ordinary messages stay in transcript order');
      duration()[0].click();
      await wait(()=>visible(node('before-comment'))&&visible(node('after-comment')));
      assert(visible(node('before-comment'))&&visible(node('after-comment')),'a single action reveals both process fragments');
      assert(reads.join(',')==='before,after','expansion reuses already loaded directories');

      const readTop=()=>supplement.getBoundingClientRect().top-viewport.getBoundingClientRect().top;
      viewport.scrollTop+=readTop();
      await frame();await frame();
      const offset=readTop();
      apply([...status([...live,answer,files],'completed'),...tail,{...tail.at(-1),id:'new',key:'new',order:99}],false);
      await frame();await frame();
      assert(duration().length===1&&visible(node('before-comment'))&&visible(node('after-comment')),'ordinary refresh preserves manual expansion');
      assert(Math.abs(readTop()-offset)<=2,'ordinary refresh keeps the supplement at the same reading position');
      duration()[0].click();
      await frame();
      assert(!visible(node('before-comment'))&&!visible(node('after-comment')),'one action folds both fragments again');

      reads.length=0;
      apply(status(live,'inProgress'),true,{reset:true,cached:true});
      await frame();await frame();
      assert(!visible(node('after-comment'))&&reads.length===0,'cached running state does not start process reads');
      apply(status([...live,answer,files],'completed',false),false,{reset:true});
      await frame();
      const headings=[...container.querySelectorAll('summary')].filter(el=>el.textContent==='执行过程'&&visible(el));
      assert(headings.length===1&&duration().length===0&&reads.length===0,'cold history starts folded without inventing a clock');
      headings[0].click();
      await wait(()=>visible(node('before-comment'))&&visible(node('after-comment')));
      assert(visible(node('before-comment'))&&visible(node('after-comment')),'expanding cold history reads both fragments');
      assert(reads.slice().sort().join(',')==='after,before','cold history reads each directory once and defers command bodies');

      reads.length=0;
      const long=[row('input',0,'user',{text:'Long task'}),process('long',1)];
      apply(status(long,'inProgress'),true,{reset:true});
      await wait(()=>visible(node('recent-progress')));
      assert(reads.join(',')==='latest'&&visible(node('recent-progress'))&&!node('old-progress'),'running a long turn shows only recent process');
      apply(status(long,'completed'),false);
      await frame();
      duration()[0].click();
      await wait(()=>visible(node('old-progress')));
      assert(reads.join(',')==='latest,first'&&visible(node('old-progress')),'opening a completed long turn starts at the beginning');
      container.querySelector('.chat-process-more')?.click();
      await wait(()=>visible(node('recent-progress')));
      assert(reads.join(',')==='latest,first,old-progress'&&visible(node('old-progress'))&&visible(node('recent-progress')),'history can continue from the first page to the former live preview');
    }finally{
      timeline?.dispose();view?.clear();root.remove();
    }
  }
  return {passed:checks.length,checks};
}
