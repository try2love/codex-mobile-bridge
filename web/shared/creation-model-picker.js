'use strict';
// Only catalog data is shared. Selection, permissions and connection state belong
// to the requesting view; an older in-flight read must not undo a newer refresh.
window.BridgeModelCatalogCache={
  key(namespace,identity){return JSON.stringify(['models',...namespace,identity]);},
  read(cache,namespace,identity){
    let value=identity&&cache.get(this.key(namespace,identity));
    if(value?.projection){
      const {directoryIdentity,surface}=value.projection,base=cache.get(this.key(namespace,directoryIdentity));
      if(!base)return null;
      const groups=base.groups||[],group=groups.find(row=>row.surface===surface);
      const models=group?.models||(groups.length&&groups.every(row=>row.surface==='api')?groups.flatMap(row=>row.models||[]):[]);
      value={...base,models,cache:{...base.cache,identity,directoryIdentity,surface}};
    }
    return value?JSON.parse(JSON.stringify(value)):null;
  },
  write(cache,namespace,value){
    const identity=value?.cache?.identity;if(!identity||!Array.isArray(value.models)||value.cache.state==='unavailable')return null;
    const key=this.key(namespace,identity),previous=this.read(cache,namespace,identity),stamp=value.cache.updatedAt;
    const older=previous&&Number.isFinite(previous.cache.updatedAt)&&(!Number.isFinite(stamp)||previous.cache.updatedAt>stamp);
    const {directoryIdentity,surface}=value.cache;
    if(directoryIdentity&&directoryIdentity!==identity&&surface&&Array.isArray(value.groups)&&value.groups.length){
      this.write(cache,namespace,{...value,models:value.groups.flatMap(group=>group.models||[]),cache:{...value.cache,identity:directoryIdentity,directoryIdentity:undefined,surface:undefined}});
      if(older)return previous;
      cache.set(key,{projection:{directoryIdentity,surface}});
      return this.read(cache,namespace,identity);
    }
    if(older)return previous;
    const stable=Object.fromEntries(['models','groups','cache','modelSource','source','modelSourcesVersion'].filter(field=>field in value).map(field=>[field,value[field]]));
    cache.set(key,JSON.parse(JSON.stringify(stable)));return this.read(cache,namespace,identity);
  },
  forScope(cache,scope){const alias=cache.get('create:'+scope);return alias?this.read(cache,alias.namespace,alias.identity):null;},
  bind(cache,scope,namespace,value){const saved=this.write(cache,namespace,value);if(saved)cache.set('create:'+scope,{namespace:[...namespace],identity:value.cache.identity});return saved;},
  forget(cache,namespace){
    const scope=JSON.stringify(namespace);
    for(const [key,value] of cache){
      if(key.startsWith('create:')){if(JSON.stringify(value.namespace)===scope)cache.delete(key);}
      else if(key.startsWith('["models",')&&JSON.stringify(JSON.parse(key).slice(1,-1))===scope)cache.delete(key);
    }
  }
};
// Account-bound catalog shape and choices are shared by all new-chat surfaces.
class CreationModelPicker {
  static intent(value){return Object.fromEntries(Object.entries(value).filter(([key])=>!['modelCatalogIdentity','environment'].includes(key)));}
  constructor({root,cache=new Map(),namespace=()=>[],isCurrent=()=>true,onChange=()=>{}}){
    Object.assign(this,{root,cache,namespace,isCurrent,onChange});this.revision=0;this.selectionRevision=0;this.busy=false;this.catalog=null;this.scope='';
    const node=(tag,text)=>{const result=document.createElement(tag);if(text!==undefined)result.textContent=BridgeI18n.t(text);return result;};this.node=node;
    root.classList.add('project-picker-models');
    const header=node('div'),label=node('strong','模型与思考强度');header.className='project-picker-model-head';
    this.refresh=node('button','刷新模型');this.refresh.type='button';this.refresh.className='plain';this.refresh.onclick=()=>this.load(this.scope,this.read,true);header.append(label,this.refresh);
    this.model=node('select');this.model.setAttribute('aria-label',BridgeI18n.t('模型'));this.effort=node('select');this.effort.setAttribute('aria-label',BridgeI18n.t('思考强度'));
    this.manualModel=node('input');this.manualModel.maxLength=300;this.manualModel.placeholder=BridgeI18n.t('手动输入模型');this.manualModel.setAttribute('aria-label',BridgeI18n.t('手动输入模型'));this.manualModel.hidden=true;
    this.manualEffort=node('input');this.manualEffort.maxLength=64;this.manualEffort.placeholder=BridgeI18n.t('手动输入思考强度');this.manualEffort.setAttribute('aria-label',BridgeI18n.t('手动输入思考强度'));this.manualEffort.hidden=true;
    this.feedback=node('p');this.feedback.className='muted';this.feedback.setAttribute('role','status');this.error=node('p');this.error.className='error';this.error.setAttribute('role','alert');
    const field=(title,input)=>{const wrap=node('label');wrap.append(node('span',title),input);root.append(wrap);return wrap;};
    root.append(header);field('模型',this.model);root.append(this.manualModel);this.effortField=field('思考强度',this.effort);root.append(this.manualEffort,this.feedback,this.error);
    this.model.onchange=()=>{this.selectionRevision++;this.effortValue='';this.renderEffort();this.changed();};this.effort.onchange=()=>{this.selectionRevision++;this.effortValue=this.effort.value;this.changed();};
    this.manualModel.oninput=this.manualEffort.oninput=()=>{this.selectionRevision++;this.changed();};this.show(null);
  }
  option(label,value){const option=this.node('option',label);option.value=value;return option;}
  show(catalog,preserve=false,allowDefault=true){
    const previousModel=this.model.value,previousEffort=this.effortValue;
    this.catalog=catalog;this.rows=Array.isArray(catalog?.models)?catalog.models.filter(row=>row&&typeof row.id==='string'):[];this.capabilities=catalog?.capabilities||{};
    this.model.replaceChildren(this.option('请选择模型',''),...this.rows.map(row=>this.option(row.name||row.id,row.id)),...(this.capabilities.manualModels?[this.option('手动输入模型','__manual__')]:[]));
    const defaults=this.rows.filter(row=>row.isDefault===true),proposed=preserve?previousModel:allowDefault?(catalog?.currentModel||(defaults.length===1?defaults[0].id:'')):'';
    this.model.value=this.rows.some(row=>row.id===proposed)||proposed==='__manual__'&&this.capabilities.manualModels?proposed:'';
    if(!preserve){this.manualModel.value='';this.manualEffort.value='';}
    if(!preserve&&typeof proposed==='string'&&proposed&&!this.model.value&&this.capabilities.manualModels){this.model.value='__manual__';this.manualModel.value=proposed;}
    this.effortValue=preserve?previousEffort:catalog?.currentEffort||'';this.renderEffort();this.changed();
  }
  renderEffort(){
    const selected=this.rows.find(row=>row.id===this.model.value),levels=selected?.efforts||[],manual=this.capabilities.manualEffort===true&&selected?.supportsEffort!==false;
    const proposed=this.effortValue||selected?.defaultEffort;
    this.effort.replaceChildren(this.option(levels.length?'请选择思考强度':'沿用原生默认',''),...levels.map(value=>this.option(value,value)),...(manual?[this.option('手动输入思考强度','__manual__')]:[]));
    this.effort.value=levels.includes(proposed)||proposed==='__manual__'&&manual?proposed:'';
    if(!this.effort.value&&manual&&proposed&&proposed===this.catalog?.currentEffort){this.effort.value='__manual__';this.manualEffort.value=proposed;}
    this.effortValue=this.effort.value;this.effortField.hidden=!levels.length&&!manual;
  }
  changed(){
    this.manualModel.hidden=this.model.value!=='__manual__';this.manualEffort.hidden=this.effort.value!=='__manual__';
    this.refresh.disabled=this.busy||this.loading||!this.read;for(const control of [this.model,this.effort,this.manualModel,this.manualEffort])control.disabled=this.busy||!this.catalog;
    this.onChange();
  }
  valid(){
    if(!this.catalog?.cache?.identity||this.capabilities.models===false||!this.model.value)return false;
    if(this.model.value==='__manual__'&&!this.manualModel.value.trim())return false;
    const selected=this.rows.find(row=>row.id===this.model.value);
    if(selected?.efforts?.length&&!this.effort.value)return false;
    return this.effort.value!=='__manual__'||!!this.manualEffort.value.trim();
  }
  selection(){
    if(!this.valid())throw Error(BridgeI18n.t('请先选择新会话的模型与思考强度'));
    const result={model:this.model.value==='__manual__'?this.manualModel.value.trim():this.model.value,modelCatalogIdentity:this.catalog.cache.identity};
    if(this.model.value==='__manual__')result.manualModel=true;
    if(this.effort.value){result.effort=this.effort.value==='__manual__'?this.manualEffort.value.trim():this.effort.value;if(this.effort.value==='__manual__')result.manualEffort=true;}
    return result;
  }
  async load(scope,read,refresh=false){
    if(this.busy||!this.isCurrent())return;
    if(scope===this.scope&&this.read&&(!refresh||this.loading))return;
    const changed=scope!==this.scope;this.scope=scope;this.read=read;const revision=++this.revision;
    const namespace=this.namespace();
    if(changed)this.show(window.BridgeModelCatalogCache.forScope(this.cache,scope)||{models:[],capabilities:{manualModels:true,manualEffort:true}});
    const selectionRevision=this.selectionRevision;
    this.loading=true;this.error.textContent='';this.feedback.textContent=BridgeI18n.t(this.catalog?'正在核对模型目录…':'正在读取模型…');this.changed();
    try{
      let value=await read(refresh);if(revision!==this.revision||!this.isCurrent())return;
      value={...value,...window.BridgeModelCatalogCache.bind(this.cache,scope,namespace,value)};
      const identityChanged=!!this.catalog?.cache?.identity&&this.catalog.cache.identity!==value.cache?.identity;
      // Cached choices are usable while checking the catalog; that read must not
      // replace a user's newer selection with the original client's default.
      const preserve=!identityChanged&&(!changed||selectionRevision!==this.selectionRevision);
      this.show(value,preserve,!identityChanged);
      this.feedback.textContent=BridgeI18n.t(identityChanged?'账号或配置已变化，请重新选择模型与思考强度':value.notice||value.modelError||(!value.currentModel&&!this.model.value?'请选择模型；不会自动选用目录中的第一个模型。':''));
    }catch(error){if(revision===this.revision&&this.isCurrent())this.feedback.textContent=BridgeI18n.t(error.message)+' · '+BridgeI18n.t('可手动填写模型');}
    finally{if(revision===this.revision&&this.isCurrent()){this.loading=false;this.changed();}}
  }
  clear(){this.revision++;this.scope='';this.read=null;this.loading=false;this.feedback.textContent='';this.error.textContent='';this.show(null);}
  setBusy(value){this.busy=value;this.changed();}
  dispose(){this.revision++;this.read=null;}
}
window.BridgeCreationModelPicker=CreationModelPicker;
