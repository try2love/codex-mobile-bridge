'use strict';

// Drafts stay on this device. Every read requires a verified gateway and account scope.
class ComposerDrafts {
  constructor(storage=localStorage,coordinator=globalThis.indexedDB){this.storage=storage;this.coordinator=coordinator;this.temporaryPending=new Map();}
  key(scope,kind='draft'){
    if(!scope||!['gateway','provider','account','host','session'].every(name=>typeof scope[name]==='string'&&scope[name]))return null;
    return 'composer-v1:'+kind+':'+JSON.stringify([scope.gateway,scope.provider,scope.account,scope.host,scope.session]);
  }
  read(key){if(!key)return null;try{return JSON.parse(this.storage.getItem(key)||'null');}catch{return null;}}
  write(key,value){if(!key)return false;try{this.storage.setItem(key,JSON.stringify(value));return true;}catch{return false;}}
  remove(key){if(!key)return;try{this.storage.removeItem(key);}catch{}}
  load(scope){const saved=this.read(this.key(scope));return saved&&typeof saved.text==='string'?saved:null;}
  #lastDraft;
  #readDraftForSave(key){
    try{
      const raw=this.storage.getItem(key);
      if(this.#lastDraft?.key!==key||this.#lastDraft.raw!==raw){
        const value=JSON.parse(raw||'null');
        this.#lastDraft={key,raw,value:typeof value?.text==='string'?value:null};
      }
      return this.#lastDraft.value;
    }catch{return null;}
  }
  #sameDraft(draft,previous){
    const sameList=(list,stored,equal)=>Array.isArray(stored)&&list.length===stored.length&&list.every((item,i)=>equal(item,stored[i]));
    return previous&&draft.text===previous.text&&draft.selectionStart===previous.selectionStart&&
      draft.selectionEnd===previous.selectionEnd&&draft.workMode===previous.workMode&&
      sameList(draft.quotes,previous.quotes,(a,b)=>b&&a.text===b.text&&a.source===b.source&&a.provider===b.provider)&&
      sameList(draft.skills,previous.skills,(a,b)=>a===b);
  }
  save(scope,fields){
    const key=this.key(scope);if(!key)return null;
    // Re-read the stored value so another tab's edit or removal is always seen.
    const previous=this.#readDraftForSave(key);
    const text=String(fields.text||'').slice(0,100000);
    const clamp=value=>Math.max(0,Math.min(text.length,Number(value)||0));
    const quotes=Array.isArray(fields.quotes)?fields.quotes.filter(value=>value&&typeof value.text==='string'&&value.text.trim()).slice(0,8).map(value=>({
      text:value.text.slice(0,100000),source:typeof value.source==='string'?value.source.slice(0,500):'',provider:typeof value.provider==='string'?value.provider.slice(0,40):'',
    })):[];
    const skills=Array.isArray(fields.skills)?fields.skills.filter(value=>typeof value==='string').slice(0,8):[];
    const draft={text,selectionStart:clamp(fields.selectionStart),selectionEnd:clamp(fields.selectionEnd),workMode:fields.workMode||'default',quotes,skills,revision:previous?.revision||0};
    if(this.#sameDraft(draft,previous))return draft;
    draft.revision++;
    try{
      const raw=JSON.stringify(draft);
      this.storage.setItem(key,raw);
      this.#lastDraft={key,raw,value:{...draft,quotes:draft.quotes.map(quote=>({...quote})),skills:[...draft.skills]}};
      return draft;
    }catch{return null;}
  }
  clear(scope){const key=this.key(scope);if(this.#lastDraft?.key===key)this.#lastDraft=undefined;this.remove(key);}
  pending(scope){const saved=this.read(this.key(scope,'pending'));return saved&&typeof saved.id==='string'&&saved.id?saved:null;}
  discardPending(scope,id){if(this.pending(scope)?.id===id)this.remove(this.key(scope,'pending'));}
  begin(scope,body,id){
    const key=this.key(scope,'pending');if(!key)return null;
    const fingerprint=JSON.stringify(body),raw=this.pending(scope),settled=this.read(this.key(scope,'pending-v2'));
    const previous=settled?.legacyVersion===JSON.stringify(raw)?null:raw;
    if(previous?.fingerprint===fingerprint)return previous;
    if(previous?.body?.text===body.text)return {...previous,blocked:true};
    const pending={id,fingerprint,body};return this.write(key,pending)?pending:null;
  }
  confirm(scope,id,revision){
    const pending=this.pending(scope);if(pending?.id!==id)return false;
    this.remove(this.key(scope,'pending'));
    if(this.load(scope)?.revision===revision)this.clear(scope);
    return true;
  }
  // Only coordinated clients use this key: an older page cannot silently join an
  // attempt without registering it. Imported v1 requests remain untracked.
  coordinate(update){
    if(!this.coordinator)return Promise.resolve(null);
    return new Promise(resolve=>{
      let done=false,db,transaction,value;
      const finish=result=>{if(done)return;done=true;clearTimeout(timer);db?.close();resolve(result);};
      const timer=setTimeout(()=>{try{transaction?.abort();}catch{}finish(null);},2000);
      let request;try{request=this.coordinator.open('composer-pending-v2',1);}catch{finish(null);return;}
      request.onupgradeneeded=()=>request.result.createObjectStore('mutex');
      request.onblocked=request.onerror=()=>finish(null);
      request.onsuccess=()=>{
        db=request.result;if(done){db.close();return;}
        db.onversionchange=()=>db.close();
        try{
          transaction=db.transaction('mutex','readwrite');
          transaction.oncomplete=()=>finish(value);
          transaction.onabort=transaction.onerror=()=>finish(null);
          transaction.objectStore('mutex').get('pending').onsuccess=()=>{
            if(done)return;
            try{value=update(true);}catch{transaction.abort();}
          };
        }catch{finish(null);}
      };
    });
  }
  pendingSend(scope){const saved=this.read(this.key(scope,'pending-v2')),old=this.pending(scope);return saved?.id?saved:old&&saved?.legacyVersion!==JSON.stringify(old)?old:null;}
  async beginSend(scope,body,id,attempt,imported=false){
    if(!this.coordinator)return this.pendingSend(scope)?{blocked:true}:false;
    const draftRevision=this.load(scope)?.revision;
    const result=await this.coordinate(coordinated=>{
      const key=this.key(scope,'pending-v2');if(!key)return null;
      const saved=this.read(key),old=this.pending(scope),legacyVersion=old?JSON.stringify(old):null;
      const unseenLegacy=old&&saved?.legacyVersion!==legacyVersion;
      const fingerprint=JSON.stringify(body);
      // A still-open v1 page may write another unknown after v2 was settled.
      // Never hide that new version behind a tombstone or an active v2 request.
      if(unseenLegacy&&(saved?.id||old.fingerprint!==fingerprint))return {blocked:true};
      const previous=unseenLegacy?old:saved;
      if(previous?.body?.text===body.text&&previous.fingerprint!==fingerprint)return {...previous,blocked:true};
      const same=previous?.fingerprint===fingerprint;
      const pending=same?{...previous}:{id,fingerprint,body,draftRevision};
      pending.attempts=[...(same&&Array.isArray(previous.attempts)?previous.attempts:[]),attempt];
      pending.untracked=!!(same&&(previous.untracked||unseenLegacy)||imported||!coordinated);
      pending.legacyVersion=unseenLegacy?legacyVersion:saved?.legacyVersion??null;
      return this.write(key,pending)?pending:false;
    });
    return result===null?(this.pendingSend(scope)?{blocked:true}:false):result;
  }
  settleSend(scope,id,attempt,action,revision){
    return this.coordinate(coordinated=>{
      if(!coordinated)return false;
      const key=this.key(scope,'pending-v2'),saved=this.read(key),old=this.pending(scope),legacyVersion=old?JSON.stringify(old):null;
      if(action==='ignore'&&old?.id===id&&saved?.id!==id){
        return this.write(key,{...(saved||{cleared:true}),legacyVersion});
      }
      const pending=saved??(action==='ignore'?old:null);
      if(pending?.id!==id)return false;
      if(action==='reject'){
        if(!pending.attempts?.includes(attempt))return false;
        pending.attempts=pending.attempts.filter(value=>value!==attempt);
        if(old&&pending.legacyVersion!==legacyVersion)pending.untracked=true;
        if(pending.untracked||pending.attempts.length){this.write(key,pending);return false;}
      }
      // A tombstone prevents reimporting v1 without racing an older page's writes.
      if(!this.write(key,{cleared:true,legacyVersion:action==='ignore'&&old?.id===id?legacyVersion:pending.legacyVersion??null}))return false;
      // Do not remove a legacy record written by a still-open older page.
      if(action==='confirm'&&this.load(scope)?.revision===revision)this.clear(scope);
      return true;
    });
  }
  pendingRecords(scope,{temporary,temporaryKey='pending:'+scope?.host+'|'+scope?.session}={}){
    if(!this.key(scope))return [];
    const records=[],saved=this.read(this.key(scope,'pending-v2')),old=this.pending(scope);
    const add=(source,value)=>{
      if(!value||typeof value.id!=='string'||!value.id)return;
      const body=value.body||Object.fromEntries(['text','mode','skills','workMode','attachments','references','uiLocale'].filter(key=>Object.prototype.hasOwnProperty.call(value,key)).map(key=>[key,value[key]]));
      records.push({source,scopeKey:this.key(scope),id:value.id,version:JSON.stringify(value),body:JSON.parse(JSON.stringify(body)),fingerprint:value.fingerprint||JSON.stringify(body),accountUnverified:(source==='session'||source==='memory')&&!value.account});
    };
    if(old&&saved?.legacyVersion!==JSON.stringify(old))add('v1',old);
    add('v2',saved);
    const matches=value=>value?.gateway===scope.gateway&&value.target===scope.host+'|'+scope.session&&(!value.account||value.account===scope.account)&&(!value.provider||value.provider===scope.provider);
    let session;try{session=JSON.parse(temporary?.getItem(temporaryKey)||'null');}catch{}
    if(matches(session))add('session',session);
    const memory=this.temporaryPending.get(temporaryKey);if(matches(memory))add('memory',memory);
    return records;
  }
  recoveryReceiptMatches(scope,record,receipt){
    if(receipt?.id!==record.id||!['accepted','cancelled','ignored','queued'].includes(receipt.status))return false;
    if(receipt.account!==undefined&&receipt.account!==scope.account)return false;
    if(receipt.scope&&this.key(receipt.scope)!==this.key(scope))return false;
    if(receipt.fingerprint!==undefined&&receipt.fingerprint!==record.fingerprint)return false;
    const canonical=value=>JSON.stringify(value,(_,item)=>item&&typeof item==='object'&&!Array.isArray(item)?Object.fromEntries(Object.keys(item).sort().map(key=>[key,item[key]])):item);
    return receipt.body===undefined||canonical(receipt.body)===canonical(record.body);
  }
  async reconcilePending(scope,envelope,options={}){
    if(!this.key(scope)||this.key(scope)!==this.key(envelope?.scope))return {resolved:[],remaining:this.pendingRecords(scope,options)};
    const records=options.records||this.pendingRecords(scope,options),receipts=Array.isArray(envelope.receipts)?envelope.receipts:[];
    const selected=records.flatMap(record=>{const receipt=receipts.find(value=>this.recoveryReceiptMatches(scope,record,value));return receipt?[{...record,status:receipt.status}]:[];});
    return this.consumePendingRecords(scope,selected,options);
  }
  ignoreLocalPending(scope,record,options={}){
    return this.consumePendingRecords(scope,[{...record,status:'localIgnored'}],options);
  }
  async consumePendingRecords(scope,selected,options){
    if(!this.key(scope))return {resolved:[],remaining:[]};
    const matches=(record,current)=>record.scopeKey===current.scopeKey&&record.source===current.source&&record.id===current.id&&record.version===current.version;
    let clearedDraft=null;
    const shared=await this.coordinate(()=>{
      const current=this.pendingRecords(scope,options),eligible=selected.filter(record=>['v1','v2'].includes(record.source)&&current.some(value=>matches(record,value)));
      if(!eligible.length)return [];
      const key=this.key(scope,'pending-v2'),saved=this.read(key);
      const legacy=eligible.find(record=>record.source==='v1'),active=eligible.some(record=>record.source==='v2');
      const next=active?{cleared:true,legacyVersion:saved?.legacyVersion??null}:{...(saved||{cleared:true})};
      if(legacy)next.legacyVersion=legacy.version;
      if(!this.write(key,next))return [];
      const accepted=eligible.find(record=>record.source==='v2'&&['accepted','queued'].includes(record.status)),draft=this.load(scope);
      if(accepted&&Number.isInteger(saved?.draftRevision)&&draft?.revision===saved.draftRevision){
        this.clear(scope);
        if(!this.load(scope))clearedDraft={scope:{...scope},draft,body:accepted.body,id:accepted.id};
      }
      return eligible;
    });
    const resolved=Array.isArray(shared)?shared:[];
    // Session storage and the memory fallback belong to this document. Never
    // delete a replacement written since the UI captured its recovery snapshot.
    for(const record of selected){
      if(!['session','memory'].includes(record.source)||!this.pendingRecords(scope,options).some(value=>matches(record,value)))continue;
      const key=options.temporaryKey||'pending:'+scope.host+'|'+scope.session;
      if(record.source==='memory')this.temporaryPending.delete(key);
      else {try{options.temporary.removeItem(key);}catch{continue;}}
      resolved.push(record);
    }
    return {resolved:resolved.map(({source,id,status})=>({source,id,status})),remaining:this.pendingRecords(scope,options),clearedDraft};
  }
  continueAccount(previous,next){
    if(!this.key(previous)||!this.key(next)||previous.account===next.account||
       !['gateway','provider','host','session'].every(key=>previous[key]===next[key]))return null;
    // Copy only unsent composition. Submission IDs and receipts remain in the
    // original scope, and any unresolved send prevents copying its draft.
    if(this.pendingRecords(previous).length||this.pendingRecords(next).length||this.load(next))return null;
    const draft=this.load(previous);if(!draft)return null;
    return this.save(next,{text:draft.text,selectionStart:draft.selectionStart,selectionEnd:draft.selectionEnd,
      quotes:draft.quotes,workMode:'default'});
  }
  migrate(scope,temporary,keys){
    if(!this.key(scope)||this.load(scope))return this.load(scope);
    let text;try{text=temporary.getItem(keys.text);}catch{return null;}
    if(text===null)return null;
    let skills=[];try{skills=JSON.parse(temporary.getItem(keys.skills)||'[]');}catch{}
    const saved=this.save(scope,{text,skills,workMode:'default'});
    if(saved)for(const key of Object.values(keys))try{temporary.removeItem(key);}catch{}
    return saved;
  }
}

if(typeof module!=='undefined')module.exports={ComposerDrafts};
