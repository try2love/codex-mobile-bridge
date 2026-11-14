// Synthetic state transitions through the production shared composer. No client
// or model request is permitted by this fixture.
async function runSharedComposerLoadingChecks({keepOpen=false}={}){
  const checks=[],check=(value,label)=>{if(!value)throw Error(label);checks.push(label);};
  document.getElementById('login').hidden=true;
  const root=document.createElement('div');root.className='app desktop-mode';root.style.cssText='position:fixed;inset:0;background:var(--page);z-index:100';document.body.append(root);
  const select=document.createElement('select');let requests=0;
  const view=new DesktopSessionsView({root,select,notify(){},onSelect(){},csrf:()=>'',onUnauthorized(){},request:async()=>{requests++;throw Error('Unexpected fixture request');}});
  const frame=()=>new Promise(requestAnimationFrame),controls=()=>[view.modelButton,view.skillsButton,view.permissionsButton,view.contextButton,view.attachButton,view.mode,view.workMode,view.input,view.send];
  const visible=control=>!control.hidden&&getComputedStyle(control).display!=='none'&&control.getBoundingClientRect().width>0;
  const fit=label=>{const rect=view.form.getBoundingClientRect();check(rect.width>0&&rect.left>=-1&&rect.right<=innerWidth+1,label+' composer fits viewport');check(view.mode.getBoundingClientRect().width>=65,label+' send mode keeps its full grid column');check(view.workMode.getBoundingClientRect().width>=65,label+' work mode keeps its full grid column');check(view.send.getBoundingClientRect().right<=rect.right+1,label+' send button fits');};
  try{
    view.container.hidden=false;
    for(const provider of ['claude','deepseek','zcode','antigravity']){
      view.provider=provider;view.clientStates=[{id:provider,enabled:true,connected:true,historyAvailable:true}];view.rows=[{id:'synthetic',title:'聊天',model:'GLM-5.3-Flash'}];view.prepareChat('synthetic');await frame();
      const original=controls();check(original.every(visible),provider+' loading keeps every shared slot');check(view.modelButton.disabled&&view.skillsButton.disabled&&view.permissionsButton.disabled&&view.attachButton.disabled&&view.workMode.disabled&&view.send.disabled,provider+' unknown capabilities remain disabled');check(view.skillsButton.dataset.capabilityState==='unknown',provider+' unknown is explicit');fit(provider+' loading');
      view.input.value='保留离线草稿';view.attachments.rows.push({id:'saved',status:'ready',name:'saved.png'});view.attachments.render();
      const detail=capabilities=>({session:{id:'synthetic',title:'聊天',status:'idle',connectionState:'ready',model:'GLM-5.3-Flash'},capabilities,messages:[]});
      view.applyDetail(detail({send:true,models:false,skills:false,permissions:false,attachments:false,projectReferences:false,plan:false,goal:false}));await frame();
      check(controls().every((control,index)=>control===original[index]&&visible(control)),provider+' unsupported retains the same controls');check(view.skillsButton.disabled&&view.skillsButton.dataset.capabilityState==='unsupported',provider+' unsupported differs from unknown');fit(provider+' unsupported');
      view.attachButton.hidden=true;await frame();fit(provider+' hidden attachment regression');view.attachButton.hidden=false;
      view.applyDetail(detail({send:true,models:true,skills:true,permissions:true,attachments:true,plan:true,goal:true}));await frame();
      check([view.modelButton,view.skillsButton,view.permissionsButton,view.attachButton,view.workMode].every(control=>!control.disabled),provider+' verified capabilities enable existing controls');
      check(view.input.value==='保留离线草稿'&&view.attachments.rows[0].id==='saved',provider+' reconnect keeps draft and attachment');
      view.staleDetail();await frame();check(controls().every(visible)&&view.send.disabled&&view.attachButton.disabled,provider+' disconnect keeps disabled complete layout');fit(provider+' disconnected');
      view.attachments.rows.splice(0);
    }
    check(requests===0,'All transitions make zero client or model requests');
    return {checks:checks.length,width:innerWidth,language:BridgeI18n.language(),requests};
  }finally{clearTimeout(view.timer);if(!keepOpen)root.remove();else window.__sharedComposerPreview={view,cleanup:()=>root.remove()};}
}

// Incremental review checks: real Codex markup and the two mixed-capability
// cases missing from the original four-client matrix. Never submit a chat.
async function runComposerReviewRegressionChecks({keepOpen=false,nativeDetails=null}={}){
  const checks=[],check=(ok,label)=>{if(!ok)throw Error(label);checks.push(label);};
  const frame=()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
  const original=document.getElementById('composer'),marker=document.createComment('Codex composer fixture');original.replaceWith(marker);
  const root=document.createElement('section');root.style.cssText='position:fixed;inset:0;display:flex;flex-direction:column;justify-content:flex-end;background:var(--page);z-index:100';
  const form=original.cloneNode(true);form.classList.remove('is-collapsed');form.querySelector('#composer-body').hidden=false;form.querySelectorAll('button,input,textarea,select').forEach(control=>control.disabled=true);root.append(form);document.body.append(root);
  const mode=form.querySelector('#send-mode'),workMode=form.querySelector('#work-mode'),attachment=form.querySelector('#attach-button');mode.hidden=workMode.hidden=false;
  let view,requests=0;
  try{
    for(const hidden of [false,true]){
      attachment.hidden=hidden;await frame();
      const send=mode.getBoundingClientRect(),work=workMode.getBoundingClientRect(),bounds=form.getBoundingClientRect();
      check(getComputedStyle(mode).gridColumnStart==='2'&&getComputedStyle(workMode).gridColumnStart==='3','Codex selects retain distinct grid columns with attachment hidden='+hidden);
      check(Math.abs(send.top-work.top)<1&&send.right<=work.left+1,'Codex selects do not overlap or wrap with attachment hidden='+hidden);
      check(send.width>=65&&work.width>=65,'Codex selects keep full column width with attachment hidden='+hidden);
      check(bounds.left>=-1&&bounds.right<=innerWidth+1&&form.querySelector('#send').getBoundingClientRect().right<=bounds.right+1,'Codex send row fits viewport with attachment hidden='+hidden);
    }
    const shared=document.createElement('div');shared.className='app desktop-mode';shared.hidden=true;root.append(shared);
    view=new DesktopSessionsView({root:shared,select:document.createElement('select'),notify(){},onSelect(){},csrf:()=>'',onUnauthorized(){},request:async()=>{requests++;throw Error('Unexpected fixture request');}});view.provider='claude';view.sid='review';view.clientStates=[{id:'claude',enabled:true,connected:true}];view.attachments.open('review');
    const detail=capabilities=>({session:{id:'review',title:'Review',status:'idle',cwd:'/project'},capabilities:{send:true,...capabilities},messages:[]});
    for(const attachments of [undefined,false]){view.applyDetail(detail({attachments,goal:false}));check(view.attachButton.disabled&&view.attachButton.dataset.capabilityState==='unknown','cwd cannot authorize undeclared references with uploads='+attachments);check(view.workMode.disabled&&view.workMode.dataset.capabilityState==='unknown','unknown plan plus unsupported goal remains unknown');check(view.workbench.attachReference({kind:'file',name:'a',path:'a',absolutePath:'/project/a'})===false,'undeclared reference cannot enter draft');}
    view.applyDetail(detail({attachments:false,projectReferences:false,plan:false,goal:false}));check(view.attachButton.dataset.capabilityState==='unsupported'&&view.workMode.dataset.capabilityState==='unsupported','both explicit negatives remain unsupported');
    view.applyDetail(detail({projectReferences:true,plan:true,goal:false}));check(!view.attachButton.disabled&&view.attachButton.dataset.capabilityState==='supported'&&!view.workMode.disabled&&view.workMode.dataset.capabilityState==='supported','explicit capability enables only existing controls');
    view.applyDetail({session:{id:'review',title:'Review',status:'active'},capabilities:{},messages:[]});check(view.send.disabled&&view.mode.disabled&&view.mode.options[1].disabled&&view.mode.options[2].disabled&&!view.input.disabled,'fresh unknown sending capabilities remain disabled while draft input is editable');
    if(nativeDetails)for(const [index,detail] of nativeDetails.entries()){view.sid=detail.session.id;view.applyDetail(detail);check(view.send.disabled===(index!==0)&&view.attachButton.disabled===(index!==0),'actual manager native detail controls send and attachments '+index);check(view.attachButton.dataset.capabilityState===(index===0?'supported':'unsupported'),'actual manager native detail retains reference capability '+index);}
    check(requests===0,'Incremental checks make zero client or model requests');
    return {checks:checks.length,width:innerWidth,language:BridgeI18n.language(),requests};
  }finally{if(view)clearTimeout(view.timer);if(keepOpen)window.__composerReviewPreview={cleanup:()=>{root.remove();marker.replaceWith(original);}};else{root.remove();marker.replaceWith(original);}}
}
