'use strict';
const os=require('node:os');
function interfaces(value=os.networkInterfaces()){
  const rows=new Map();
  for(const [name,addresses] of Object.entries(value))for(const item of addresses||[]){
    if(item.family!=='IPv4'||item.internal||item.address.startsWith('127.'))continue;
    if(!rows.has(item.address))rows.set(item.address,{address:item.address,name});
  }
  return [...rows.values()].sort((a,b)=>a.name.localeCompare(b.name)||a.address.localeCompare(b.address));
}
module.exports={interfaces};
