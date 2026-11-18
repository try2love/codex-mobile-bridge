'use strict';
// One API editor and draft-model lifecycle for the desktop and phone account panels.
const ApiAccessEditor=(()=>{
  const MAX_MODELS=200;
  let sequence=0;
  function mount(owner,{connectionFields,actions,caption='默认模型',mode='single'}={}){
    owner.root.classList.add('api-access-editor');owner.draftModelMode=mode;
    const connection=owner.node('div',null,'api-access-connection'),models=owner.node('div',null,'api-access-models');
    owner.model.label.children[0]?.remove();owner.model.input.id='api-access-model-'+(++sequence);
    owner.model.label.htmlFor=owner.model.input.id;
    owner.modelSelect=owner.node('select');owner.modelSelect.id=owner.model.input.id+'-select';owner.modelSelect.hidden=true;
    owner.modelSelect.setAttribute('aria-label',owner.t(caption));
    owner.modelSelect.onchange=()=>{
      if(mode==='multiple')owner.model.input.value=[...new Set([...values(owner),owner.modelSelect.value].filter(Boolean))].join('\n');
      else if(mode!=='readonly')owner.model.input.value=owner.modelSelect.value;
      owner.draftAutoModel=null;
      updateAddAll(owner);
    };
    owner.model.input.oninput=()=>{owner.draftAutoModel=null;updateAddAll(owner);};
    owner.fetchModels=owner.button('获取上游模型',()=>discover(owner));owner.fetchModels.className='api-access-fetch';
    owner.addAllModels=owner.button('添加全部模型',()=>addAllModels(owner));owner.addAllModels.className='api-access-fetch';owner.addAllModels.hidden=true;
    owner.modelFeedback=owner.node('p',null,'account-muted api-access-model-feedback');owner.modelFeedback.setAttribute('role','status');
    owner.manualModel=owner.button('手动填写',()=>{const selected=owner.modelSelect.hidden;if(selected&&mode==='single')owner.model.input.value=owner.modelSelect.value;owner.draftAutoModel=null;selection(owner,selected);});owner.manualModel.className='api-access-fetch';owner.manualModel.hidden=true;
    const head=owner.node('div',null,'api-access-model-head'),modelActions=owner.node('div',null,'api-access-model-actions'),footer=owner.node('div',null,'api-access-model-footer');
    owner.modelCaption=owner.node('label',caption);owner.modelCaption.htmlFor=owner.model.input.id;
    modelActions.append(owner.fetchModels,owner.addAllModels);head.append(owner.modelCaption,modelActions);footer.append(owner.modelFeedback,owner.manualModel);
    models.append(head,owner.model.label,owner.modelSelect,footer);connection.append(...connectionFields);
    owner.form.append(connection,models,actions);owner.modelSection=models;owner.connectionSection=connection;
    selection(owner,false);
    return owner;
  }
  function values(owner){return owner.model.input.value.split(/\r?\n/).map(value=>value.trim()).filter(Boolean);}
  function updateAddAll(owner){
    if(!owner.addAllModels)return;
    const available=[...new Set((owner.availableModels||[]).map(model=>model.trim()).filter(Boolean))],selected=new Set(values(owner));
    const remaining=available.filter(model=>!selected.has(model));
    const overLimit=selected.size+remaining.length>MAX_MODELS;
    owner.addAllModels.hidden=owner.draftModelMode!=='multiple'||!available.length;
    owner.addAllModels.disabled=!!owner.busy||!!owner.draftLoading||!remaining.length||overLimit;
    owner.addAllModels.textContent=owner.t(remaining.length?'添加全部模型':'全部模型已添加');
    owner.addAllModels.title=owner.t(overLimit?'全部模型超过 200 个上限，请减少现有模型或手动选择。':'');
  }
  function addAllModels(owner){
    if(owner.busy||owner.draftLoading||!Array.isArray(owner.availableModels))return;
    const combined=[...new Set([...values(owner),...owner.availableModels])];
    if(combined.length>MAX_MODELS){owner.modelFeedback.textContent=owner.t('全部模型超过 200 个上限，请减少现有模型或手动选择。');return;}
    owner.model.input.value=combined.join('\n');owner.draftAutoModel=null;
    owner.modelFeedback.textContent=owner.t('已将全部可用模型添加到配置。');updateAddAll(owner);
  }
  function selection(owner,selected){
    const mode=owner.draftModelMode||'single';
    owner.modelSelect.hidden=!selected;
    owner.model.input.hidden=mode==='readonly'||mode==='single'&&selected;
    owner.model.label.hidden=mode==='readonly';
    if(owner.model.input.id){owner.model.label.htmlFor=selected&&mode==='single'?owner.modelSelect.id:owner.model.input.id;owner.modelCaption.htmlFor=owner.model.label.htmlFor;}
    owner.manualModel.textContent=owner.t(selected?'手动填写':'从列表选择');
    if(mode==='readonly')owner.manualModel.hidden=true;
  }
  function clear(owner){
    owner.draftRevision=(owner.draftRevision||0)+1;owner.draftLoading=false;
    if(!owner.modelSelect)return;
    if(owner.draftAutoModel&&owner.model.input.value===owner.draftAutoModel&&owner.draftModelMode!=='legacy')owner.model.input.value='';
    owner.draftAutoModel=null;owner.save.disabled=!!owner.busy;
    owner.modelSelect.replaceChildren();selection(owner,false);owner.manualModel.hidden=true;
    owner.availableModels=[];
    if(owner.addAllModels){owner.addAllModels.hidden=true;owner.addAllModels.disabled=true;owner.addAllModels.textContent=owner.t('添加全部模型');owner.addAllModels.title='';}
    owner.modelFeedback.textContent='';owner.fetchModels.textContent=owner.t('获取上游模型');owner.fetchModels.disabled=!!owner.busy;
  }
  async function discover(owner){
    if(owner.busy||owner.draftLoading)return;
    const revision=owner.draftRevision||0,context=owner.draftModelContext||(()=>JSON.stringify([owner.editId,owner.baseUrl.input.value,owner.key.input.value])),started=context();
    const request=owner.draftModelRequest?owner.draftModelRequest():{provider:'codex',action:'modelsDraft',baseUrl:owner.baseUrl.input.value,apiKey:owner.key.input.value,...(owner.editId?{id:owner.editId}:{})};
    owner.onDraftStart?.();
    owner.availableModels=[];owner.draftLoading=true;owner.save.disabled=true;owner.fetchModels.disabled=true;owner.fetchModels.textContent=owner.t('正在获取…');owner.modelFeedback.textContent='';updateAddAll(owner);
    try{
      const result=await owner.request(request);
      if(revision!==(owner.draftRevision||0)||started!==context())return;
      const models=Array.isArray(result.models)?[...new Set(result.models.filter(id=>typeof id==='string').map(id=>id.trim()).filter(Boolean))]:[];
      if(!models.length)throw Error('上游未返回兼容的模型列表，请手动填写模型 ID');
      owner.availableModels=models;
      const mode=owner.draftModelMode||'single',choices=owner.availableModels.map(id=>{const option=owner.node('option');option.value=id;option.textContent=id;return option;});
      if(mode==='multiple'||mode==='legacy'){const option=owner.node('option','请选择模型');option.value='';choices.unshift(option);}
      owner.modelSelect.replaceChildren(...choices);
      if(!owner.model.input.value.trim()&&mode!=='readonly'){owner.model.input.value=owner.availableModels[0];owner.draftAutoModel=owner.availableModels[0];}
      const selected=owner.availableModels.includes(owner.model.input.value);if(selected)owner.modelSelect.value=owner.model.input.value;
      selection(owner,mode==='single'?selected:true);owner.manualModel.hidden=mode==='readonly'||owner.draftManualToggle===false;
      owner.modelFeedback.textContent=owner.t(owner.draftModelSuccess||(mode==='readonly'?'仅核对模型列表，未发送对话请求。':mode==='multiple'?'已获取模型，可选择添加或手动填写。':'已获取模型，可修改默认选择。'));
      updateAddAll(owner);
    }catch(error){if(revision===(owner.draftRevision||0)&&started===context()){if(owner.onDraftError)owner.onDraftError(error);else owner.modelFeedback.textContent=owner.t(error.message);}}
    finally{if(Object.hasOwn(request,'apiKey'))request.apiKey='';if(revision===(owner.draftRevision||0)){owner.draftLoading=false;owner.save.disabled=!!owner.busy;owner.fetchModels.disabled=!!owner.busy;owner.fetchModels.textContent=owner.t('获取上游模型');}}
  }
  function management(root,node){const section=node('div',undefined,'accounts-management');root.append(section);return section;}
  function section(parent,node,label){const details=node('details');details.append(node('summary',label));parent.append(details);return details;}
  return {mount,selection,clear,discover,addAllModels,management,section};
})();
if(typeof module!=='undefined')module.exports={ApiAccessEditor};
