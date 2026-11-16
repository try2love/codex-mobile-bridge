'use strict';
// Display-only history. The caller supplies the verified gateway/auth/client scope;
// cached content never supplies live permissions, approvals or write receipts.
class ChatHistoryCache {
  constructor(database=globalThis.indexedDB,now=()=>Date.now()){
    this.database=database;this.now=now;this.generation=0;
    this.ttl=7*24*60*60*1000;this.maxBytes=32*1024*1024;this.maxPageBytes=2*1024*1024;this.maxPages=256;
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
    const generation=this.generation,db=await this.open();if(!db||generation!==this.generation)return null;
    return new Promise(resolve=>{
      try{
        const request=db.transaction('pages').objectStore('pages').get(JSON.stringify([scope,slot]));
        request.onerror=()=>resolve(null);
        request.onsuccess=()=>{const record=request.result;resolve(generation===this.generation&&record&&record.savedAt<=this.now()&&this.now()-record.savedAt<this.ttl?record.value:null);};
      }catch{resolve(null);}
    });
  }
  async write(scope,slot,value){
    if(!scope)return false;
    const savedAt=this.now();
    let bytes;
    try{bytes=new TextEncoder().encode(JSON.stringify(value)).byteLength;}catch{return false;}
    if(bytes>this.maxPageBytes)return false;
    const generation=this.generation,db=await this.open();if(!db||generation!==this.generation)return false;
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
          let size=0,count=0;
          store.index('budget').openKeyCursor(null,'prev').onsuccess=event=>{
            const cursor=event.target.result;if(!cursor)return;
            const [recordTime,recordBytes]=cursor.key,expired=this.now()-recordTime>=this.ttl||recordTime>this.now();
            if(!expired){size+=recordBytes;count++;}
            if(expired||size>this.maxBytes||count>this.maxPages)store.delete(cursor.primaryKey);
            cursor.continue();
          };
        };
      }catch{resolve(false);}
    });
  }
  forgetNamespace(namespace){return this.forget(namespace,true);}
  async forget(scope,namespace=false){
    this.generation++;
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
