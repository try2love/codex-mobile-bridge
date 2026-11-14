'use strict';

(() => {
  const officialOrigin='https://bridge.waken.cloudns.be:8787';
  const api=window.bridgeDesktop,t=BridgeI18n.t,root=document.createElement('article'),dialog=document.createElement('dialog'),sponsorDialog=document.createElement('dialog');
  root.id='cloud-account';
  root.innerHTML=`<div class="section-head"><h2 data-i18n="Cloud Relay">Cloud Relay</h2><button type="button" class="primary" data-cloud-open-login data-i18n="Sign In to Cloud">Sign In to Cloud</button></div>
    <p class="hint" data-cloud-signin-hint data-i18n="Sign in to use the cloud relay. Your direct connections are unchanged.">Sign in to use the cloud relay. Your direct connections are unchanged.</p>
    <p data-cloud-state role="status"></p>
    <section class="cloud-sponsor" data-cloud-sponsor hidden aria-labelledby="cloud-sponsor-title"><h3 id="cloud-sponsor-title" data-i18n="Sponsor support">Sponsor support</h3>
    <p class="hint" id="cloud-sponsor-help" data-i18n="After purchasing, sign in to your Official Cloud account and redeem the code on this page.">After purchasing, sign in to your Official Cloud account and redeem the code on this page.</p>
    <button type="button" data-cloud-open-sponsor aria-haspopup="dialog" aria-controls="cloud-sponsor-dialog" aria-describedby="cloud-sponsor-help" data-i18n="Buy a 30-day Official Cloud activation code (Xianyu)">Buy a 30-day Official Cloud activation code (Xianyu)</button></section>
    <div data-cloud-account hidden><p data-cloud-identity></p>
    <div class="actions"><button type="button" data-cloud-action="entitlement" data-i18n="Refresh Entitlement">Refresh Entitlement</button><button type="button" data-cloud-action="logout" data-i18n="Log Out">Log Out</button></div>
    <p data-cloud-entitlement></p><div data-cloud-redeem hidden><label><span data-i18n="Service-days redemption code">Service-days redemption code</span><input data-cloud-code autocomplete="off" maxlength="128"></label><button type="button" data-cloud-action="redeem" data-i18n="Redeem">Redeem</button></div>
    <div class="cloud-device cloud-web-access"><h3 data-i18n="Web Cloud Access">Web Cloud Access</h3>
    <p class="hint" id="cloud-web-help" data-i18n="Allow this cloud account to access this computer from a browser. Turning this off disconnects open web sessions; phone permissions are separate.">Allow this cloud account to access this computer from a browser. Turning this off disconnects open web sessions; phone permissions are separate.</p>
    <label class="cloud-device-allow"><input type="checkbox" role="switch" data-cloud-web-allow aria-describedby="cloud-web-help"><span data-i18n="Allow web access to this computer">Allow web access to this computer</span></label>
    <p data-cloud-web-state class="hint" role="status"></p>
    <div class="cloud-web-address" data-cloud-web-address-panel hidden><label data-cloud-web-address-field hidden><span data-i18n="Web cloud address">Web cloud address</span><input data-cloud-web-address type="url" readonly spellcheck="false"></label>
    <p class="hint" data-cloud-web-address-status role="status"></p><button type="button" data-cloud-web-address-refresh hidden data-i18n="Refresh address">Refresh address</button></div>
    <button type="button" data-cloud-web-disable hidden data-i18n="Retry saving web access as disabled">Retry saving web access as disabled</button></div>
    <h3 data-i18n="Cloud Devices">Cloud Devices</h3><p class="hint" data-i18n="Phones signed in to this account appear automatically. Allow each phone to connect on this desktop.">Phones signed in to this account appear automatically. Allow each phone to connect on this desktop.</p>
    <p data-cloud-directory-error class="hint" role="status"></p><div data-cloud-devices></div></div>
    <p data-cloud-result role="status"></p>`;
  dialog.id='cloud-login-dialog';dialog.className='cloud-login-dialog';
  dialog.setAttribute('aria-labelledby','cloud-login-title');dialog.setAttribute('aria-describedby','cloud-login-help');
  dialog.innerHTML=`<div class="cloud-login-head"><h2 id="cloud-login-title">Sign In to Cloud</h2><button type="button" data-cloud-close-login data-close="cloud-login-dialog" data-i18n="Close">Close</button></div>
    <p id="cloud-login-help" class="hint"></p>
    <form data-cloud-login-form>
      <label><span data-i18n="Cloud server">Cloud server</span><select data-cloud-server><option value="official" data-i18n="Official Cloud">Official Cloud</option><option value="custom" data-i18n="Custom Server">Custom Server</option></select></label>
      <label data-cloud-custom-origin hidden><span data-i18n="Exact HTTPS server origin">Exact HTTPS server origin</span><input data-cloud-origin type="url" placeholder="https://relay.example.com:8443" autocomplete="off" maxlength="2048" required></label>
      <label><span data-cloud-login-label></span><input data-cloud-login autocomplete="username" maxlength="320" required></label>
      <label data-cloud-email-code-field hidden><span data-i18n="Email verification code">Email verification code</span><div class="cloud-email-code-row"><input data-cloud-email-code type="text" inputmode="numeric" autocomplete="one-time-code" pattern="[0-9]{6}" minlength="6" maxlength="6" required><button type="button" data-cloud-resend-code data-cloud-action="signup-code">Get code</button></div></label>
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
  document.querySelector('[data-panel="network"]').prepend(root);
  // Keep the login form outside the desktop settings form.
  document.body.append(dialog);
  sponsorDialog.id='cloud-sponsor-dialog';sponsorDialog.className='cloud-login-dialog cloud-sponsor-dialog';
  sponsorDialog.setAttribute('aria-labelledby','cloud-sponsor-dialog-title');sponsorDialog.setAttribute('aria-describedby','cloud-sponsor-dialog-help');
  sponsorDialog.innerHTML=`<div class="cloud-login-head"><h2 id="cloud-sponsor-dialog-title" data-i18n="Scan with phone">Scan with phone</h2><button type="button" data-cloud-close-sponsor data-close="cloud-sponsor-dialog" data-i18n="Close">Close</button></div>
    <p class="hint" id="cloud-sponsor-dialog-help" data-i18n="Scan with your phone to open the Xianyu purchase page.">Scan with your phone to open the Xianyu purchase page.</p>
    <p data-cloud-sponsor-qr-status role="status" aria-live="polite"></p><img data-cloud-sponsor-qr-image width="320" height="320" alt="Xianyu purchase QR code" hidden>`;
  document.body.append(sponsorDialog);
  const $=key=>root.querySelector('[data-cloud-'+key+']')||dialog.querySelector('[data-cloud-'+key+']')||sponsorDialog.querySelector('[data-cloud-'+key+']');
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
    web_access_storage_error:'Web access is blocked for this gateway run, but its permission could not be saved. Fix storage permissions before restarting.',
    web_access_conflict:'Web access changed. Refresh its status before trying again.',
    web_access_unavailable:'Web access status is unavailable. Restart the updated gateway and refresh.',
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
    redeemRefreshFailed:'Service days redeemed, but service status could not be refreshed. Refresh status; do not redeem again.',
    sponsorQrFailed:'Could not load the QR code. Close it and try again.',
    sponsorQrLoading:'Loading QR code...'
  };
  const webAddressMessages={loading:'Loading web cloud address...',ready:'Open this address in a browser and sign in with the same cloud account.',unavailable:'This server has not provided a web cloud address.',failed:'Could not load the web cloud address. Try refreshing it.'};
  const codeRetries=new Map();
  let state={},busy=false,authView='login',registrationProof=null,originEdited=false,draftOrigin=officialOrigin,devices=[],directoryPolling=false,entitlement=null,resultMessage='',resultIsError=false,directoryError='';
  let directoryRevision=0;
  let sponsorQrBusy=false,sponsorQrImage='',sponsorQrError=false;
  let webAddress={scope:'',origin:'',status:'loading'},webAddressRequest=0;
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
  function resetDirectory(){directoryRevision++;devices=[];directoryError='';$('devices').replaceChildren();}
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
    refreshWebAddress();
  }
  // Language changes only repaint cached state; they never replay cloud operations.
  function render(){
    BridgeI18n.apply(root);BridgeI18n.apply(dialog);BridgeI18n.apply(sponsorDialog);
    const runtimeState=state.runtime?.state||'stopped';
    const runtimeText=state.enabled&&runtimeState==='stopped'?runtimeMessages.automatic:state.enabled&&runtimeState==='disabled'?runtimeMessages.connecting:Object.hasOwn(runtimeMessages,runtimeState)?runtimeMessages[runtimeState]:runtimeState;
    $('state').textContent=t(runtimeText)+(state.runtime?.code?' / '+codeText(state.runtime.code):'');
    all('button').forEach(button=>button.disabled=busy);
    $('server').disabled=busy;$('login').disabled=busy;
    const custom=$('server').value==='custom';
    $('custom-origin').hidden=!custom;$('origin').disabled=busy||!custom;
    const value=state;
    $('sponsor').hidden=!value.configured||value.origin!==officialOrigin;
    if($('sponsor').hidden&&sponsorDialog.open)sponsorDialog.close();
    renderSponsorQR();
    $('account').hidden=!value.configured;
    $('open-login').hidden=!!value.configured;
    $('signin-hint').hidden=!!value.configured;
    $('identity').textContent=value.configured?(value.origin===officialOrigin?t('Official Cloud'):value.origin)+' / '+value.login+' / '+format('Gateway ID: {id}',{id:value.gateway_id}):'';
    $('redeem').hidden=!(value.configured&&entitlement?.required===true);
    const web=value.web_access;
    $('web-allow').checked=web?.allowed===true&&!web.error;
    $('web-allow').disabled=busy||!value.configured||!web||!!web.error;
    $('web-disable').hidden=web?.error!=='web_access_storage_error';
    $('web-state').textContent=!web?t('Restart the updated gateway to manage web access.'):web.error?codeText(web.error):t(web.allowed?'Web access allowed for this cloud account.':'Web access disabled. Enable it here to connect from a browser.');
    renderWebAddress();
    $('entitlement').textContent=!entitlement?(value.configured&&value.entitlement_error?t('Cloud service status could not be verified. Refresh the service status.'):''):!entitlement.active?t('Cloud entitlement is inactive. Direct connections are unchanged.'):entitlement.expires_at?format('Active until {date}',{date:new Date(entitlement.expires_at).toLocaleString(BridgeI18n.locale())}):t('Active (selfhost)');
    renderResult();
    renderAuth();
    renderDirectory();
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
  function renderDirectory(){
    const directory=new Map(devices.map(item=>[item.binding_id,item]));
    for(const pin of state.bindings||[]){
      const item=directory.get(pin.binding_id);
      if(!item||pin.generation>item.generation)directory.set(pin.binding_id,{...item,...pin,trusted:true,proof_verified:!!pin.safety_code});
    }
    $('directory-error').textContent=directoryError?errorText(directoryError):'';
    renderDevices($('devices'),[...directory.values()]);
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
    if(busy||directoryPolling||!state.configured||document.hidden)return;
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
      devices=devices.map(item=>item.binding_id===extra.binding_id?{...item,allowed:false,sync_state:'unconfirmed'}:item);
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
      if(action==='web-access'){
        if(accountScope(result)!==expectedAccount||result.web_access?.allowed!==extra.allowed||result.web_access?.revision!==extra.expected_revision+1)throw Error('Web access status could not be confirmed. Refresh before trying again.');
        update(result);setResult(extra.allowed?'Web access allowed for this cloud account.':'Web access disabled. Open web sessions have been disconnected.');return;
      }
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
      if(action==='access'&&expectedAccount===accountScope()){
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
      if(action==='web-access'){
        if(expectedAccount!==accountScope())return;
        try{const current=await api.cloud({action:'status'});if(expectedAccount===accountScope()&&expectedAccount===accountScope(current))update(current);}catch{}
        if(expectedAccount!==accountScope())return;
      }
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
  function renderSponsorQR(){
    $('sponsor-qr-status').textContent=sponsorQrBusy?t(serviceMessages.sponsorQrLoading):sponsorQrError?t(serviceMessages.sponsorQrFailed):'';
    $('sponsor-qr-image').hidden=!sponsorQrImage;
    $('sponsor-qr-image').alt=t('Xianyu purchase QR code');
    if(sponsorQrImage)$('sponsor-qr-image').src=sponsorQrImage;
  }
  function webAddressScope(){return state.configured&&state.web_access?.allowed===true&&!state.web_access.error?accountScope():'';}
  function renderWebAddress(){
    const expected=webAddressScope(),current=webAddress.scope===expected?webAddress:{origin:'',status:'loading'};
    $('web-address-panel').hidden=!expected;
    $('web-address-field').hidden=!expected||!current.origin;
    $('web-address').value=expected?current.origin:'';
    $('web-address-status').textContent=expected?t(webAddressMessages[current.status]):'';
    $('web-address-refresh').hidden=!expected||!['failed','unavailable'].includes(current.status);
    $('web-address-refresh').disabled=busy||current.status==='loading';
  }
  async function refreshWebAddress(force=false){
    const expected=webAddressScope();
    if(!expected){webAddressRequest++;webAddress={scope:'',origin:'',status:'loading'};renderWebAddress();return;}
    if(!force&&webAddress.scope===expected)return;
    const revision=++webAddressRequest,origin=state.origin;
    webAddress={scope:expected,origin:'',status:'loading'};renderWebAddress();
    try{
      const capabilities=await api.cloud({action:'capabilities',origin});
      if(revision!==webAddressRequest||expected!==webAddressScope())return;
      const entry=capabilities?.web_client;
      if(entry?.enabled===true){
        const value=entry.origin;
        if(typeof value!=='string'||value.length>2048||!/^[\x21-\x7e]+$/.test(value))throw Error('Invalid web origin');
        const url=new URL(value);
        if(url.protocol!=='https:'||url.origin!==value||url.href!==value+'/'||url.username||url.password)throw Error('Invalid web origin');
        webAddress={scope:expected,origin:value,status:'ready'};
      }else webAddress={scope:expected,origin:'',status:'unavailable'};
    }catch{if(revision===webAddressRequest&&expected===webAddressScope())webAddress={scope:expected,origin:'',status:'failed'};}
    finally{if(revision===webAddressRequest)renderWebAddress();}
  }
  $('server').onchange=changeServer;$('origin').addEventListener('input',changeServer);
  $('open-sponsor').onclick=async()=>{
    if(busy||$('sponsor').hidden||sponsorDialog.open)return;
    if(!sponsorQrBusy){sponsorQrError=false;sponsorQrImage='';}
    renderSponsorQR();sponsorDialog.showModal();$('close-sponsor').focus();
    if(sponsorQrBusy)return;
    sponsorQrBusy=true;renderSponsorQR();
    try{const result=await api.cloud({action:'sponsorship',origin:officialOrigin});if(typeof result?.image!=='string'||result.image.length>65536||!/^data:image\/png;base64,[A-Za-z0-9+/]+={0,2}$/.test(result.image))throw Error('Invalid QR image');sponsorQrImage=result.image;}
    catch{sponsorQrError=true;}
    finally{sponsorQrBusy=false;renderSponsorQR();}
  };
  $('close-sponsor').onclick=()=>sponsorDialog.close();
  $('sponsor-qr-image').onerror=()=>{sponsorQrImage='';sponsorQrError=true;renderSponsorQR();};
  sponsorDialog.addEventListener('close',()=>{if(!$('sponsor').hidden&&!$('open-sponsor').disabled)$('open-sponsor').focus();});
  $('web-address-refresh').onclick=()=>refreshWebAddress(true);
  $('web-address').onclick=()=> $('web-address').select();
  $('web-allow').onchange=async()=>{
    const allowed=$('web-allow').checked;
    $('web-allow').checked=state.web_access?.allowed===true;
    if(busy||!state.configured||!state.web_access)return;
    await call('web-access',{origin:state.origin,account_id:state.account_id,gateway_id:state.gateway_id,
      allowed,expected_revision:state.web_access.revision});
  };
  $('web-disable').onclick=()=>call('web-access',{origin:state.origin,account_id:state.account_id,gateway_id:state.gateway_id,
    allowed:false,expected_revision:state.web_access.revision});
  $('open-login').onclick=()=>{if(busy||dialog.open||state.configured)return;clearRegistration();originEdited=true;authView='login';render();dialog.showModal();$('server').focus();};
  $('close-login').onclick=()=>{if(!busy)dialog.close();};
  $('restart-registration').onclick=()=>showView('register');
  dialog.addEventListener('cancel',event=>{if(busy)event.preventDefault();});
  dialog.addEventListener('close',()=>{clearRegistration();if(!$('open-login').hidden&&!$('open-login').disabled)$('open-login').focus();});
  $('login').addEventListener('input',()=>{if(busy)return;clearRegistration();if(authView==='password')authView='register';setResult('');renderAuth();});
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
  setInterval(async()=>{
    if(registrationProof&&registrationProof.deadline<=Date.now()&&!busy){clearRegistration();if(authView==='password')authView='register';setResult(errors.invalid_registration_token,true);render();}
    renderCodeRetry();
    await refreshDirectory();
  },2000);
  document.addEventListener('bridge-language',render);
  render();
  window.CloudPanel={update};
})();
