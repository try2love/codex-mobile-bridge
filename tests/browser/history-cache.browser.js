// Dedicated synthetic origin only: this fixture clears its IndexedDB history.
async function runHistoryCacheBrowserChecks(){
  if(location.hostname!=='history-cache.fixture.invalid')throw Error('Use the isolated history-cache fixture origin');
  const checks=[],failures=[],instances=[],clock={now:1000000};
  const make=(database=indexedDB)=>{const cache=new ChatHistoryCache(database,()=>clock.now);instances.push(cache);return cache;};
  const cache=make(),value=text=>({messages:[{id:text,role:'assistant',text}]}),equal=(actual,expected,label)=>{
    if(JSON.stringify(actual)!==JSON.stringify(expected))throw Error(label);
  };
  const check=(ok,label)=>{if(!ok)throw Error(label);};
  const test=async(name,run)=>{try{await cache.forget();clock.now+=1000;await run();checks.push(name);}catch(error){failures.push({name,error:error.message});}};
  const records=async()=>new Promise((resolve,reject)=>{
    cache.open().then(db=>{const request=db.transaction('pages').objectStore('pages').getAll();request.onsuccess=()=>resolve(request.result);request.onerror=()=>reject(request.error);});
  });
  const bounded=async promise=>{let timer;try{return await Promise.race([promise,new Promise((_,reject)=>timer=setTimeout(()=>reject(Error('Cache operation did not settle')),3000))]);}finally{clearTimeout(timer);}};
  try{
    await test('persisted history is readable from a separate cache instance',async()=>{
      const page=value('persisted');check(await cache.write('gateway/auth/client/chat','recent',page),'initial write failed');equal(await make().read('gateway/auth/client/chat'),page,'independent instance lost the persisted page');
    });
    await test('gateway auth host client chat and slot scopes remain separate',async()=>{
      const scope=JSON.stringify(['gateway-a','auth-a','host-a','codex','chat-a']);await cache.write(scope,'recent',value('recent'));await cache.write(scope,'older',value('older'));
      for(let field=0;field<5;field++){const parts=['gateway-a','auth-a','host-a','codex','chat-a'];parts[field]+='-other';equal(await make().read(JSON.stringify(parts)),null,'scope component '+field+' crossed identities');}
      equal(await cache.read(scope,'older'),value('older'),'page slot crossed recent/older');equal(await cache.read(scope),value('recent'),'recent page changed');
    });
    await test('TTL expiry and future timestamps are not displayed and expired pages are pruned on write',async()=>{
      cache.ttl=100;await cache.write('ttl','recent',value('expires'));clock.now+=99;check(await cache.read('ttl'),'page expired early');clock.now++;equal(await cache.read('ttl'),null,'page remained at its TTL boundary');
      clock.now++;await cache.write('fresh','recent',value('fresh'));check(!(await records()).some(row=>row.scope==='ttl'),'expired record was retained');clock.now--;equal(await cache.read('fresh'),null,'future timestamp was displayed');
      cache.ttl=7*24*60*60*1000;
    });
    await test('page count evicts the oldest persisted page across scopes',async()=>{
      cache.maxPages=2;for(const key of ['first','second','third']){clock.now++;await cache.write(key,'recent',value(key));}
      equal(await cache.read('first'),null,'oldest page survived the count limit');check(await cache.read('second')&&await cache.read('third'),'newer pages were lost');check((await records()).length<=2,'persisted page count exceeds its limit');cache.maxPages=256;
    });
    await test('UTF-8 byte limit evicts old records and an oversized page cannot replace a cached page',async()=>{
      const page=value('中文'.repeat(12)),bytes=new TextEncoder().encode(JSON.stringify(page)).byteLength;cache.maxBytes=2*bytes;
      for(const key of ['first','second','third']){clock.now++;await cache.write(key,'recent',page);}
      equal(await cache.read('first'),null,'byte limit retained oldest page');check((await records()).reduce((sum,row)=>sum+row.bytes,0)<=cache.maxBytes,'persisted bytes exceed the limit');
      cache.maxPageBytes=bytes-1;check(await cache.write('third','recent',page)===false,'oversized page was accepted');equal(await cache.read('third'),page,'oversized write damaged existing history');cache.maxBytes=32*1024*1024;cache.maxPageBytes=2*1024*1024;
    });
    await test('session forget removes every slot only from that session',async()=>{
      await cache.write('session-a','recent',value('a'));await cache.write('session-a','older',value('older'));await cache.write('session-b','recent',value('b'));await cache.forget('session-a');
      equal(await make().read('session-a'),null,'forgotten recent page remained');equal(await make().read('session-a','older'),null,'forgotten older page remained');equal(await make().read('session-b'),value('b'),'forget removed another session');
    });
    await test('logout forget clears all persisted scopes across instances',async()=>{
      await cache.write('gateway-a','recent',value('a'));await make().write('gateway-b','older',value('b'));await cache.forget();equal(await records(),[],'logout left persisted scopes behind');equal(await make().read('gateway-b','older'),null,'another instance still read cleared history');
    });
    await test('forget cancels an earlier write still awaiting the same instance database',async()=>{
      const racing=make(),db=await racing.open();let release;const gate=new Promise(resolve=>release=resolve);racing.open=async()=>{await gate;return db;};
      const writing=racing.write('race','recent',value('stale')),forgetting=racing.forget('race');release();check(await writing===false,'stale pending write was committed');await forgetting;equal(await cache.read('race'),null,'forget resurrected pending history');
    });
    for(const all of [false,true])await test((all?'logout':'session')+' forget cannot be undone by another instance pending write',async()=>{
      const racing=make(),db=await racing.open();let release;const gate=new Promise(resolve=>release=resolve);racing.open=async()=>{await gate;return db;};
      await cache.write('keep','recent',value('keep'));const writing=racing.write('race','recent',value('stale'));await cache.forget(all?undefined:'race');release();await writing;
      equal(await make().read('race'),null,'earlier write from another cache instance resurrected forgotten history');if(!all)equal(await cache.read('keep'),value('keep'),'session forget removed an unrelated scope');
    });
    await test('account namespace forget removes every session and slot while retaining other agents hosts and accounts',async()=>{
      const namespace=JSON.stringify(['auth-a','host-a','claude']),first=JSON.stringify(['auth-a','host-a','claude','first']),second=JSON.stringify(['auth-a','host-a','claude','second']);
      const others=[['auth-a','host-a','codex','first'],['auth-a','host-a','claude-profile:other','first'],['auth-b','host-a','claude','first'],['auth-a','host-b','claude','first']].map(parts=>JSON.stringify(parts));
      await cache.write(first,'recent',value('first'));await cache.write(first,'older',value('older'));await cache.write(second,'recent',value('second'));for(const scope of others)await cache.write(scope,'recent',value('retained'));
      await cache.forgetNamespace(namespace);equal(await make().read(first),null,'first namespace session survived');equal(await make().read(first,'older'),null,'older namespace slot survived');equal(await make().read(second),null,'second namespace session survived');for(const scope of others)equal(await make().read(scope),value('retained'),'namespace deletion crossed an agent host or auth identity');
    });
    await test('namespace forget rejects another instance earlier pending write and permits later fresh history',async()=>{
      const namespace=JSON.stringify(['auth-a','host-a','claude']),scope=JSON.stringify(['auth-a','host-a','claude','race']),other=JSON.stringify(['auth-a','host-a','codex','keep']);
      await cache.write(other,'recent',value('keep'));const racing=make(),db=await racing.open();let release;const gate=new Promise(resolve=>release=resolve);racing.open=async()=>{await gate;return db;};
      const writing=racing.write(scope,'recent',value('stale'));await cache.forgetNamespace(namespace);release();check(await writing===false,'namespace clear accepted the delayed stale write');equal(await make().read(scope),null,'namespace history was resurrected');equal(await make().read(other),value('keep'),'namespace clear removed another agent');
      clock.now++;check(await cache.write(scope,'recent',value('fresh')),'new namespace history remained blocked');equal(await make().read(scope),value('fresh'),'fresh namespace history was lost');
    });
    await test('missing or denied storage settles without breaking the page',async()=>{
      for(const database of [null,{open(){throw new DOMException('Synthetic storage denial','SecurityError');}},{open(){const request={};queueMicrotask(()=>request.onerror?.());return request;}},{open(){const request={};queueMicrotask(()=>request.onblocked?.());return request;}}]){
        const unavailable=make(database);equal(await bounded(unavailable.read('a')),null,'unavailable storage returned a page');check(await bounded(unavailable.write('a','recent',value('a')))===false,'unavailable storage accepted a write');await bounded(unavailable.forget());
      }
      document.body.dataset.cacheResponsive='true';check(document.body.dataset.cacheResponsive==='true','page became unusable');
    });
    await test('an IndexedDB open with no callbacks times out without blocking browser timers',async()=>{
      const unavailable=make({open(){return {};}});let beats=0;const timer=setInterval(()=>beats++,20),started=performance.now();
      try{equal(await bounded(unavailable.read('a')),null,'unresponsive open returned history');check(beats>1,'browser event loop stopped while opening storage');check(performance.now()-started<3000,'open timeout exceeded the budget');check(await bounded(unavailable.write('a','recent',value('a')))===false,'timed-out storage accepted a write');await bounded(unavailable.forget());}finally{clearInterval(timer);}
    });
    return {passed:checks.length,checks,failures};
  }finally{await cache.forget();for(const instance of instances)(await instance.open())?.close();}
}
