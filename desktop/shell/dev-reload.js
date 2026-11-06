'use strict';
(function(scope){
  function create(api,{window:win,document:doc,setPoll=setInterval,clearPoll=clearInterval,now=Date.now}={}){
    if(!api)return null;
    let hooks={},pending=null,reloading=false,closed=false,initialized=false,view=null,lastInput=0,styles=Promise.resolve();
    const edited=new WeakSet(),badge=doc.createElement('div');
    badge.id='dev-reload-status';badge.setAttribute('role','status');doc.body.append(badge);
    const status=()=>{badge.textContent=pending?'DEV UI - refresh queued; save or close edits':'DEV UI - live';};
    function input(event){lastInput=now();if(event.target&&!hooks.managedInput?.(event.target))edited.add(event.target);}
    doc.addEventListener('input',input,true);doc.addEventListener('change',input,true);
    function blocked(){
      if(!initialized||closed||reloading||doc.hidden||api.busy()||hooks.ready?.()===false||hooks.dirty?.())return true;
      if(doc.querySelector('dialog[open],[aria-busy="true"]')||doc.activeElement?.matches?.('input,textarea,select,[contenteditable="true"]'))return true;
      if(lastInput&&now()-lastInput<600)return true;
      return [...doc.querySelectorAll('input,textarea,select,[contenteditable="true"]')].some(node=>
        edited.has(node)&&!node.closest?.('dialog:not([open])')&&!hooks.managedInput?.(node)&&(node.type==='checkbox'||node.type==='radio'||(node.isContentEditable?node.textContent:node.value)));
    }
    function refreshStyles(revision){
      return Promise.all([...doc.querySelectorAll('link[rel="stylesheet"]')].map(link=>new Promise(resolve=>{
        const next=link.cloneNode(),url=new URL(link.href);url.searchParams.set('dev-ui',String(revision));next.href=url.href;
        next.onload=()=>{link.remove();resolve();};
        next.onerror=()=>{next.remove();win.console?.warn('[dev-ui] Stylesheet reload failed; keeping previous styles.');resolve();};
        link.after(next);
      })));
    }
    function change(value){
      if(closed||!value||!Number.isInteger(value.revision)||!Array.isArray(value.files))return;
      if(value.files.some(file=>file.endsWith('.css')))styles=styles.then(()=>refreshStyles(value.revision));
      if(!value.stylesOnly){pending=value;reloading=false;status();}
    }
    async function flush(){
      if(!pending||blocked())return;
      const revision=pending.revision;reloading=true;
      try{
        const result=await api.reload({revision,view:hooks.view?.()||{tab:'overview',scroll:0,details:[]}});
        if(result?.accepted!==true)reloading=false;
      }catch{reloading=false;}
    }
    const off=api.onChange(change),timer=setPoll(flush,500);
    const controller={
      reloaded:false,
      initialize:Promise.resolve(api.state()).then(state=>{
        controller.reloaded=state.reloaded===true;view=state.view;initialized=true;
        if(state.pending)change(state.pending);
      }).catch(error=>{win.console?.warn('[dev-ui] Development reload unavailable.',error);}),
      attach(value){hooks=value;},
      async restore(){await controller.initialize;if(view)hooks.restore?.(view);},
      flush,
      close(){closed=true;clearPoll(timer);off();doc.removeEventListener('input',input,true);doc.removeEventListener('change',input,true);badge.remove();}
    };
    win.addEventListener('pagehide',()=>controller.close(),{once:true});status();
    return controller;
  }
  if(typeof module!=='undefined'&&module.exports)module.exports={create};
  else scope.sourceDevelopmentReload=create(scope.bridgeDesktop?.development,{window:scope,document:scope.document});
})(typeof window==='undefined'?globalThis:window);
