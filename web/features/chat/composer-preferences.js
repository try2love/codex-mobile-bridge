'use strict';

const composerTools = Object.freeze([
  {id:'more', label:'工具面板', zone:'topLeft'},
  {id:'skills', label:'Skill', zone:'more', addMenu:true},
  {id:'permissions', label:'权限', zone:'topLeft'},
  {id:'context', label:'上下文使用量', shortLabel:'上下文', zone:'topRight'},
  {id:'collapse', label:'收起输入区', zone:'topRight'},
  {id:'attachments', label:'添加文件或图片', shortLabel:'附件', zone:'bottomLeft'},
  {id:'model', label:'模型与思考强度', shortLabel:'模型·强度', zone:'bottomLeft', display:'text'},
  {id:'sendMode', label:'发送方式', zone:'more', display:'text'},
  {id:'plan', label:'计划模式', zone:'more'},
  {id:'goal', label:'目标模式', zone:'more'},
].map(Object.freeze));
const composerSubmitTool=Object.freeze({id:'submit',label:'发送按钮',display:'both'});
const composerToolIds = Object.freeze(composerTools.map(tool=>tool.id));
const composerZones = Object.freeze(['topLeft','topRight','bottomLeft','bottomRight','more']);
const composerPreferencesKey = 'bridge-composer-preferences';
const composerSendModes = Object.freeze(['send','queue','steer']);

function defaultComposerLayout(){
  return Object.fromEntries(composerZones.map(zone=>[zone,composerTools.filter(tool=>tool.zone===zone).map(tool=>tool.id)]));
}
function composerPreset(id){
  const layout=defaultComposerLayout(),display=normalizeComposerDisplay();
  if(id==='single'){
    layout.topLeft=[];layout.topRight=[];layout.bottomLeft=['more','model'];layout.bottomRight=['context','attachments'];
    layout.more=['permissions','collapse','skills','sendMode','plan','goal'];display.collapse='hidden';
  }else if(id!=='default')throw new Error('Unknown composer preset');
  return {layout,display};
}
function normalizeComposerLayout(value){
  if(!value || !composerZones.every(zone=>Array.isArray(value[zone])))return defaultComposerLayout();
  const seen=new Set();
  const layout=Object.fromEntries(composerZones.map(zone=>[zone,value[zone].filter(id=>{
    if(!composerToolIds.includes(id)||seen.has(id)||id==='more'&&zone==='more')return false;
    seen.add(id);return true;
  })]));
  for(const id of composerToolIds)if(!seen.has(id)){if(id==='more')layout.topLeft.unshift(id);else layout.more.push(id);}
  return layout;
}
function normalizeComposerDisplay(value){
  return Object.fromEntries([...composerTools,composerSubmitTool].map(tool=>[tool.id,(['icon','text','both'].includes(value?.[tool.id])||value?.[tool.id]==='hidden'&&!['more','submit'].includes(tool.id))?value[tool.id]:tool.display||'icon']));
}
function normalizeComposerPreferences(value){
  const layout=normalizeComposerLayout(value?.layout),display=normalizeComposerDisplay(value?.display);
  for(const id of composerToolIds)if(display[id]==='hidden'){
    for(const zone of composerZones)if(zone!=='more')layout[zone]=layout[zone].filter(tool=>tool!==id);
    if(!layout.more.includes(id))layout.more.push(id);
  }
  for(const zone of composerZones)Object.freeze(layout[zone]);
  return Object.freeze({layout:Object.freeze(layout),display:Object.freeze(display),
    followUpMode:composerSendModes.includes(value?.followUpMode)?value.followUpMode:'queue'});
}

function moveComposerTool(value,id,zone,index){
  if(!composerToolIds.includes(id)||!composerZones.includes(zone)||id==='more'&&zone==='more')return value;
  const layout=normalizeComposerLayout(value.layout);
  const display=normalizeComposerDisplay(value.display);
  for(const key of composerZones)layout[key]=layout[key].filter(tool=>tool!==id);
  layout[zone].splice(Math.max(0,Math.min(index,layout[zone].length)),0,id);
  if(zone!=='more'&&display[id]==='hidden')display[id]=normalizeComposerDisplay()[id];
  return {...value,layout,display};
}

// The preference survives idle periods; the effective action always follows the live runtime.
function deriveComposerAction(state,preferred='queue'){
  const available=state.availableModes||composerSendModes;
  const mode=!state.running?'send':available.includes(preferred)?preferred:composerSendModes.filter(value=>value!=='send').find(value=>available.includes(value))||'send';
  const stop=!!state.running&&!state.hasDraft;
  return {mode,stop,choices:!!state.running&&!!state.hasDraft,
    label:state.sending?'正在发送…':!state.running?'发送':mode==='queue'?'排队':mode==='steer'?'补充任务':'发送',
    sendDisabled:!!state.pending||state.sendEnabled===false||!state.hasDraft||stop||!available.includes(mode),
    stopDisabled:!state.running||!!state.pending||state.stopEnabled===false,
    choicesDisabled:!!state.pending||state.modeEnabled===false};
}

class ComposerPreferences {
  static shared(){
    if(!this.instance){let storage;try{storage=window.localStorage;}catch{}
      this.instance=new ComposerPreferences(storage,window);}
    return this.instance;
  }
  constructor(storage,events){
    this.storage=storage;this.listeners=new Set();this.value=this.read();
    this.onStorage=event=>{if(event.key===composerPreferencesKey||event.key===null){this.value=this.read();this.notify();}};
    this.events=events;events?.addEventListener('storage',this.onStorage);
  }
  read(){try{return normalizeComposerPreferences(JSON.parse(this.storage?.getItem(composerPreferencesKey)));}catch{return normalizeComposerPreferences();}}
  subscribe(listener){this.listeners.add(listener);return ()=>this.listeners.delete(listener);}
  notify(){for(const listener of this.listeners)listener(this.value);}
  update(patch){
    const next=normalizeComposerPreferences({...this.value,...patch});
    if(JSON.stringify(next)===JSON.stringify(this.value))return;
    try{if(!this.storage)throw Error();this.storage.setItem(composerPreferencesKey,JSON.stringify(next));}
    catch{throw new Error('工具栏设置保存失败，请检查浏览器存储权限。');}
    this.value=next;this.notify();
  }
  moveTool(id,zone,index){this.update(moveComposerTool(this.value,id,zone,index));}
  applyPreset(id){this.update(composerPreset(id));}
  resetLayout(){this.applyPreset('default');}
  dispose(){this.events?.removeEventListener('storage',this.onStorage);this.listeners.clear();}
}
if(typeof module!=='undefined')module.exports={composerTools,composerToolIds,composerZones,composerPreferencesKey,composerPreset,defaultComposerLayout,normalizeComposerLayout,normalizeComposerDisplay,normalizeComposerPreferences,moveComposerTool,deriveComposerAction,ComposerPreferences};
