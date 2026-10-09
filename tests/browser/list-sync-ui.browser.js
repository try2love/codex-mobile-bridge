async function runListSyncTests(){
 const sync=window.BridgeListSync,checks=[],original={refresh:sync.refresh,visible:sync.visible,ready:sync.ready},root=sync.root;
 const check=(value,label)=>{if(!value)throw Error(label);checks.push(label);};let calls=0;
 sync.visible=()=>true;sync.ready=()=>true;sync.refresh=async()=>{calls++;};
 try{
  root.scrollTop=0;await sync.update();check(calls===1,'Visible top-of-list updates automatically');
  sync.ready=()=>false;await sync.update();check(calls===1,'Search or in-flight load blocks background refresh');sync.ready=()=>true;
  sync.visible=()=>false;await sync.update();check(calls===1,'Hidden list does not refresh');sync.visible=()=>true;
  await sync.update(true);check(calls===2&&sync.status.textContent.includes('已更新'),'Explicit refresh reports completion');
  sync.refresh=async()=>{throw Error('offline');};await sync.update(true);check(sync.status.textContent.includes('点击重试'),'Network failure exposes a retry control');
  sync.refresh=async()=>{calls++;};sync.status.click();await new Promise(r=>setTimeout(r,0));check(calls===3,'Retry control requests only list data');
  const touch=(type,y)=>{const event=new Event(type,{cancelable:true});Object.defineProperty(event,'touches',{value:type==='touchend'?[]:[{clientX:30,clientY:y}]});root.dispatchEvent(event);};
  touch('touchstart',20);touch('touchmove',110);touch('touchend',110);await new Promise(r=>setTimeout(r,0));check(calls===4,'Pull gesture refreshes the list');
 }finally{Object.assign(sync,original);sync.status.hidden=true;}
 return checks;
}
