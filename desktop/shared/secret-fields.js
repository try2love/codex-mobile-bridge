'use strict';
const savedSecretValues=new Map();
let credentialDirectory='',credentialLoad;
function secretControl(input){
  const wrap=document.createElement('div');wrap.className='secret-control';
  input.parentNode.insertBefore(wrap,input);wrap.append(input);
  const eye=document.createElement('button');eye.type='button';eye.className='secret-eye';
  eye.innerHTML='<svg viewBox="0 0 24 24" width="19" height="19" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/></svg>';
  function label(){eye.setAttribute('aria-label',t(input.type==='password'?'显示凭据':'隐藏凭据'));eye.setAttribute('aria-pressed',String(input.type!=='password'));eye.title=t(input.type==='password'?'显示凭据':'隐藏凭据');}
  eye.onclick=()=>{input.type=input.type==='password'?'text':'password';label();};label();wrap.append(eye);
}
function submittedSecret(id){return $(id).value===savedSecretValues.get(id)?'':$(id).value;}
async function loadSavedSecrets(force=false){
  if(!api.readCredentials||!snapshot)return;
  if(credentialDirectory===snapshot.dataDir&&!force)return credentialLoad;
  const directory=snapshot.dataDir;credentialDirectory=directory;
  const ids={password:'password','ntfy-token':'token','bark-key':'barkKey','pushplus-token':'pushplusToken'};
  const before=Object.fromEntries(Object.keys(ids).map(id=>[id,savedFields[id]]));
  credentialLoad=(async()=>{
    try{
      const values=await api.readCredentials({});if(snapshot.dataDir!==directory)return;
      for(const [id,key] of Object.entries(ids)){
        // Changed gateway passwords are one-way hashes; retain this session's value only.
        const value=id==='password'&&!values[key]?(savedSecretValues.get(id)||''):values[key]||'';
        savedSecretValues.set(id,value);
        if($(id).value===before[id])$(id).value=value;
        savedFields[id]=value;
      }
      if(!$('password').value){$('password').placeholder=t('已设置；旧密码无法查看，可填写新密码');$('password').setAttribute('data-i18n-placeholder','已设置；旧密码无法查看，可填写新密码');}
      updateDirty();
    }catch(error){feedback(error.message,true);}
  })();return credentialLoad;
}
function resetSecretFields(){
  savedSecretValues.clear();connectionSecretDraft.clear();connectionSetupState.clear();credentialDirectory='';
  for(const id of ['password','ntfy-token','bark-key','pushplus-token']){$(id).value='';$(id).type='password';}
}
