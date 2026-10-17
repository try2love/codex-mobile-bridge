// Run with timeline.js, image-viewer.js and a same-origin synthetic image endpoint.
async function runToolImageTests(imageId) {
  const checks=[],check=(value,label)=>{if(!value)throw Error(label);checks.push(label);};
  const frame=()=>new Promise(resolve=>requestAnimationFrame(resolve));
  const until=async fn=>{const end=performance.now()+4000;while(!fn()){if(performance.now()>end)throw Error('Timed out');await frame();}};
  document.getElementById('messages').replaceChildren();
  delete document.documentElement.dataset.showToolImages;
  let detailCalls=0;
  const timeline=new ChatTimeline({
    url:action=>'/api/sessions/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee/'+action+'?host=local',
    request:async()=>{detailCalls++;return {key:'shot',version:'v1',offset:0,text:'Screenshot ready',next:null};},
    renderMeta:()=>{},renderText:(node,text)=>node.textContent=text,status:()=>{}});
  const row={key:'shot',order:0,version:'v1',role:'activity',kind:'mcpToolCall',title:'cua_repl · js',
    text:'Screenshot ready',truncated:true,attachments:[{type:'toolImage',desktopId:imageId,name:'Screenshot.png'}]};
  const page=(row,sequence)=>({epoch:'images',sequence,rows:[row],before:'images.shot',hasMore:false,meta:{requests:[]},files:[]});
  timeline.apply(page(row,1),true);
  check(!!timeline.nodes.get('shot').node.querySelector('img'),'Tool screenshots are enabled by default');
  const node=timeline.nodes.get('shot').node,img=node.querySelector('img'),details=node.querySelector('details');
  await until(()=>img.complete&&img.naturalWidth>0);
  check(!details.open,'Text details remain collapsed by default');
  check(!img.closest('details')&&img.getBoundingClientRect().height>0,'Screenshot is visible outside collapsed details');
  check(detailCalls===0,'Enabling screenshots does not fetch full text');
  check(img.src.includes('/desktop-images/'+imageId)&&new URL(img.src).searchParams.get('host')==='local','Image uses host-scoped gateway URL');
  node.querySelector('[data-image-preview]').click();
  const dialog=document.getElementById('image-preview-dialog');
  await until(()=>dialog?.open&&dialog.querySelector('img')?.naturalWidth>0);
  check(dialog.open,'Screenshot opens in existing zoom viewer');
  dialog.close();await frame();
  details.open=true;await until(()=>detailCalls===1);
  timeline.apply(page({...row,version:'v2',text:'Updated screenshot'},2));
  check(timeline.nodes.get('shot').node.querySelector('details').open,'Live updates retain expanded tool details');
  check(timeline.container.querySelectorAll('.timeline-attachments img').length===1,'Live updates replace the image row without duplicates');
  timeline.relabel();
  check(timeline.nodes.get('shot').node.querySelector('summary').textContent==='cua_repl · js','Relabeling preserves the gallery and tool heading');
  timeline.setVisibility({showReasoning:true,showProcess:false});
  check(timeline.nodes.get('shot').node.hidden,'Hide process preference also hides tool screenshots');
  timeline.setVisibility({showReasoning:true,showProcess:true});
  check(!timeline.nodes.get('shot').node.hidden,'Restoring process preference restores screenshots');
  timeline.setVisibility({showToolImages:false});
  check(!timeline.nodes.get('shot').node.querySelector('img'),'Disabling screenshots removes existing image previews');
  check(timeline.nodes.get('shot').node.querySelector('details').open,'Disabling screenshots retains expanded tool details');
  timeline.apply(page({...row,version:'v3',text:'Another update'},3));
  check(!timeline.nodes.get('shot').node.querySelector('img'),'Live updates keep screenshot previews disabled');
  const userRow={...row,key:'user-image',order:1,role:'user',truncated:false,attachments:[{type:'image',desktopId:imageId,name:'Upload.png'}]};
  timeline.apply(page(userRow,4));
  check(!!timeline.nodes.get('user-image').node.querySelector('img'),'User images remain visible when tool screenshots are disabled');
  timeline.setVisibility({showToolImages:true});
  check(timeline.nodes.get('shot').node.querySelectorAll('img').length===1,'Re-enabling restores exactly one screenshot');
  timeline.dispose();
  return checks;
}
