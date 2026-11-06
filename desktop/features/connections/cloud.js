'use strict';

(() => {
  const officialOrigin='https://bridge.waken.cloudns.be:8787';
  const api=window.bridgeDesktop,t=BridgeI18n.t,root=document.createElement('article'),dialog=document.createElement('dialog');
  root.id='cloud-account';
  root.innerHTML=`<div class="section-head"><h2 data-i18n="Cloud Relay">Cloud Relay</h2><button type="button" class="primary" data-cloud-open-login data-i18n="Sign In to Cloud">Sign In to Cloud</button></div>
    <p class="hint" data-cloud-signin-hint data-i18n="Sign in to use the cloud relay. Your direct connections are unchanged.">Sign in to use the cloud relay. Your direct connections are unchanged.</p>
    <p data-cloud-state role="status"></p>
    <div data-cloud-account hidden><p data-cloud-identity></p>
    <div class="actions"><button type="button" data-cloud-action="entitlement" data-i18n="Refresh Entitlement">Refresh Entitlement</button><button type="button" data-cloud-action="logout" data-i18n="Log Out">Log Out</button></div>
    <p data-cloud-entitlement></p><div data-cloud-redeem hidden><label for="cloud-service-days-code" data-i18n="Service-days redemption code">Service-days redemption code</label><div class="cloud-field-row"><input id="cloud-service-days-code" data-cloud-code autocomplete="off" maxlength="128"><button type="button" data-cloud-action="redeem" data-i18n="Redeem">Redeem</button></div></div>
    <details class="module-setting" data-cloud-directory><summary><span data-i18n="Cloud Devices">Cloud Devices</span><span class="module-status" data-cloud-device-count></span></summary><div class="cloud-directory-body"><p class="hint" data-i18n="Phones signed in to this account appear automatically. Allow each phone to connect on this desktop.">Phones signed in to this account appear automatically. Allow each phone to connect on this desktop.</p>
    <p data-cloud-directory-error class="hint" role="status"></p><div data-cloud-devices></div></div></details></div>
    <p data-cloud-result role="status"></p>`;
  dialog.id='cloud-login-dialog';dialog.className='cloud-login-dialog';
  dialog.setAttribute('aria-labelledby','cloud-login-title');dialog.setAttribute('aria-describedby','cloud-login-help');
  dialog.innerHTML=`<div class="cloud-login-head"><h2 id="cloud-login-title">Sign In to Cloud</h2><button type="button" data-cloud-close-login data-close="cloud-login-dialog" data-i18n="Close">Close</button></div>
    <p id="cloud-login-help" class="hint"></p>
    <form data-cloud-login-form>
      <label><span data-i18n="Cloud server">Cloud server</span><select data-cloud-server><option value="official" data-i18n="Official Cloud">Official Cloud</option><option value="custom" data-i18n="Custom Server">Custom Server</option></select></label>
      <label data-cloud-custom-origin hidden><span data-i18n="Exact HTTPS server origin">Exact HTTPS server origin</span><input data-cloud-origin type="url" placeholder="https://relay.example.com:8443" autocomplete="off" maxlength="2048" required></label>
      <label><span data-cloud-login-label></span><input data-cloud-login autocomplete="username" maxlength="320" required></label>
      <label data-cloud-email-code-field hidden><span data-i18n="Email verification code">Email verification code</span><div class="cloud-field-row"><input data-cloud-email-code type="text" inputmode="numeric" autocomplete="one-time-code" pattern="[0-9]{6}" minlength="6" maxlength="6" required><button type="button" data-cloud-resend-code data-cloud-action="signup-code">Get code</button></div></label>
      <div data-cloud-password-field class="cloud-password-field"><div class="cloud-field-heading"><label for="cloud-login-password" data-cloud-password-label></label><button type="button" class="cloud-auth-link" data-cloud-view="forgot" data-cloud-forgot-link data-i18n="Forgot password?">Forgot password?</button></div><input id="cloud-login-password" data-cloud-password type="password" autocomplete="current-password" maxlength="1024" required></div>
      <p data-cloud-password-help class="hint cloud-password-help" data-i18n="Use at least 12 characters for your new password." hidden>Use at least 12 characters for your new password.</p>
      <label data-cloud-confirm-field hidden><span data-i18n="Confirm password">Confirm password</span><input data-cloud-password-confirm type="password" autocomplete="new-password" maxlength="1024" required></label>
      <p data-cloud-login-result class="cloud-login-result" role="status" aria-live="polite"></p>
      <button type="submit" data-cloud-action="login" data-cloud-auth-submit class="primary cloud-login-submit">Log In</button>
      <p class="cloud-auth-switch" data-cloud-signup-link><span data-i18n="Don't have an account?">Don't have an account?</span> <button type="button" class="cloud-auth-link" data-cloud-view="register" data-i18n="Create an account">Create an account</button></p>
      <p class="cloud-auth-switch" data-cloud-back-link hidden><span data-i18n="Already have an account?">Already have an account?</span> <button type="button" class="cloud-auth-link" data-cloud-view="login" data-i18n="Back to sign in">Back to sign in</button></p>
      <p class="cloud-auth-switch" data-cloud-email-back hidden><button type="button" class="cloud-auth-link" data-cloud-restart-registration data-i18n="Use a different email">Use a different email</button></p>
    </form>
    <p class="cloud-auth-security hint" data-i18n="Signing in does not authorize a phone. Pairing still requires approval on this desktop.">Signing in does not authorize a phone. Pairing still requires approval on this desktop.</p>`;
  const network=document.querySelector('[data-panel="network"]');
  network.prepend(root);
  // Keep the login form outside the desktop settings form.
  document.body.append(dialog);
  const $=key=>root.querySelector('[data-cloud-'+key+']')||dialog.querySelector('[data-cloud-'+key+']');
  const all=selector=>[...root.querySelectorAll(selector),...dialog.querySelectorAll(selector)];
  const runtimeMessages={stopped:'Relay stopped',disabled:'Relay disabled',connecting:'Relay connecting',connected:'Relay connected',unavailable:'Relay unavailable',automatic:'Relay enabled automatically. Start the gateway to connect.'};
  const errors={
    invalid_local_configuration:'Local cloud configuration is invalid.',
    login_required:'Sign in to the cloud account first.',
    native_vault_unavailable:'The system credential vault is unavailable. Check desktop access permissions.',
    credential_scope_mismatch:'Stored credentials belong to a different server or account. Sign in again.',
    response_too_large:'The cloud response exceeds the size limit.',
    redirect_forbidden:'The cloud server redirected the request. Use its exact HTTPS origin.',
    network_or_certificate_error:'Cannot connect to the cloud server. Check the network and HTTPS certificate.',
    device_identity_mismatch:'The registered gateway identity does not match this desktop.',
    gateway_not_connected:'The cloud gateway is not connected. Keep the gateway running and wait for automatic connection.',
    device_proof_unavailable:'The encrypted device proof is unavailable or its identity changed. Refresh the device list and verify the safety code.',
    device_proof_capacity_reached:'The local device verification limit has been reached. This device cannot be added.',
    automatic_discovery_unsupported:'Update the server, desktop and phone together to use automatic device discovery.',
    invalid_device_directory:'The cloud device directory could not be verified.',
    device_access_unavailable:'This device is permanently revoked or unavailable. Its identity is not restored automatically.',
    device_access_unconfirmed:'Connection is blocked locally. The cloud permission change could not be confirmed; refresh before trying again.',
    device_access_superseded:'This permission change was superseded. Connection remains blocked locally.',
    device_access_storage_error:'Connection is blocked for this gateway run, but its disabled state could not be saved. Fix storage permissions before restarting.',
    connection_failed:'Cloud connection failed. Check the server and network.',
    protocol_upgrade_required:'Update the server, desktop and phone together to use automatic device discovery.',
    capacity:'Too many attempts. Wait before trying again.',
    gateway_already_connected:'The cloud gateway is not connected. Keep the gateway running and wait for automatic connection.',
    cloud_dependencies_missing:'Cloud relay dependencies are missing. Complete the gateway runtime setup.',
    invalid_credentials:'Incorrect credentials or account unavailable. Hosted accounts must verify their email first.',
    authentication_required:'Your cloud session has expired. Sign in again.',
    entitlement_required:'Cloud service is inactive or expired. Redeem service days; direct connections are unchanged.',
    rate_limited:'Too many attempts. Wait before trying again.',
    registration_disabled:'Public registration is disabled. Ask the server administrator to create an account.',
    redemption_disabled:'Service-day redemption is not available in self-host mode.',
    invalid_redemption:'The redemption code is invalid or no longer available.',
    redemption_batch_already_used:'This account has already redeemed a code from this batch. Use a different batch.',
    invalid_password:'Use a password containing 12 to 256 bytes.',
    invalid_email:'Enter a valid email address.',
    invalid_email_token:'This password reset link is invalid, expired or already used. Request a new link.',
    access_denied:'Access to the cloud service is denied.',
    https_required:'This cloud server accepts HTTPS connections only.',
    state_conflict:'The cloud record has changed or the username is in use. Refresh the status.',
    not_found:'The cloud record was not found.',
    unavailable:'The cloud service is temporarily unavailable.',
    server_error:'The cloud server returned an error. Check the server logs.',
    email_code_unsupported:'This server does not support email verification codes yet. Update the server before registering.',
    invalid_verification_code:'The verification code is incorrect, expired or exhausted. Request a new code.',
    email_first_registration_unsupported:'This server does not support email-first registration yet. Update the server before registering.',
    invalid_registration_token:'Email verification has expired or already been used. Verify your email again.',
    invalid_entitlement_response:'Cloud service status could not be verified. Refresh the service status.',
    service_status_unavailable:'Cloud service status could not be verified. Refresh the service status.',
    redemption_result_unconfirmed:'The redemption result could not be confirmed. Refresh service status before submitting again.'
  };
  const validationMessages={
    'An exact HTTPS origin is required':'Enter the exact HTTPS server origin without a path, query or fragment.',
    'an exact HTTPS origin is required':'Enter the exact HTTPS server origin without a path, query or fragment.',
    'an exact HTTPS origin without a path is required':'Enter the exact HTTPS server origin without a path, query or fragment.',
    'non-canonical server host':'Use the canonical server origin; do not add a trailing slash or default HTTPS port.',
    'non-canonical server origin':'Use the canonical server origin; do not add a trailing slash or default HTTPS port.',
    'Invalid cloud operation':'Invalid cloud operation.',
    'invalid cloud operation':'Invalid cloud operation.',
    'unsupported cloud operation':'Invalid cloud operation.',
    'Invalid cloud switch':'Invalid cloud switch.',
    'invalid cloud switch':'Invalid cloud switch.',
    'Invalid cloud access revision':'Invalid cloud access revision',
    'invalid cloud access revision':'Invalid cloud access revision',
    'Invalid cloud binding':'Invalid cloud phone binding.',
    'invalid cloud login':'Enter a valid cloud account login.',
    'Enter the six-digit email verification code.':'Enter the six-digit email verification code.',
    'invalid redemption code':'Enter a valid service-days redemption code.',
    'local desktop approval required':'Approve the phone locally on the desktop first.',
    'unapproved mobile identity':'This phone identity is not approved locally.',
    'preview key replacement requires re-pairing':'The phone keys changed. Pair and approve the identity again.'
  };
  const fieldLabels={origin:'Exact HTTPS server origin',login:'Email / selfhost username',password:'Password',name:'Gateway name',code:'Service-days redemption code',binding_id:'Phone binding ID',verification_code:'Email verification code',registration_token:'Email verification',safety_code:'Pairing safety code'};
  const authTitles={login:'Sign In to Cloud',register:'Create a cloud account',forgot:'Reset your password',password:'Set your password'};
  const authHelp={login:'Sign in with your account to continue using cloud services.',register:'Enter your email address, get a code, then verify it before setting a password.',forgot:'Enter your email address to request a password reset link.',password:'Email verified. Set a password to finish creating your account.'};
  const authSubmits={login:'Log In',register:'Verify your email',forgot:'Send reset link',password:'Complete registration'};
  const completionMessages={'signup-code':'If this email can be registered, a six-digit code will be sent. It expires after 10 minutes.',forgot:'If this account exists, a password reset link will be sent to its verified email address.'};
  const serviceMessages={
    login:'Cloud sign-in completed. Phones on this account appear automatically; allow connections on this desktop.',
    loginRefreshFailed:'Cloud sign-in completed, but service status could not be refreshed. Refresh the service status.',
    logout:'Signed out of the cloud account.',
    logoutRemoteUnconfirmed:'Signed out locally. Cloud session revocation could not be confirmed; direct connections are unchanged.',
    logoutCredentialsUncleared:'Signed out locally, but saved cloud credentials could not be cleared. Check the system credential vault.',
    entitlement:'Cloud service status refreshed.',
    redeem:'Service days redeemed. Service status refreshed.',
    redeemRefreshFailed:'Service days redeemed, but service status could not be refreshed. Refresh status; do not redeem again.'
  };
  const codeRetries=new Map();
  let state={},busy=false,authView='login',registrationProof=null,originEdited=false,draftOrigin=officialOrigin,devices=null,directoryPolling=false,entitlement=null,resultMessage='',resultIsError=false,directoryError='';
  let directoryRevision=0,directoryTimer=null,authTimer=null,lastRows=null;
  function accountScope(value=state){return value.configured?value.origin+'\0'+value.account_id+'\0'+value.gateway_id:'';}
  function selectedOrigin(){return $('server').value==='official'?officialOrigin:$('origin').value.trim();}
  function authAction(){return authView==='register'?'signup-verify':authView==='password'?'register':authView;}
  function emailCodeScope(){return selectedOrigin()+'\0'+$('login').value.trim().toLowerCase();}
  function canRegister(){return registrationProof&&registrationProof.scope===emailCodeScope()&&registrationProof.deadline>Date.now();}
  function clearRegistration(){registrationProof=null;$('password').value='';$('password-confirm').value='';$('email-code').value='';}
  function format(source,values){return t(source).replace(/\{(\w+)\}/g,(match,key)=>Object.hasOwn(values,key)?String(values[key]):match);}
  function codeText(code){return Object.hasOwn(errors,code)?t(errors[code])+' ('+code+')':code;}
  function errorText(value){
    const source=String(value||'').replace(/^Error invoking remote method '[^']+': (?:Error: )?/,'');
    const cloud=/^Cloud service: (.+)$/.exec(source),field=/^Invalid cloud field: (\w+)$/.exec(source);
    if(cloud)return format('Cloud service: {message}',{message:codeText(cloud[1])});
    if(field)return format('Invalid cloud field: {field}',{field:t(Object.hasOwn(fieldLabels,field[1])?fieldLabels[field[1]]:field[1])})+' ('+field[1]+')';
    return t(Object.hasOwn(validationMessages,source)?validationMessages[source]:source);
  }
  function renderResult(){const text=resultIsError?errorText(resultMessage):t(resultMessage);$('result').textContent=text;$('login-result').textContent=text;$('login-result').classList.toggle('is-error',resultIsError);}
  function setResult(message,error=false){resultMessage=message;resultIsError=error;renderResult();}
  function resetDirectory(){directoryRevision++;devices=null;directoryError='';lastRows=null;$('directory').open=false;$('devices').replaceChildren();}
  function completeLogout(value){
    resetDirectory();clearRegistration();authView='login';$('code').value='';update(value);
    const uncleared=!!value.credential_clear_error,unconfirmed=value.remote_logout_confirmed!==true;
    setResult(uncleared?serviceMessages.logoutCredentialsUncleared:unconfirmed?serviceMessages.logoutRemoteUnconfirmed:serviceMessages.logout,uncleared||unconfirmed);
  }
  function update(value){
    if(!value)return;
    if(accountScope()!==accountScope(value)){entitlement=null;setResult('');}
    if(Object.hasOwn(value,'entitlement'))entitlement=value.entitlement;
    if(accountScope()!==accountScope(value))resetDirectory();
    state=value;
    // A saved custom origin takes precedence until the user edits the address.
    if(!originEdited){$('server').value=value.origin&&value.origin!==officialOrigin?'custom':'official';$('origin').value=value.origin&&value.origin!==officialOrigin?value.origin:'';draftOrigin=selectedOrigin();}
    if(!dialog.open){
      if(!$('login').value)$('login').value=value.login||'';
    }
    render();
  }
  // Language changes only repaint cached state; they never replay cloud operations.
  function render(){
    BridgeI18n.apply(root);BridgeI18n.apply(dialog);
    const runtimeState=state.runtime?.state||'stopped';
    const runtimeText=state.enabled&&runtimeState==='stopped'?runtimeMessages.automatic:state.enabled&&runtimeState==='disabled'?runtimeMessages.connecting:Object.hasOwn(runtimeMessages,runtimeState)?runtimeMessages[runtimeState]:runtimeState;
    $('state').textContent=t(runtimeText)+(state.runtime?.code?' / '+codeText(state.runtime.code):'');
    all('button').forEach(button=>button.disabled=busy);
    $('server').disabled=busy;$('login').disabled=busy;
    const custom=$('server').value==='custom';
    $('custom-origin').hidden=!custom;$('origin').disabled=busy||!custom;
    const value=state;
    $('account').hidden=!value.configured;
    $('open-login').hidden=!!value.configured;
    $('signin-hint').hidden=!!value.configured;
    $('identity').textContent=value.configured?(value.origin===officialOrigin?t('Official Cloud'):value.origin)+' / '+value.login+' / '+format('Gateway ID: {id}',{id:value.gateway_id}):'';
    $('redeem').hidden=!(value.configured&&entitlement?.required===true);
    $('entitlement').textContent=!entitlement?(value.configured&&value.entitlement_error?t('Cloud service status could not be verified. Refresh the service status.'):''):!entitlement.active?t('Cloud entitlement is inactive. Direct connections are unchanged.'):entitlement.expires_at?format('Active until {date}',{date:new Date(entitlement.expires_at).toLocaleString(BridgeI18n.locale())}):t('Active (selfhost)');
    renderResult();
    renderAuth();
    renderDirectory();
    syncPolling();
  }
  function renderAuth(){
    const login=authView==='login',register=authView==='register',password=authView==='password',needsPassword=login||password;
    dialog.dataset.view=authView;
    dialog.querySelector('#cloud-login-title').textContent=t(authTitles[authView]);
    dialog.querySelector('#cloud-login-help').textContent=t(authHelp[authView]);
    $('login-label').textContent=t(login?'Email / selfhost username':'Email address');
    $('login').type=login?'text':'email';$('login').autocomplete=login?'username':'email';
    $('login').readOnly=password;
    $('email-code-field').hidden=!register;$('email-code').disabled=busy||!register;$('email-code').required=register;
    $('password-label').textContent=t(password?'Create a password':'Password');
    $('password-field').hidden=!needsPassword;$('password').required=needsPassword;$('password').disabled=busy||!needsPassword;
    $('password').minLength=password?12:0;$('password').autocomplete=password?'new-password':'current-password';
    $('password').setCustomValidity(password&&new TextEncoder().encode($('password').value).length>256?t(errors.invalid_password):'');
    $('password-help').hidden=!password;
    $('confirm-field').hidden=!password;$('password-confirm').required=password;$('password-confirm').disabled=busy||!password;
    $('password-confirm').setCustomValidity(password&&$('password-confirm').value&&$('password-confirm').value!==$('password').value?t('Passwords do not match.'): '');
    $('forgot-link').hidden=!login;$('signup-link').hidden=!login;$('back-link').hidden=login;$('email-back').hidden=!password;
    $('auth-submit').dataset.cloudAction=authAction();$('auth-submit').textContent=t(authSubmits[authView]);
    $('auth-submit').disabled=busy||(register&&(!$('login').checkValidity()||!$('email-code').checkValidity()))||(password&&!canRegister());
    renderCodeRetry();
  }
  function renderCodeRetry(){
    const remaining=Math.max(0,Math.ceil(((codeRetries.get(emailCodeScope())||0)-Date.now())/1000));
    $('resend-code').disabled=busy||authView!=='register'||remaining>0||!$('login').checkValidity();
    $('resend-code').textContent=remaining?format('Get another code in {seconds}s',{seconds:remaining}):t('Get code');
  }
  function showView(view){
    if(busy||!Object.hasOwn(authTitles,view)||view==='password')return;
    if(authView!==view){clearRegistration();setResult('');}
    authView=view;render();$('login').focus();
  }
  function directoryVisible(){return state.configured&&!network.hidden&&!document.hidden&&$('directory').open;}
  function renderDirectory(){
    const directory=new Map((devices||[]).map(item=>[item.binding_id,item]));
    for(const pin of state.bindings||[]){
      const item=directory.get(pin.binding_id);
      if(!item||pin.generation>item.generation)directory.set(pin.binding_id,{...item,...pin,trusted:true,proof_verified:!!pin.safety_code});
    }
    $('device-count').textContent=devices===null?'':String(directory.size);
    if(!directoryVisible())return;
    $('directory-error').textContent=directoryError?errorText(directoryError):'';
    const rows=[...directory.values()],signature=JSON.stringify([rows,busy,state.enabled,BridgeI18n.language()]);
    if(signature===lastRows)return;
    lastRows=signature;
    const focused=document.activeElement?.dataset.cloudAllow;
    renderDevices($('devices'),rows);
    if(focused)Array.from($('devices').querySelectorAll('[data-cloud-allow]')).find(node=>node.dataset.cloudAllow===focused)?.focus();
  }
  function renderDevices(container,devices){
    container.replaceChildren();
    if(!devices.length){const empty=document.createElement('p');empty.className='hint';empty.textContent=t('No cloud phones yet. Sign in on your phone with this account.');container.append(empty);}
    for(const item of devices){
      const row=document.createElement('div'),name=document.createElement('strong'),code=document.createElement('p'),label=document.createElement('label'),toggle=document.createElement('input'),caption=document.createElement('span');
      row.className='cloud-device';row.setAttribute('data-cloud-device',item.binding_id);
      name.textContent=item.device_name||t('Phone');
      const allowed=item.allowed===true&&item.remote_status==='active'&&item.trusted&&!item.device_revoked;
      const revoked=item.remote_status==='revoked'||item.device_revoked;
      code.textContent=item.safety_code?format('Safety code: {code}',{code:item.safety_code}):t('Waiting for encrypted device verification');
      const status=document.createElement('p');status.className='hint';status.textContent=t(revoked?'Device permanently revoked':item.sync_state==='unconfirmed'?'Connection blocked locally; cloud permission unconfirmed':allowed?'Connection allowed':item.trusted?'Connection disabled; verified keys are kept':'Waiting for desktop permission')+' / '+t(item.online?'Online':'Offline');
      toggle.type='checkbox';toggle.setAttribute('role','switch');toggle.setAttribute('data-cloud-allow',item.binding_id);toggle.checked=allowed;
      toggle.setAttribute('aria-label',format('Allow connection for {name}',{name:item.device_name||t('Phone')}));
      toggle.disabled=busy||revoked||item.sync_state==='identity_changed'||(!allowed&&(!state.enabled||!item.proof_verified));
      caption.textContent=t('Allow Connection');label.className='cloud-device-allow';label.append(toggle,caption);
      toggle.onchange=async()=>{
        const next=toggle.checked;toggle.checked=allowed;
        if(next&&!item.trusted&&!window.confirm(format('Allow {name} only if safety code {code} matches the phone?',{name:item.device_name||t('Phone'),code:item.safety_code})))return;
        await call('access',{binding_id:item.binding_id,allowed:next,expected_revision:item.remote_revision||0,...(next&&!item.trusted?{safety_code:item.safety_code}:{})});
      };
      row.append(name,code,status,label);
      container.append(row);
    }
  }
  async function refreshDirectory(){
    if(busy||directoryPolling||!directoryVisible())return;
    const expected=accountScope(),revision=directoryRevision;directoryPolling=true;
    try{
      const result=await api.cloud({action:'devices'});
      if(expected!==accountScope()||revision!==directoryRevision)return;
      devices=result.devices||[];directoryError='';renderDirectory();
    }catch(error){if(expected===accountScope()&&revision===directoryRevision){directoryError=error.message;renderDirectory();}}finally{directoryPolling=false;}
  }
  async function call(action,extra={}){
    if(busy)return;
    if(action==='signup-code'&&(authView!=='register'||!$('login').reportValidity()||($('server').value==='custom'&&!$('origin').reportValidity())))return;
    if(action==='signup-verify'&&(authView!=='register'||!$('login-form').reportValidity()))return;
    if(action==='register'){
      if(authView!=='password'||!canRegister()){clearRegistration();authView='register';setResult(errors.invalid_registration_token,true);render();return;}
      if(!$('login-form').reportValidity())return;
    }
    let signedIn=false,redeemed=false,refreshError='';
    const expectedAccount=accountScope();
    if(action==='access')directoryRevision++;
    if(action==='access'){
      if(devices)devices=devices.map(item=>item.binding_id===extra.binding_id?{...item,allowed:false,sync_state:'unconfirmed'}:item);
      state={...state,bindings:(state.bindings||[]).map(item=>item.binding_id===extra.binding_id?{...item,allowed:false,sync_state:'unconfirmed'}:item)};
    }
    busy=true;setResult('Working...');render();
    try{
      const origin=selectedOrigin();
      const payload={action,...extra};
      if(['login','register','signup-code','signup-verify','forgot'].includes(action))payload.origin=origin;
      if(['login','register','signup-code','signup-verify','forgot'].includes(action))payload.login=$('login').value.trim();
      const requestScope=emailCodeScope();
      if(action==='signup-verify'){payload.verification_code=$('email-code').value.trim();$('email-code').value='';}
      if(action==='register'){payload.registration_token=registrationProof.token;registrationProof=null;}
      if(['login','register'].includes(action)){payload.password=$('password').value;$('password').value='';$('password-confirm').value='';}
      if(action==='redeem'){payload.code=$('code').value.trim();$('code').value='';}
      const result=await api.cloud(payload);
      if(expectedAccount&&action!=='login'&&expectedAccount!==accountScope()&&(action!=='logout'||state.configured))return;
      if(action==='logout'){
        if(result?.configured!==false)throw Error('The cloud sign-out result could not be confirmed. Refresh status before trying again.');
        completeLogout(result);return;
      }
      if(action==='login'&&(!result.configured||result.origin!==origin||!result.account_id))throw Error('The cloud sign-in result could not be confirmed. Refresh status before trying again.');
      if(action==='login'){signedIn=true;refreshError=result.entitlement_error||'';}
      if(action==='redeem'){
        if(result.redeemed!==true)throw Error('The redemption result could not be confirmed. Refresh service status before submitting again.');
        redeemed=true;refreshError=result.refresh_error||'';
        if(expectedAccount===accountScope()){
          entitlement=result.entitlement||null;
          if(result.cloud&&expectedAccount===accountScope(result.cloud))update(result.cloud);
        }
      }
      if(action==='access'&&result.remote_access_confirmed!==true)refreshError=result.access_error||'device_access_unconfirmed';
      if(Object.hasOwn(completionMessages,action)&&result.accepted!==true)throw Error('The account request could not be confirmed. Check your email and status before trying again.');
      if(action==='signup-code'&&result.verification_method!=='code')throw Error('The account request could not be confirmed. Check your email and status before trying again.');
      if(action==='signup-verify'){
        if(result.verified!==true||typeof result.registration_token!=='string'||!/^[A-Za-z0-9_-]{43}$/.test(result.registration_token)||!Number.isInteger(result.expires_in)||result.expires_in<1||result.expires_in>600||requestScope!==emailCodeScope())throw Error('The email verification result could not be confirmed. Request a new code before trying again.');
        registrationProof={scope:requestScope,token:result.registration_token,deadline:Date.now()+result.expires_in*1000};authView='password';
      }
      if(action==='register'&&result.registered!==true)throw Error('The registration result could not be confirmed. Try signing in or resetting your password before registering again.');
      if(action==='signup-code'){
        $('email-code').value='';
        codeRetries.set(requestScope,Date.now()+Math.min(300,Math.max(0,Number(result.retry_after)||60))*1000);
      }
      if(action==='entitlement')entitlement=result;
      if(result.configured!==undefined)update(result);
      if(action==='register')authView='login';
      if(!['signup-code','signup-verify','register'].includes(action)){
        const current=await api.cloud({action:'status'});
        if(expectedAccount&&action!=='login'&&expectedAccount!==accountScope(current)){update(current);return;}
        update(current);
      }
      if(action==='access'&&expectedAccount===accountScope()&&directoryVisible()){
        const current=await api.cloud({action:'devices'});
        if(expectedAccount===accountScope()){devices=current.devices||[];directoryError='';}
      }
      if(action==='login')signedIn=state.configured&&state.origin===origin&&state.account_id===result.account_id;
      if(action==='login'&&!signedIn)throw Error('The cloud sign-in result could not be confirmed. Refresh status before trying again.');
      setResult(action==='access'?refreshError?errors.device_access_unconfirmed:extra.allowed?'Connection allowed on this desktop.':'Connection disabled. Verified keys are kept for a later restore.':action==='signup-verify'?'':action==='register'?'Account created. Sign in to continue.':Object.hasOwn(completionMessages,action)?completionMessages[action]:action==='redeem'&&refreshError?serviceMessages.redeemRefreshFailed:action==='login'&&refreshError?serviceMessages.loginRefreshFailed:Object.hasOwn(serviceMessages,action)?serviceMessages[action]:'Done. If a write times out, refresh status; do not blindly replay it.',!!refreshError);
    }catch(error){
      if(action==='register'){clearRegistration();authView='login';}
      if(action==='logout')try{
        const current=await api.cloud({action:'status'});
        if(expectedAccount&&state.configured&&expectedAccount!==accountScope())return;
        if(current?.configured===false){completeLogout(current);return;}
        update(current);
      }catch{}
      if(action==='access')try{update(await api.cloud({action:'status'}));}catch{}
      setResult(action==='access'?/device_access_storage_error/.test(error.message)?errors.device_access_storage_error:errors.device_access_unconfirmed:redeemed?serviceMessages.redeemRefreshFailed:signedIn?serviceMessages.loginRefreshFailed:action==='register'&&/network_or_certificate_error/.test(error.message)?'The registration result could not be confirmed. Try signing in or resetting your password before registering again.':error.message,true);
    }
    finally{busy=false;render();if(signedIn&&dialog.open)dialog.close();else if(dialog.open&&['register','signup-code','signup-verify'].includes(action))$(authView==='password'?'password':authView==='register'?'email-code':'login').focus();}
  }
  function changeServer(){
    if(busy)return;
    originEdited=true;const origin=selectedOrigin();
    if(origin!==draftOrigin){clearRegistration();if(authView==='password')authView='register';setResult('');}
    draftOrigin=origin;render();
  }
  $('server').onchange=changeServer;$('origin').addEventListener('input',changeServer);
  $('open-login').onclick=()=>{if(busy||dialog.open||state.configured)return;clearRegistration();originEdited=true;authView='login';render();dialog.showModal();syncPolling();$('server').focus();};
  $('close-login').onclick=()=>{if(!busy)dialog.close();};
  $('restart-registration').onclick=()=>showView('register');
  dialog.addEventListener('cancel',event=>{if(busy)event.preventDefault();});
  dialog.addEventListener('close',()=>{clearRegistration();syncPolling();if(!$('open-login').hidden&&!$('open-login').disabled)$('open-login').focus();});
  $('login').addEventListener('input',()=>{if(busy)return;clearRegistration();if(authView==='password')authView='register';setResult('');renderAuth();syncPolling();});
  $('email-code').addEventListener('input',renderAuth);
  $('password').addEventListener('input',renderAuth);$('password-confirm').addEventListener('input',renderAuth);
  $('login-form').onsubmit=event=>{event.preventDefault();event.stopPropagation();if(dialog.open)call(authAction());};
  all('[data-cloud-view]').forEach(button=>button.onclick=()=>showView(button.dataset.cloudView));
  all('[data-cloud-action]').filter(button=>button.type!=='submit').forEach(button=>button.onclick=()=>call(button.dataset.cloudAction));
  root.addEventListener('keydown',event=>{
    if(event.key==='Enter'&&event.target.hasAttribute('data-cloud-code')){
      event.preventDefault();event.stopPropagation();call('redeem');
    }
  });
  function tickAuth(){
    if(registrationProof&&registrationProof.deadline<=Date.now()&&!busy){clearRegistration();if(authView==='password')authView='register';setResult(errors.invalid_registration_token,true);render();}
    renderCodeRetry();
    syncPolling();
  }
  function syncPolling(){
    if(directoryVisible()){
      if(directoryTimer===null){
        directoryTimer=setInterval(refreshDirectory,2000);
        renderDirectory();
        refreshDirectory();
      }
    }else if(directoryTimer!==null){clearInterval(directoryTimer);directoryTimer=null;}
    const countdown=!document.hidden&&!network.hidden&&dialog.open&&(registrationProof||authView==='register'&&(codeRetries.get(emailCodeScope())||0)>Date.now());
    if(countdown&&authTimer===null)authTimer=setInterval(tickAuth,2000);
    else if(!countdown&&authTimer!==null){clearInterval(authTimer);authTimer=null;}
  }
  $('directory').addEventListener('toggle',syncPolling);
  document.addEventListener('visibilitychange',syncPolling);
  document.addEventListener('bridge-language',render);
  render();
  window.CloudPanel={update,syncPolling};
})();
