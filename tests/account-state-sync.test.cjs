'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');

function fixture(){
 const node=tag=>({tag,children:[],textContent:'',hidden:false,disabled:false,isConnected:true,attributes:{},classList:{add(){}},
  append(...nodes){this.children.push(...nodes);},replaceChildren(...nodes){this.children=nodes;},setAttribute(key,value){this.attributes[key]=value;},closest(){return null;}});
 const context=vm.createContext({document:{createElement:node,addEventListener(){},hidden:false},window:{},Date,URL,
  setTimeout(){return 1;},clearTimeout(){},localStorage:{getItem(){},setItem(){}}});
 for(const file of ['web/shared/i18n.js','web/features/accounts/accounts.js','web/features/accounts/account-settings.js'])vm.runInContext(fs.readFileSync(path.join(__dirname,'..',file),'utf8'),context);
 const Sync=vm.runInContext('AccountStateSync',context),Accounts=vm.runInContext('AccountsPanel',context),Settings=vm.runInContext('AccountSettingsPanel',context),sync=new Sync();
 let value={accounts:[],reminders:{globalReset:false,globalResetSignal:false},switch:{phase:'idle'}},settings;
 let readAccounts=async()=>structuredClone(value),readSettings=async()=>structuredClone(value),write=async payload=>{
  value={...value,reminders:{...value.reminders,...payload.preferences}};return structuredClone(value);
 };
 const writes=[];
 const request=payload=>{writes.push(payload);return write(payload);};
 const accounts=new Accounts({root:node('div'),stateSync:sync,read:()=>readAccounts(),request,onUpdate:value=>settings?.accept(value)});
 settings=new Settings({remindersRoot:node('div'),stateSync:sync,read:()=>readSettings(),request,requestId:()=> 'fixture',onUpdate:value=>accounts.acceptReminders(value)});
 accounts.accept(structuredClone(value));
 return {accounts,settings,sync,writes,value:()=>value,setWrite:fn=>write=fn,setAccountRead:fn=>readAccounts=fn,setSettingsRead:fn=>readSettings=fn};
}

test('shared status reads coalesce only within the same query and write revision',async()=>{
 const {sync}=fixture(),finish=[];let calls=0;
 const read=()=>{calls++;return new Promise(resolve=>finish.push(resolve));};
 const old=sync.read(true,read),same=sync.read(true,read),forced=sync.read('refresh',read);
 assert.equal(old,same);assert.notEqual(old,forced);await Promise.resolve();assert.equal(calls,2);
 const end=sync.begin(),during=sync.read(true,read);end();const after=sync.read(true,read);
 assert.notEqual(old,during);assert.notEqual(during,after);
 await Promise.resolve();assert.equal(calls,4);
 finish.forEach((resolve,i)=>resolve(i));assert.deepEqual(await Promise.all([old,same,forced,during,after]),[0,0,1,2,3]);
 assert.equal(sync.reads.size,0);
 await sync.read(true,async()=>{calls++;return 'fresh';});assert.equal(calls,5);
});

test('failed shared reads release their slot and retry as a fresh read',async()=>{
 const {sync}=fixture();let calls=0;
 const read=async()=>{calls++;throw Error('Offline');};
 await assert.rejects(Promise.all([sync.read(false,read),sync.read(false,read)]),/Offline/);
 assert.equal(calls,1);assert.equal(sync.reads.size,0);
 assert.equal(await sync.read(false,async()=> 'recovered'),'recovered');
});

test('desktop account and update readers share the real status transport',async()=>{
 const ui=fixture(),source=fs.readFileSync(path.join(__dirname,'../desktop/shell/renderer.js'),'utf8');
 const start=source.indexOf('function readAccountStatus('),end=source.indexOf('\nlet accountSettings=',start);
 let calls=0,finish,shown=true;const payloads=[];
 const context=vm.createContext({accountStateSync:ui.sync,accountsPanel:{visible:()=>shown},api:{accounts:value=>{calls++;payloads.push(value);return new Promise(resolve=>finish=resolve);}}});
 vm.runInContext(source.slice(start,end),context);const read=vm.runInContext('readAccountStatus',context);
 assert.match(source,/desktop:true,read:readAccountStatus/);assert.match(source,/read:\(\)=>readAccountStatus\(\)/);
 ui.setAccountRead(()=>read());ui.setSettingsRead(()=>read());ui.settings.error='Previous read failed';
 const first=ui.accounts.refresh(),second=ui.settings.refresh();await Promise.resolve();assert.equal(calls,1);
 finish(structuredClone(ui.value()));await Promise.all([first,second]);
 assert.equal(ui.settings.error,'');assert.equal(ui.settings.loading,false);
 shown=false;const next=read();await Promise.resolve();assert.equal(payloads.at(-1).resetForecast,false);
 finish(structuredClone(ui.value()));await next;
});

test('a settings write supersedes an in-flight quota batch without replaying its remainder',async()=>{
 const ui=fixture(),accounts=Array.from({length:5},(_,i)=>({id:'row'+i,kind:'chatgpt',name:'Account '+i}));
 const before={...ui.value(),accounts};ui.accounts.accept(before);let finish;
 ui.setWrite(payload=>payload.action==='details'?new Promise(resolve=>finish=resolve):Promise.resolve({...before,reminders:{globalReset:true}}));
 const batch=ui.accounts.loadUsage(ui.accounts.generation);
 await ui.settings.perform({action:'reminders',preferences:{globalReset:true}});
 finish({...before,reminders:{globalReset:false}});await batch;
 assert.equal(ui.writes.filter(row=>row.action==='details').length,1);
 assert.equal(ui.accounts.value.reminders.globalReset,true);
 assert.equal(ui.accounts.value.accounts.length,5);assert.equal(ui.sync.busy,false);
});

for(const owner of ['accounts','settings'])for(const oldReturns of ['during','after'])test(`${owner} saves supersede the other panel's old reads returning ${oldReturns} the write`,async()=>{
 const ui=fixture(),other=owner==='accounts'?'settings':'accounts',old=structuredClone(ui.value());
 let finishRead,finishWrite;
 (other==='accounts'?ui.setAccountRead:ui.setSettingsRead)(()=>new Promise(resolve=>finishRead=resolve));
 const pending=ui[other].refresh();
 ui.setWrite(()=>new Promise(resolve=>finishWrite=resolve));
 const saving=ui[owner].perform({action:'reminders',preferences:{globalReset:true,globalResetSignal:true}});
 assert.equal(ui.sync.busy,true);assert.equal(ui[other].busy,true);assert.equal(ui[other].loading,false);
 await ui[other].perform({action:'reminders',preferences:{globalReset:false}});assert.equal(ui.writes.length,1);
 if(oldReturns==='during'){finishRead(old);await pending;}
 finishWrite({...old,reminders:{globalReset:true,globalResetSignal:true}});await saving;
 if(oldReturns==='after'){finishRead(old);await pending;}
 assert.equal(ui.accounts.value.reminders.globalReset,true);assert.equal(ui.settings.value.reminders.globalReset,true);
 assert.equal(ui.sync.busy,false);assert.equal(ui.sync.revision,2);
 assert.equal(ui.accounts.busy,false);assert.equal(ui.settings.busy,false);
});

test('notification snapshots only synchronize the bell and retain account nodes',async()=>{
 const ui=fixture(),rows=ui.accounts.rows.children,original=ui.accounts.value.accounts;
 ui.setSettingsRead(async()=>({...ui.value(),accounts:[{id:'unrelated',kind:'api'}],reminders:{globalReset:true}}));
 await ui.settings.refresh();
 assert.equal(ui.accounts.value.accounts,original);assert.equal(ui.accounts.rows.children,rows);
 assert.equal(ui.accounts.value.reminders.globalReset,true);
});

test('failed and cleared writes always release the shared barrier and never replay',async()=>{
 const ui=fixture();ui.setWrite(async()=>{throw Error('Save failed');});
 await ui.settings.perform({action:'reminders',preferences:{globalReset:true}});
 assert.equal(ui.sync.busy,false);assert.equal(ui.writes.length,1);assert.equal(ui.settings.value.reminders.globalReset,false);
 let finish;ui.setWrite(()=>new Promise(resolve=>finish=resolve));const pending=ui.settings.perform({action:'reminders',preferences:{globalReset:true}});
 ui.settings.clear();ui.accounts.clear();finish({...ui.value(),reminders:{globalReset:true}});await pending;
 assert.equal(ui.sync.busy,false);assert.equal(ui.accounts.value,null);assert.equal(ui.settings.value,null);assert.equal(ui.writes.length,2);
});

test('desktop form snapshots exclude separately persisted reminder inputs',()=>{
 const excluded={id:'',type:'checkbox',checked:true,closest:selector=>selector==='#notification-account-reminders'?{}:null};
 const common={id:'port',value:'8787',closest(){return null;}};
 const source=fs.readFileSync(path.join(__dirname,'../desktop/shell/renderer.js'),'utf8');
 const functions=source.slice(source.indexOf('function fields()'),source.indexOf('function updateDirty()'));
 const context=vm.createContext({$:()=>({querySelectorAll:()=>[excluded,common]})});vm.runInContext(functions,context);
 assert.equal(vm.runInContext('fields().length',context),1);assert.equal(vm.runInContext("fieldValues().port",context),'8787');
 assert.equal(vm.runInContext("Object.hasOwn(fieldValues(),'')",context),false);
});
