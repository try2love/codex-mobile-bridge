'use strict';
const pairToken=new URLSearchParams(location.hash.slice(1)).get('pair');
history.replaceState(null,'',location.pathname);
const form=document.getElementById('pair-form'),statusText=document.getElementById('pair-status');
let claim=null;
async function request(path,value){const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(value),credentials:'same-origin'});const data=await r.json();if(!r.ok)throw Error(data.error);return data;}
async function check(){
  if(!claim)return;
  try{const result=await request('/relay/claim-status',{claim});
    if(result.state==='approved'){claim=null;location.replace('/');return;}
    if(result.state!=='pending'){claim=null;statusText.textContent='连接申请已拒绝或失效，请在电脑上重新配对。';return;}
    statusText.textContent='等待电脑批准，请核对电脑上显示的手机名称。';setTimeout(check,2500);
  }catch(error){claim=null;statusText.textContent=error.message;}
}
form.hidden=!pairToken;
form.onsubmit=async event=>{event.preventDefault();form.querySelector('button').disabled=true;try{const result=await request('/relay/claim',{token:pairToken,name:document.getElementById('phone-name').value});claim=result.claim;await check();}catch(error){statusText.textContent=error.message;}};
fetch('/api/auth',{credentials:'same-origin'}).then(r=>r.json()).then(value=>{document.getElementById('paired-link').hidden=!value.authenticated;}).catch(()=>{});
