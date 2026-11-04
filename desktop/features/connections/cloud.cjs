'use strict';

const fields={
  status:[],capabilities:['origin'],entitlement:[],logout:[],
  'signup-code':['origin','login'],'signup-verify':['origin','login','verification_code'],
  devices:[],access:['binding_id','allowed','expected_revision','safety_code'],login:['origin','login','password','name'],
  register:['origin','login','password','registration_token'],
  forgot:['origin','login'],redeem:['code']
};
const limits={origin:2048,login:320,password:1024,name:128,code:128,binding_id:36,verification_code:6,registration_token:43,safety_code:71};

function validateCloud(value={action:'status'}){
  if(!value||Array.isArray(value)||typeof value!=='object')throw Error('Invalid cloud operation');
  const action=value.action||'status',allowed=fields[action];
  if(!allowed||Object.keys(value).some(key=>key!=='action'&&!allowed.includes(key)))throw Error('Invalid cloud operation');
  for(const key of allowed){
    if(key==='name'&&value[key]===undefined)continue;
    if(action==='access'&&key==='safety_code'&&value[key]===undefined)continue;
    if(key==='allowed'){if(typeof value[key]!=='boolean')throw Error('Invalid cloud switch');continue;}
    if(key==='expected_revision'){if(!Number.isSafeInteger(value[key])||value[key]<0)throw Error('Invalid cloud access revision');continue;}
    if(typeof value[key]!=='string'||!value[key].length||value[key].length>limits[key]||value[key].includes('\0'))throw Error('Invalid cloud field: '+key);
  }
  if(value.origin){
    let url;try{url=new URL(value.origin);}catch{throw Error('An exact HTTPS origin is required');}
    if(url.protocol!=='https:'||url.origin!==value.origin||url.href!==value.origin+'/'||url.username||url.password||!/^[\x21-\x7e]+$/.test(value.origin))throw Error('An exact HTTPS origin is required');
  }
  if(value.binding_id&&!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(value.binding_id))throw Error('Invalid cloud binding');
  if(value.safety_code!==undefined&&!/^[0-9A-F]{8}( [0-9A-F]{8}){7}$/.test(value.safety_code))throw Error('Invalid cloud field: safety_code');
  if(action==='signup-verify'&&!/^[0-9]{6}$/.test(value.verification_code))throw Error('Enter the six-digit email verification code.');
  if(action==='register'&&!/^[A-Za-z0-9_-]{43}$/.test(value.registration_token))throw Error('Invalid cloud field: registration_token');
  return {...value,action};
}

module.exports={validateCloud};
