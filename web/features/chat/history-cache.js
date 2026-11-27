'use strict';
// Display-only history. The caller supplies the verified gateway/auth/client scope;
// cached content never supplies live permissions, approvals or write receipts.
class ChatHistoryCache {
  constructor(database=globalThis.indexedDB,now=()=>Date.now()){
    this.database=database;this.now=now;this.generation=0;this.nativeQueue=Promise.resolve();
    this.ttl=7*24*60*60*1000;
  }
  native(){return globalThis.window?.BridgeMobileStorage;}
  nativeKey(scope,slot){return 'history:'+JSON.stringify([scope,slot]);}
  serialNative(work){const pending=this.nativeQueue.then(work,work);this.nativeQueue=pending.catch(()=>{});return pending;}
  async readNative(scope,slot){
    const native=this.native();if(!native)return null;
    const key=this.nativeKey(scope,slot),record=await native.request('read',key);
    if(record&&record.savedAt<=this.now()&&this.now()-record.savedAt<this.ttl)return record.value;
    if(record)native.request('remove',key);
    return null;
  }
  async forgetNative(scope,namespace){
    const native=this.native();if(!native)return;
    const keys=await native.request('list','history:');
    for(const key of Array.isArray(keys)?keys:[]){
      let stored;try{[stored]=JSON.parse(key.slice(8));}catch{continue;}
      if(!scope||(namespace?this.namespace(stored)===scope:stored===scope))await native.request('remove',key);
    }
  }
  namespace(scope){try{const parts=JSON.parse(scope);return Array.isArray(parts)?JSON.stringify(parts.slice(0,-1)):'';}catch{return '';}}
  open(){
    if(!this.database)return Promise.resolve(null);
    if(this.ready)return this.ready;
    this.ready=new Promise(resolve=>{
      let settled=false;
      const finish=db=>{if(settled){db?.close();return;}settled=true;clearTimeout(timer);resolve(db);};
      const timer=setTimeout(()=>finish(null),1500);
      try{
        const request=this.database.open('bridge-chat-history',3);
        request.onupgradeneeded=()=>{
          const db=request.result;
          const store=db.objectStoreNames.contains('pages')?request.transaction.objectStore('pages'):db.createObjectStore('pages',{keyPath:'id'});
          if(!store.indexNames.contains('scope'))store.createIndex('scope','scope');
          if(!store.indexNames.contains('budget'))store.createIndex('budget',['savedAt','bytes']);
          if(!db.objectStoreNames.contains('clears'))db.createObjectStore('clears');
        };
        request.onerror=request.onblocked=()=>finish(null);
        request.onsuccess=()=>{const db=request.result;db.onversionchange=()=>{db.close();this.ready=null;};finish(db);};
      }catch{finish(null);}
    });
    return this.ready;
  }
  async read(scope,slot='recent'){
    if(!scope)return null;
    const generation=this.generation,native=await this.serialNative(()=>this.readNative(scope,slot));
    if(generation!==this.generation)return null;if(native!==null)return native;
    const db=await this.open();if(!db||generation!==this.generation)return null;
    return new Promise(resolve=>{
      try{
        const request=db.transaction('pages').objectStore('pages').get(JSON.stringify([scope,slot]));
        request.onerror=()=>resolve(null);
        request.onsuccess=()=>{
          const record=request.result,valid=generation===this.generation&&record&&record.savedAt<=this.now()&&this.now()-record.savedAt<this.ttl;
          resolve(valid?record.value:null);
          if(valid&&this.native())this.serialNative(()=>generation===this.generation&&this.native().request('write',this.nativeKey(scope,slot),{savedAt:record.savedAt,value:record.value}));
        };
      }catch{resolve(null);}
    });
  }
  async write(scope,slot,value){
    if(!scope)return false;
    const savedAt=this.now();
    let bytes;
    try{bytes=new TextEncoder().encode(JSON.stringify(value)).byteLength;}catch{return false;}
    const generation=this.generation;
    if(this.native()){const written=await this.serialNative(async()=>generation===this.generation&&await this.native().request('write',this.nativeKey(scope,slot),{savedAt,value}));if(written===true)return true;}
    const db=await this.open();if(!db||generation!==this.generation)return false;
    return new Promise(resolve=>{
      try{
        const transaction=db.transaction(['pages','clears'],'readwrite'),store=transaction.objectStore('pages'),clears=transaction.objectStore('clears');
        let written=false;
        transaction.oncomplete=()=>resolve(written);transaction.onabort=transaction.onerror=()=>resolve(false);
        const globalClear=clears.get(''),scopeClear=clears.get(scope),namespaceClear=clears.get('@'+this.namespace(scope));
        namespaceClear.onsuccess=()=>{
          // The comparison shares the write transaction with put, including
          // other tabs. Same-millisecond writes conservatively miss the cache.
          if(savedAt<=Math.max(globalClear.result??-1,scopeClear.result??-1,namespaceClear.result??-1)||savedAt>this.now()||this.now()-savedAt>=this.ttl)return;
          store.put({id:JSON.stringify([scope,slot]),scope,savedAt,bytes,value});written=true;
          store.index('budget').openKeyCursor(null,'prev').onsuccess=event=>{
            const cursor=event.target.result;if(!cursor)return;
            const [recordTime]=cursor.key,expired=this.now()-recordTime>=this.ttl||recordTime>this.now();
            if(expired)store.delete(cursor.primaryKey);
            cursor.continue();
          };
        };
      }catch{resolve(false);}
    });
  }
  forgetNamespace(namespace){return this.forget(namespace,true);}
  async forget(scope,namespace=false){
    this.generation++;
    await this.serialNative(()=>this.forgetNative(scope,namespace));
    const db=await this.open();if(!db)return;
    return new Promise(resolve=>{
      try{
        const transaction=db.transaction(['pages','clears'],'readwrite'),store=transaction.objectStore('pages'),clears=transaction.objectStore('clears');
        transaction.oncomplete=transaction.onabort=transaction.onerror=()=>resolve();
        if(!scope){store.clear();clears.clear();}
        else store.index('scope').openKeyCursor(namespace?null:scope).onsuccess=event=>{const cursor=event.target.result;if(cursor){if(!namespace||this.namespace(cursor.key)===scope)store.delete(cursor.primaryKey);cursor.continue();}};
        clears.put(this.now(),namespace?'@'+scope:scope||'');
        clears.openCursor().onsuccess=event=>{const cursor=event.target.result;if(cursor){if(this.now()-cursor.value>=this.ttl)cursor.delete();cursor.continue();}};
      }catch{resolve();}
    });
  }
}
if(typeof window!=='undefined')window.BridgeChatHistoryCache=new ChatHistoryCache();
if(typeof module!=='undefined')module.exports={ChatHistoryCache};
