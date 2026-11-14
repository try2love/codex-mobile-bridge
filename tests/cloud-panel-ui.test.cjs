'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const root=path.resolve(__dirname,'..');
const copy=value=>JSON.parse(JSON.stringify(value));
const grant={required:true,active:true,expires_at:'2030-06-01T00:00:00Z',server_time:'2030-05-01T00:00:00Z'};
const account={configured:true,enabled:true,origin:'https://relay.example.test',account_id:'account-original',gateway_id:'gateway-original',login:'original-user',entitlement:grant,entitlement_error:'',runtime:{state:'connected'},bindings:[],web_access:{allowed:false,revision:0,error:''}};

function fixture(){
  const events=new Map(),timers=[],calls=[],responses={entitlement:copy(grant)};
  let document,now=Date.parse('2030-05-01T00:00:00Z'),status={configured:false,enabled:false,entitlement:null,runtime:{state:'stopped'},bindings:[]};
  // Parse just the production panel's markup; no browser, Electron or live IPC runs.
  class Node{
    constructor(tag){this.tagName=tag.toUpperCase();this.nodeType=tag==='#text'?3:1;this.childNodes=[];this.attributes={};this.dataset={};this.style={};this.value='';this.type='';this._text='';this.hidden=false;this.disabled=false;this.required=false;this.open=false;this.listeners=new Map();this.classList={toggle(){}};}
    get children(){return this.childNodes.filter(node=>node.nodeType===1);}
    get textContent(){return this._text+this.childNodes.map(node=>node.textContent).join('');}
    set textContent(value){this._text=String(value);this.childNodes=[];}
    set innerHTML(html){
      this.childNodes=[];const stack=[this];
      for(const token of html.matchAll(/<\/?[a-z][^>]*>|[^<]+/gi)){
        const value=token[0];
        if(value.startsWith('</')){stack.pop();continue;}
        if(!value.startsWith('<')){const node=new Node('#text');node.textContent=value;stack.at(-1).append(node);continue;}
        const tag=value.match(/^<([a-z][a-z0-9-]*)/i)[1],node=new Node(tag);
        for(const attr of value.slice(tag.length+1,-1).matchAll(/([^\s=]+)(?:="([^"]*)")?/g))node.setAttribute(attr[1],attr[2]??'');
        stack.at(-1).append(node);
        if(!['input','img','br','hr','meta','link'].includes(tag))stack.push(node);
      }
    }
    append(...nodes){for(const node of nodes){node.parentElement=this;this.childNodes.push(node);if(this.tagName==='SELECT'&&node.tagName==='OPTION'&&this.children.length===1)this.value=node.value;}}
    prepend(node){node.parentElement=this;this.childNodes.unshift(node);}
    replaceChildren(...nodes){this._text='';this.childNodes=[];this.append(...nodes);}
    setAttribute(name,value){this.attributes[name]=String(value);if(name.startsWith('data-'))this.dataset[name.slice(5).replace(/-([a-z])/g,(_,letter)=>letter.toUpperCase())]=String(value);if(['type','value','id'].includes(name))this[name]=String(value);if(['required','hidden','disabled'].includes(name))this[name]=true;}
    getAttribute(name){return this.attributes[name]??null;}
    removeAttribute(name){delete this.attributes[name];}
    hasAttribute(name){return Object.hasOwn(this.attributes,name);}
    matches(selector){const attr=selector.match(/^\[([^=\]]+)(?:="([^"]*)")?\]$/);return attr?this.hasAttribute(attr[1])&&(attr[2]===undefined||this.getAttribute(attr[1])===attr[2]):selector.startsWith('#')?this.id===selector.slice(1):this.tagName===selector.toUpperCase();}
    querySelectorAll(selector){return this.children.flatMap(node=>[...(node.matches(selector)?[node]:[]),...node.querySelectorAll(selector)]);}
    querySelector(selector){return this.querySelectorAll(selector)[0]||null;}
    addEventListener(name,fn){this.listeners.set(name,fn);}
    setCustomValidity(message){this.validationMessage=message;}
    checkValidity(){if(this.disabled)return true;if(this.tagName==='FORM')return this.querySelectorAll('input').every(node=>node.checkValidity());return !this.validationMessage&&(!this.required||!!this.value)&&(this.type!=='email'||/^[^@]+@[^@]+\.[^@]+$/.test(this.value));}
    reportValidity(){return this.checkValidity();}
    focus(){document.activeElement=this;}
    showModal(){this.open=true;}
    close(){this.open=false;this.listeners.get('close')?.();}
  }
  document={body:new Node('body'),documentElement:{},hidden:false,createElement:tag=>new Node(tag),querySelector:selector=>document.body.querySelector(selector),querySelectorAll:selector=>document.body.querySelectorAll(selector),addEventListener:(name,fn)=>events.set(name,fn),dispatchEvent:event=>events.get(event.type)?.(event)};
  const panel=new Node('section');panel.setAttribute('data-panel','network');document.body.append(panel);
  function respond(payload){
    if(payload.action==='status')return copy(status);
    if(payload.action==='web-access'){status.web_access={allowed:payload.allowed,revision:payload.expected_revision+1,error:''};return copy(status);}
    if(payload.action==='entitlement'){status={...status,entitlement:copy(responses.entitlement),entitlement_error:'',enabled:responses.entitlement.active};return copy(responses.entitlement);}
    if(payload.action==='login'){status=copy(responses.login||account);return copy(status);}
    if(payload.action==='logout'){status={configured:false,enabled:false,entitlement:null,runtime:{state:'stopped'},bindings:[]};return {...copy(status),logged_out:true,remote_logout_confirmed:true,logout_error:'',credential_clear_error:'',...copy(responses.logout||{})};}
    if(payload.action==='redeem'){
      const result=responses.redeem||{redeemed:true,entitlement:copy(grant),refresh_error:'',cloud:{...status,enabled:true,entitlement:copy(grant)}};
      if(result.cloud)status=copy(result.cloud);
      return copy(result);
    }
    if(payload.action==='access'){
      const item=responses.devices?.devices?.find(item=>item.binding_id===payload.binding_id)||status.bindings.find(item=>item.binding_id===payload.binding_id);
      const result=responses.access||{allowed:payload.allowed,remote_access_confirmed:true,access_error:''};
      if(item){
        item.allowed=payload.allowed&&result.remote_access_confirmed===true;
        item.trusted=true;item.generation=(item.generation||0)+1;
        item.remote_revision=(item.remote_revision||0)+1;
        item.remote_status=payload.allowed?'active':'disabled';item.sync_state=result.remote_access_confirmed?'confirmed':'unconfirmed';
        status.bindings=status.bindings.filter(pin=>pin.binding_id!==item.binding_id);status.bindings.push(copy(item));
      }
      return copy(result);
    }
    return copy(responses[payload.action]||{});
  }
  let request=respond;
  class Clock extends Date{static now(){return now;}}
  const context=vm.createContext({document,Date:Clock,URL,TextEncoder,CustomEvent:class{constructor(type){this.type=type;}},localStorage:{getItem(){return null;},setItem(){}},setInterval:fn=>{timers.push(fn);return timers.length;},window:{confirm:()=>false,bridgeDesktop:{cloud:async payload=>{calls.push(copy(payload));return request(payload);}}}});
  for(const file of ['web/shared/i18n.js','desktop/features/connections/cloud.js'])vm.runInContext(fs.readFileSync(path.join(root,file),'utf8'),context,{filename:file});
  const node=key=>document.querySelector('[data-cloud-'+key+']');
  const flush=()=>new Promise(resolve=>setImmediate(resolve));
  return {calls,responses,respond,node,context,flush,setRequest:fn=>request=fn,setStatus:value=>status=copy(value),update(value){status=copy(value);context.window.CloudPanel.update(copy(value));},language:value=>vm.runInContext('BridgeI18n.setLanguage('+JSON.stringify(value)+')',context),async action(name){await document.querySelector('[data-cloud-action="'+name+'"]').onclick();await flush();},async tick(ms){now+=ms;for(const timer of timers)await timer();await flush();},root:()=>document.querySelector('#cloud-account'),dialog:()=>document.querySelector('#cloud-login-dialog'),async login(){node('open-login').onclick();node('server').value='custom';node('origin').value=account.origin;node('login').value=account.login;node('password').value='synthetic-password';node('login-form').onsubmit({preventDefault(){},stopPropagation(){}});await flush();}};
}

async function signedIn(ui,value=account){ui.update(value);await ui.flush();ui.calls.length=0;}

test('web access starts off and writes only explicit account-scoped permission',async()=>{
  const ui=fixture();await signedIn(ui);ui.language('en');
  const input=ui.node('web-allow');assert.equal(input.checked,false);assert.equal(input.disabled,false);
  input.checked=true;await input.onchange();
  assert.deepEqual(ui.calls,[{action:'web-access',origin:account.origin,account_id:account.account_id,gateway_id:account.gateway_id,allowed:true,expected_revision:0},{action:'capabilities',origin:account.origin}]);
  assert.equal(input.checked,true);assert.match(ui.node('web-state').textContent,/allowed/);
  input.checked=false;await input.onchange();
  assert.equal(input.checked,false);assert.equal(ui.calls[2].expected_revision,1);assert.match(ui.node('result').textContent,/disconnected/);
  ui.language('zh');assert.match(ui.node('web-state').textContent,/[\u4e00-\u9fff]/);
});

test('web permission failures refresh without replay and stale account completions are ignored',async()=>{
  const ui=fixture();await signedIn(ui);ui.language('en');
  ui.setRequest(payload=>{if(payload.action==='web-access')throw Error('request timed out');return ui.respond(payload);});
  ui.node('web-allow').checked=true;await ui.node('web-allow').onchange();
  assert.equal(ui.node('web-allow').checked,false);
  assert.deepEqual(ui.calls.map(c=>c.action),['web-access','status']);
  let complete;ui.setRequest(payload=>payload.action==='web-access'?new Promise(resolve=>complete=resolve):ui.respond(payload));
  ui.node('web-allow').checked=true;const pending=ui.node('web-allow').onchange();
  ui.update({...account,account_id:'other-account'});
  complete({...account,web_access:{allowed:true,revision:1,error:''}});await pending;
  assert.equal(ui.node('web-allow').checked,false);
  assert.doesNotMatch(ui.node('result').textContent,/Web access allowed/);
});

test('a failed persisted disable has an explicit recovery without granting access',async()=>{
  const ui=fixture();await signedIn(ui,{...account,web_access:{allowed:false,revision:3,error:'web_access_storage_error'}});
  assert.equal(ui.node('web-allow').disabled,true);assert.equal(ui.node('web-disable').hidden,false);
  await ui.node('web-disable').onclick();
  assert.equal(ui.calls[0].action,'web-access');assert.equal(ui.calls[0].allowed,false);
  assert.equal(ui.calls[0].expected_revision,3);assert.equal(ui.node('web-disable').hidden,true);
});

test('web address uses the selected server capability once per enabled account and repaint preserves it',async()=>{
  const ui=fixture();ui.responses.capabilities={web_client:{enabled:true,origin:'https://web.example.test:9443'}};
  const enabled={...account,web_access:{allowed:true,revision:1,error:''}};
  ui.update(enabled);await ui.flush();
  assert.deepEqual(ui.calls,[{action:'capabilities',origin:account.origin}]);
  assert.equal(ui.node('web-address').value,'https://web.example.test:9443');assert.equal(ui.node('web-address-panel').hidden,false);
  ui.node('code').value='KEEP-THIS-CODE';ui.language('en');ui.language('zh');ui.update(enabled);await ui.flush();
  assert.equal(ui.calls.length,1);assert.equal(ui.node('code').value,'KEEP-THIS-CODE');
  ui.update(account);assert.equal(ui.node('web-address-panel').hidden,true);assert.equal(ui.node('web-address').value,'');
});

test('web address rejects invalid server origins and only retries after an explicit refresh',async()=>{
  for(const origin of ['http://web.example.test','javascript:alert(1)','https://user:pass@web.example.test','https://web.example.test/path','https://web.example.test?token=secret','https://web.example.test/#fragment','https://WEB.example.test','https://web.example.test\n']){
    const ui=fixture();ui.responses.capabilities={web_client:{enabled:true,origin}};
    const enabled={...account,web_access:{allowed:true,revision:1,error:''}};
    ui.update(enabled);await ui.flush();
    assert.equal(ui.node('web-address').value,'',origin);assert.equal(ui.node('web-address-field').hidden,true);
    assert.equal(ui.node('web-address-refresh').hidden,false);
    ui.language('en');ui.update(enabled);await ui.flush();assert.equal(ui.calls.length,1);
    ui.responses.capabilities={web_client:{enabled:true,origin:'https://bridge.waken.cloudns.be:18787'}};
    await ui.node('web-address-refresh').onclick();assert.equal(ui.node('web-address').value,'https://bridge.waken.cloudns.be:18787');
  }
});

test('late web address reads cannot cross accounts or restore a disabled entry',async()=>{
  const ui=fixture(),pending=[];
  ui.setRequest(payload=>payload.action==='capabilities'?new Promise(resolve=>pending.push(resolve)):ui.respond(payload));
  const enabled={...account,web_access:{allowed:true,revision:1,error:''}};
  ui.update(enabled);
  ui.update({...enabled,origin:'https://other.example.test',account_id:'other-account'});
  pending[1]({web_client:{enabled:true,origin:'https://other-web.example.test'}});await ui.flush();
  pending[0]({web_client:{enabled:true,origin:'https://old-web.example.test'}});await ui.flush();
  assert.equal(ui.node('web-address').value,'https://other-web.example.test');
  ui.update(enabled);ui.update(account);
  pending[2]({web_client:{enabled:true,origin:'https://late-web.example.test'}});await ui.flush();
  assert.equal(ui.node('web-address').value,'');assert.equal(ui.node('web-address-panel').hidden,true);
});

test('unavailable or failed web capability leaves an explicit retry without affecting permission',async()=>{
  for(const fails of [false,true]){
    const ui=fixture();ui.language('en');ui.setRequest(payload=>{if(fails&&payload.action==='capabilities')throw Error('offline');return ui.respond(payload);});
    ui.update({...account,web_access:{allowed:true,revision:1,error:''}});await ui.flush();
    assert.equal(ui.node('web-allow').checked,true);assert.equal(ui.node('web-address').value,'');assert.equal(ui.node('web-address-refresh').hidden,false);
    assert.match(ui.node('web-address-status').textContent,fails?/Could not load/:/has not provided/);
  }
});

test('logged-in cloud accounts hide the sign-in entry and have no manual relay switch',async()=>{
  const ui=fixture();assert.equal(ui.node('open-login').hidden,false);
  await signedIn(ui,{...account,enabled:false});
  assert.equal(ui.node('open-login').hidden,true);assert.equal(ui.node('signin-hint').hidden,true);
  assert.equal(ui.root().querySelector('[data-cloud-action="enabled"]'),null);
  assert.equal(ui.root().querySelector('[data-cloud-action="pairing"]'),null);
  ui.node('open-login').onclick();assert.equal(ui.dialog().open,false);
  assert.deepEqual(ui.calls,[]);
});

test('successful login closes the modal and describes automatic relay eligibility',async()=>{
  const ui=fixture();await ui.login();
  assert.equal(ui.dialog().open,false);assert.equal(ui.node('open-login').hidden,true);
  assert.match(ui.node('result').textContent,/[\u4e00-\u9fff]/);
  ui.language('en');assert.equal(ui.node('result').textContent,'Cloud sign-in completed. Phones on this account appear automatically; allow connections on this desktop.');
  assert.deepEqual(ui.calls.filter(call=>!['devices'].includes(call.action)).map(call=>call.action),['login','status']);
  assert.equal(ui.node('password').value,'');
});

test('the recovery button is outside labels and the password label explicitly targets its input',()=>{
  const ui=fixture(),button=ui.node('forgot-link'),label=ui.node('password-label'),input=ui.node('password');
  for(let node=button.parentElement;node;node=node.parentElement)assert.notEqual(node.tagName,'LABEL');
  assert.equal(label.tagName,'LABEL');assert.ok(input.id);assert.equal(label.getAttribute('for'),input.id);
});

test('eligible service with a stopped gateway has translated automatic-enable guidance',async()=>{
  const ui=fixture();await signedIn(ui,{...account,runtime:{state:'stopped'}});ui.language('en');
  assert.equal(ui.node('state').textContent,'Relay enabled automatically. Start the gateway to connect.');
  ui.language('zh');assert.match(ui.node('state').textContent,/[\u4e00-\u9fff]/);
  assert.equal(ui.root().querySelector('[data-cloud-action="pairing"]'),null);
  assert.deepEqual(ui.calls,[]);
});

test('service status refreshed feedback is translated and language repaint sends nothing',async()=>{
  const ui=fixture();await signedIn(ui);await ui.action('entitlement');
  assert.equal(ui.node('result').textContent,'\u670d\u52a1\u72b6\u6001\u5df2\u5237\u65b0\u3002');
  ui.node('code').value='unsent-test-code';const before=copy(ui.calls);
  ui.language('en');assert.equal(ui.node('result').textContent,'Cloud service status refreshed.');
  ui.language('zh');assert.equal(ui.node('result').textContent,'\u670d\u52a1\u72b6\u6001\u5df2\u5237\u65b0\u3002');
  assert.equal(ui.node('code').value,'unsent-test-code');assert.deepEqual(ui.calls,before);
});

test('service mode, expiry dates and pairing readiness come from the current server state',async()=>{
  const ui=fixture();ui.responses.entitlement={...grant,active:false};await signedIn(ui,{...account,enabled:false,entitlement:ui.responses.entitlement});
  assert.equal(ui.node('redeem').hidden,false);assert.equal(ui.root().querySelector('[data-cloud-action="pairing"]'),null);
  ui.responses.entitlement={...grant,required:false,expires_at:null};await ui.action('entitlement');ui.language('en');
  assert.equal(ui.node('entitlement').textContent,'Active (selfhost)');assert.equal(ui.node('redeem').hidden,true);
  ui.responses.entitlement=copy(grant);await ui.action('entitlement');
  assert.equal(ui.node('entitlement').textContent,'Active until '+new Date(grant.expires_at).toLocaleString('en-US'));
  const count=ui.calls.length;ui.language('zh');
  assert.equal(ui.node('entitlement').textContent,'\u670d\u52a1\u6709\u6548\u671f\u81f3 '+new Date(grant.expires_at).toLocaleString('zh-CN'));assert.equal(ui.calls.length,count);
});

test('confirmed redemption immediately displays refreshed expiry without another redemption',async()=>{
  const ui=fixture();await signedIn(ui);const renewed={...grant,expires_at:'2030-07-01T00:00:00Z'};
  ui.responses.redeem={redeemed:true,entitlement:renewed,refresh_error:'',cloud:{...account,entitlement:renewed}};
  ui.node('code').value='TEST-ONLY-CODE';await ui.action('redeem');ui.language('en');
  assert.equal(ui.node('code').value,'');assert.equal(ui.node('result').textContent,'Service days redeemed. Service status refreshed.');
  assert.equal(ui.node('entitlement').textContent,'Active until '+new Date(renewed.expires_at).toLocaleString('en-US'));
  assert.deepEqual(ui.calls.filter(call=>!['devices'].includes(call.action)).map(call=>call.action),['redeem','status']);
});

test('a confirmed redemption with refresh failure waits for an explicit refresh',async()=>{
  const ui=fixture();await signedIn(ui);
  ui.responses.redeem={redeemed:true,entitlement:null,refresh_error:'network_or_certificate_error',cloud:{...account,enabled:false,entitlement:null,entitlement_error:'network_or_certificate_error'}};
  ui.node('code').value='TEST-ONLY-CODE';await ui.action('redeem');ui.language('en');
  assert.equal(ui.node('result').textContent,'Service days redeemed, but service status could not be refreshed. Refresh status; do not redeem again.');
  ui.language('zh');assert.match(ui.node('result').textContent,/\u4e0d\u8981\u518d\u6b21\u5151\u6362/);
  await ui.tick(5000);
  assert.deepEqual(ui.calls.filter(call=>!['devices'].includes(call.action)).map(call=>call.action),['redeem','status']);
});

test('a failed local status read after confirmed redemption does not misreport the redeemed write',async()=>{
  const ui=fixture();await signedIn(ui);ui.setRequest(payload=>{if(payload.action==='status')throw Error('synthetic status failure');return ui.respond(payload);});
  await ui.action('redeem');ui.language('en');
  assert.equal(ui.node('result').textContent,'Service days redeemed, but service status could not be refreshed. Refresh status; do not redeem again.');
  assert.equal(ui.calls.filter(call=>call.action==='redeem').length,1);
});

test('unknown redemption outcomes never replay on language changes or automatic refresh',async()=>{
  const ui=fixture();await signedIn(ui);ui.setRequest(payload=>{if(payload.action==='redeem')throw Error('Cloud service: network_or_certificate_error');return ui.respond(payload);});
  ui.node('code').value='TEST-ONLY-CODE';await ui.action('redeem');
  ui.language('en');ui.language('zh');await ui.tick(60000);
  assert.equal(ui.calls.filter(call=>call.action==='redeem').length,1);assert.equal(ui.node('code').value,'');
  assert.deepEqual(ui.calls.filter(call=>!['devices'].includes(call.action)).map(call=>call.action),['redeem']);
});

test('background timers never query qualification before or after redemption',async()=>{
  const ui=fixture();await signedIn(ui);
  await ui.tick(60000);const renewed={...grant,expires_at:'2030-08-01T00:00:00Z'};
  ui.responses.redeem={redeemed:true,entitlement:renewed,refresh_error:'',cloud:{...account,entitlement:renewed}};
  await ui.action('redeem');await ui.tick(3600000);ui.language('en');
  assert.equal(ui.node('entitlement').textContent,'Active until '+new Date(renewed.expires_at).toLocaleString('en-US'));
  assert.deepEqual(ui.calls.filter(call=>!['devices'].includes(call.action)).map(call=>call.action),['redeem','status']);
});

test('late service reads cannot cross server, account or gateway identity',async()=>{
  for(const change of [{origin:'https://other.example.test'},{account_id:'other-account'},{gateway_id:'other-gateway'}]){
    const ui=fixture();await signedIn(ui);let finish;
    ui.setRequest(payload=>payload.action==='entitlement'?new Promise(resolve=>finish=resolve):ui.respond(payload));
    const pending=ui.action('entitlement');
    const selfhost={...grant,required:false,expires_at:null};ui.update({...account,...change,entitlement:selfhost});
    finish({...grant,active:false});await pending;ui.language('en');
    assert.equal(ui.node('entitlement').textContent,'Active (selfhost)');assert.equal(ui.node('redeem').hidden,true);
    assert.deepEqual(ui.calls.filter(call=>!['devices'].includes(call.action)).map(call=>call.action),['entitlement']);
  }
});

test('a manual service read is also fenced when the account changes during its response',async()=>{
  const ui=fixture();await signedIn(ui);let finish;
  ui.setRequest(payload=>payload.action==='entitlement'?new Promise(resolve=>finish=resolve):ui.respond(payload));
  const pending=ui.action('entitlement');ui.update({...account,account_id:'new-account',entitlement:{...grant,required:false,expires_at:null}});
  finish({...grant,active:false});await pending;ui.language('en');
  assert.equal(ui.node('entitlement').textContent,'Active (selfhost)');assert.equal(ui.node('result').textContent,'');
  assert.deepEqual(ui.calls.filter(call=>!['devices'].includes(call.action)).map(call=>call.action),['entitlement']);
});

test('logout restores the sign-in button without starting a login or relay operation',async()=>{
  const ui=fixture();await signedIn(ui);await ui.action('logout');
  assert.equal(ui.node('open-login').hidden,false);assert.equal(ui.node('signin-hint').hidden,false);assert.equal(ui.node('account').hidden,true);
  assert.equal(ui.node('identity').textContent,'');assert.equal(ui.node('entitlement').textContent,'');
  ui.language('en');assert.equal(ui.node('result').textContent,'Signed out of the cloud account.');
  ui.language('zh');assert.equal(ui.node('result').textContent,'\u5df2\u9000\u51fa\u4e91\u8d26\u53f7\u3002');
  await ui.tick(60000);assert.deepEqual(ui.calls.filter(call=>!['devices'].includes(call.action)).map(call=>call.action),['logout']);
});

test('logout with expired credentials or a failed remote request displays local completion',async()=>{
  for(const code of ['login_required','authentication_required','network_or_certificate_error']){
    const ui=fixture();await signedIn(ui);ui.responses.logout={remote_logout_confirmed:false,logout_error:code};
    await ui.action('logout');ui.language('en');
    assert.equal(ui.node('account').hidden,true);assert.equal(ui.node('open-login').hidden,false);
    assert.equal(ui.node('result').textContent,'Signed out locally. Cloud session revocation could not be confirmed; direct connections are unchanged.');
    ui.language('zh');assert.match(ui.node('result').textContent,/\u5df2\u5728\u672c\u673a\u9000\u51fa/);
    await ui.tick(60000);assert.deepEqual(ui.calls.filter(call=>!['devices'].includes(call.action)).map(call=>call.action),['logout']);
  }
});

test('logout clearly distinguishes an uncleared credential vault from remote revocation',async()=>{
  const ui=fixture();await signedIn(ui);ui.responses.logout={credential_clear_error:'native_vault_unavailable'};
  await ui.action('logout');ui.language('en');
  assert.equal(ui.node('account').hidden,true);
  assert.equal(ui.node('result').textContent,'Signed out locally, but saved cloud credentials could not be cleared. Check the system credential vault.');
  assert.deepEqual(ui.calls.filter(call=>!['devices'].includes(call.action)).map(call=>call.action),['logout']);
});

test('a legacy logout error is reconciled as local completion only after status confirms it',async()=>{
  const ui=fixture();await signedIn(ui);
  ui.setRequest(payload=>{if(payload.action==='logout'){ui.respond(payload);throw Error('Cloud service: login_required');}return ui.respond(payload);});
  await ui.action('logout');ui.language('en');
  assert.equal(ui.node('account').hidden,true);assert.equal(ui.node('open-login').hidden,false);
  assert.equal(ui.node('result').textContent,'Signed out locally. Cloud session revocation could not be confirmed; direct connections are unchanged.');
  await ui.tick(60000);assert.deepEqual(ui.calls.filter(call=>!['devices'].includes(call.action)).map(call=>call.action),['logout','status']);
});

test('an unconfirmed logout keeps the account visible and never replays the write',async()=>{
  const ui=fixture();await signedIn(ui);
  ui.setRequest(payload=>{if(payload.action==='logout')throw Error('Cloud service: login_required');return ui.respond(payload);});
  await ui.action('logout');ui.language('en');
  assert.equal(ui.node('account').hidden,false);assert.equal(ui.node('open-login').hidden,true);
  assert.match(ui.node('result').textContent,/login_required/);
  ui.language('zh');await ui.tick(60000);
  assert.equal(ui.calls.filter(call=>call.action==='logout').length,1);
});

test('confirmed logout does not depend on another fallible status request',async()=>{
  const ui=fixture();await signedIn(ui);
  ui.setRequest(payload=>{if(payload.action==='status')throw Error('synthetic status failure');return ui.respond(payload);});
  await ui.action('logout');ui.language('en');
  assert.equal(ui.node('account').hidden,true);assert.equal(ui.node('result').textContent,'Signed out of the cloud account.');
  assert.deepEqual(ui.calls.filter(call=>!['devices'].includes(call.action)).map(call=>call.action),['logout']);
});

test('a configured logout response is not accepted as local completion',async()=>{
  const ui=fixture();await signedIn(ui);
  ui.setRequest(payload=>payload.action==='logout'?copy(account):ui.respond(payload));
  await ui.action('logout');ui.language('en');
  assert.equal(ui.node('account').hidden,false);assert.equal(ui.node('open-login').hidden,true);
  assert.equal(ui.node('result').textContent,'The cloud sign-out result could not be confirmed. Refresh status before trying again.');
  assert.deepEqual(ui.calls.filter(call=>!['devices'].includes(call.action)).map(call=>call.action),['logout','status']);
});

test('a logout response after a local signed-out snapshot still renders completion',async()=>{
  const ui=fixture();await signedIn(ui);let finish;
  ui.setRequest(payload=>payload.action==='logout'?new Promise(resolve=>finish=resolve):ui.respond(payload));
  const pending=ui.action('logout');const result=ui.respond({action:'logout'});ui.update(result);
  finish(result);await pending;ui.language('en');
  assert.equal(ui.node('open-login').hidden,false);assert.equal(ui.node('result').textContent,'Signed out of the cloud account.');
  assert.deepEqual(ui.calls.filter(call=>!['devices'].includes(call.action)).map(call=>call.action),['logout']);
});

test('a late logout response cannot clear a different configured account',async()=>{
  const ui=fixture();await signedIn(ui);let finish;
  ui.setRequest(payload=>payload.action==='logout'?new Promise(resolve=>finish=resolve):ui.respond(payload));
  const pending=ui.action('logout');ui.update({...account,account_id:'new-account',login:'new-user'});
  finish({configured:false,enabled:false,logged_out:true,bindings:[]});await pending;
  assert.equal(ui.node('account').hidden,false);assert.match(ui.node('identity').textContent,/new-user/);
  assert.deepEqual(ui.calls.filter(call=>!['devices'].includes(call.action)).map(call=>call.action),['logout']);
});

test('timers after logout cannot restore entitlement or retry cloud work',async()=>{
  const ui=fixture();await signedIn(ui);
  await ui.tick(60000);await ui.action('logout');await ui.flush();ui.language('en');
  assert.equal(ui.node('open-login').hidden,false);assert.equal(ui.node('entitlement').textContent,'');
  assert.equal(ui.node('result').textContent,'Signed out of the cloud account.');
  await ui.tick(60000);assert.deepEqual(ui.calls.filter(call=>!['devices'].includes(call.action)).map(call=>call.action),['logout']);
});

test('account phones appear automatically without a popup and default to no connection',async()=>{
  const ui=fixture();await signedIn(ui);
  ui.responses.devices={devices:[{binding_id:'request-one',device_name:'Test phone',safety_code:'12345678 ABCDEF90 12345678 ABCDEF90 12345678 ABCDEF90 12345678 ABCDEF90',proof_verified:true,mobile_public_key:'mobile',preview_public_key:'preview',allowed:false,remote_status:'pending',remote_revision:0,generation:0}]};
  await ui.tick(2000);
  assert.equal(ui.context.document.querySelector('#cloud-pairing-dialog'),null);
  const toggle=ui.node('devices').querySelector('[data-cloud-allow]');assert.equal(toggle.checked,false);assert.equal(toggle.disabled,false);
  assert.equal(ui.root().querySelector('[data-cloud-qr]'),null);assert.equal(ui.root().querySelector('[data-cloud-action="pairing"]'),null);
  await ui.tick(365*24*60*60*1000);
  assert.match(ui.node('devices').textContent,/Test phone/);
  assert.equal(ui.calls.some(call=>['approve','reject','access'].includes(call.action)),false);
  ui.context.window.confirm=()=>true;
  const allow=ui.node('devices').querySelector('[data-cloud-allow]');allow.checked=true;await allow.onchange();await ui.flush();
  assert.deepEqual(ui.calls.filter(call=>call.action==='access'),[{action:'access',binding_id:'request-one',allowed:true,expected_revision:0,safety_code:'12345678 ABCDEF90 12345678 ABCDEF90 12345678 ABCDEF90 12345678 ABCDEF90'}]);
  assert.equal(ui.node('devices').querySelector('[data-cloud-allow]').checked,true);
});

test('a discovered phone without encrypted proof cannot be enabled or deleted',async()=>{
  const ui=fixture();await signedIn(ui);
  ui.responses.devices={devices:[{binding_id:'unverified',device_name:'Unknown phone',proof_verified:false,mobile_public_key:'unknown',allowed:false,remote_status:'pending'}]};
  await ui.tick(2000);
  assert.equal(ui.node('devices').querySelector('[data-cloud-allow]').disabled,true);
  assert.equal(ui.context.document.querySelector('#cloud-pairing-dialog'),null);
  assert.equal(ui.node('devices').querySelector('button'),null);
  assert.equal(ui.calls.some(call=>['approve','reject','access'].includes(call.action)),false);
});

test('a late directory response cannot undo a confirmed permission change',async()=>{
  const ui=fixture();await signedIn(ui);
  const candidate={binding_id:'late-request',device_name:'Test phone',safety_code:'12345678 ABCDEF90 12345678 ABCDEF90 12345678 ABCDEF90 12345678 ABCDEF90',proof_verified:true,mobile_public_key:'mobile',preview_public_key:'preview',allowed:false,remote_status:'pending',remote_revision:0,generation:0};
  ui.responses.devices={devices:[copy(candidate)]};await ui.tick(2000);let finish,first=true;
  ui.setRequest(payload=>payload.action==='devices'&&first?(first=false,new Promise(resolve=>finish=resolve)):ui.respond(payload));
  const pending=ui.tick(2000);ui.context.window.confirm=()=>true;
  const toggle=ui.node('devices').querySelector('[data-cloud-allow]');toggle.checked=true;await toggle.onchange();await ui.flush();
  finish({devices:[candidate]});await pending;
  assert.equal(ui.node('devices').querySelector('[data-cloud-allow]').checked,true);
});

test('a trusted phone can be disabled while offline and remote uncertainty stays explicit',async()=>{
  const ui=fixture();await signedIn(ui,{...account,runtime:{state:'unavailable'},bindings:[{binding_id:'offline-phone',device_name:'Offline phone',allowed:true,remote_status:'active',remote_revision:7,generation:1,safety_code:'verified'}]});
  const toggle=ui.node('devices').querySelector('[data-cloud-allow]');assert.equal(toggle.disabled,false);assert.equal(toggle.checked,true);
  ui.responses.access={allowed:false,remote_access_confirmed:false,access_error:'network_or_certificate_error'};
  toggle.checked=false;await toggle.onchange();await ui.flush();ui.language('en');
  assert.equal(ui.node('devices').querySelector('[data-cloud-allow]').checked,false);
  assert.match(ui.node('devices').textContent,/Offline phone/);
  assert.equal(ui.node('result').textContent,'Connection is blocked locally. The cloud permission change could not be confirmed; refresh before trying again.');
  assert.equal(ui.calls.filter(call=>call.action==='access').length,1);
});
