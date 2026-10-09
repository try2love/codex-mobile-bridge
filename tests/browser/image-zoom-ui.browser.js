// Run in the isolated Electron renderer; pointer events here test geometry,
// while real touch delivery is checked separately in browser/native QA.
async function runImageZoomTests() {
  const checks=[],check=(value,label)=>{if(!value)throw Error(label);checks.push(label);};
  const settle=()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
  const wait=async fn=>{for(let i=0;i<150;i++){if(fn())return;await new Promise(r=>setTimeout(r,20));}throw Error('Image preview did not settle');};
  const source='/api/sessions/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee/uploads/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee/preview?host=local&variant=original';
  const opener=document.createElement('img');opener.dataset.imagePreview=source;opener.alt='Zoom fixture';opener.tabIndex=0;document.body.append(opener);
  const opened=async target=>{target.click();await wait(()=>document.querySelector('#image-preview-dialog')?.open&&document.querySelector('#image-preview-dialog img')?.naturalWidth>0);await settle();};
  const close=async()=>{document.querySelector('#image-preview-close').click();await settle();};
  await opened(opener);
  const content=document.querySelector('.image-preview-content'),scale=()=>Number(document.querySelector('#image-preview-scale').textContent.replace('%',''))/100;
  const bounds=()=>{const b=content.querySelector('img').getBoundingClientRect(),c=content.getBoundingClientRect();return {left:b.left,top:b.top,width:b.width,height:b.height,cx:c.left+c.width/2,cy:c.top+c.height/2,c};};
  const button=id=>document.querySelector('#image-preview-'+id),fit=async()=>{button('fit').click();await settle();};
  check(button('zoom-in')&&button('zoom-out')&&button('fit')&&button('scale'),'Image preview exposes zoom and fit controls');
  check(scale()===1,'Images initially fit the viewport at 1×');
  const initial=bounds();button('zoom-in').click();await settle();check(scale()>1&&bounds().width>initial.width,'Zoom in enlarges only the image');button('zoom-out').click();await settle();check(scale()===1,'Zoom out returns to the fit minimum');
  const wheel=(x,y,delta)=>content.dispatchEvent(new WheelEvent('wheel',{clientX:x,clientY:y,deltaY:delta,bubbles:true,cancelable:true}));
  const anchor={x:initial.cx+35,y:initial.cy};wheel(anchor.x,anchor.y,-180);await settle();const enlarged=bounds();
  check(Math.abs((anchor.x-initial.left)/initial.width-(anchor.x-enlarged.left)/enlarged.width)<.01,'Wheel zoom keeps the horizontal image point under the cursor');
  check(Math.abs((anchor.y-initial.top)/initial.height-(anchor.y-enlarged.top)/enlarged.height)<.01,'Wheel zoom keeps the vertical image point under the cursor');
  for(let i=0;i<12;i++)wheel(initial.cx,initial.cy,-1000);await settle();check(scale()===8&&button('zoom-in').disabled,'Zoom is capped at 8×');
  for(let i=0;i<12;i++)wheel(initial.cx,initial.cy,1000);await settle();check(scale()===1&&button('zoom-out').disabled,'Zoom is bounded at 1×');
  const captured=new Set(),capture=content.setPointerCapture,release=content.releasePointerCapture,hasCapture=content.hasPointerCapture;
  content.setPointerCapture=id=>captured.add(id);content.hasPointerCapture=id=>captured.has(id);content.releasePointerCapture=id=>captured.delete(id);
  const pointer=(type,id,x,y,pointerType='touch')=>content.dispatchEvent(new PointerEvent(type,{pointerId:id,pointerType,button:0,buttons:type==='pointerup'?0:1,clientX:x,clientY:y,bubbles:true,cancelable:true}));
  pointer('pointerdown',1,initial.cx-40,initial.cy);pointer('pointerdown',2,initial.cx+40,initial.cy);pointer('pointermove',1,initial.cx-80,initial.cy);pointer('pointermove',2,initial.cx+80,initial.cy);await settle();
  check(Math.abs(scale()-2)<.02,'Two pointers enlarge the image according to their distance');
  pointer('pointerup',2,initial.cx+80,initial.cy);const beforePan=bounds();pointer('pointermove',1,initial.cx-55,initial.cy);await settle();check(Math.abs(bounds().left-beforePan.left-25)<1,'Lifting one finger allows continuous single-finger panning');
  pointer('pointercancel',1,initial.cx-55,initial.cy);const cancelled=bounds();pointer('pointermove',1,initial.cx+80,initial.cy);await settle();check(Math.abs(bounds().left-cancelled.left)<1&&!captured.size,'Cancelled pointers stop dragging and release capture');
  pointer('pointerdown',3,initial.cx,initial.cy,'mouse');pointer('pointermove',3,initial.cx+10000,initial.cy+10000,'mouse');pointer('pointerup',3,initial.cx+10000,initial.cy+10000,'mouse');await settle();const clamped=bounds();
  check(clamped.left<=clamped.c.left+13&&clamped.top<=clamped.c.top+13,'Dragging cannot move the image past its content bounds');
  await fit();content.dispatchEvent(new MouseEvent('dblclick',{clientX:initial.cx,clientY:initial.cy,bubbles:true,cancelable:true}));await settle();check(scale()===2,'Double-click switches from fit to 2×');content.dispatchEvent(new MouseEvent('dblclick',{clientX:initial.cx,clientY:initial.cy,bubbles:true,cancelable:true}));await settle();check(scale()===1,'Double-click returns to fit');
  pointer('pointerdown',4,initial.cx,initial.cy);pointer('pointerup',4,initial.cx,initial.cy);pointer('pointerdown',5,initial.cx,initial.cy);pointer('pointerup',5,initial.cx,initial.cy);await settle();check(scale()===2,'Touch double-tap switches from fit to 2×');
  pointer('pointerdown',6,initial.cx,initial.cy);pointer('pointermove',6,initial.cx+25,initial.cy);await close();check(captured.size===0,'Closing releases active pointer capture');
  content.setPointerCapture=capture;content.releasePointerCapture=release;content.hasPointerCapture=hasCapture;
  await opened(opener);check(scale()===1&&Math.abs(bounds().left-initial.left)<1,'Reopening clears the previous zoom, pan and queued frame');await close();check(document.activeElement===opener,'Closing restores keyboard focus to the source image');
  for(const provider of ['claude','deepseek']){opener.dataset.imagePreview='/api/desktop-sessions/'+provider+'/uploads/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee/preview?sessionId=code%3Anative&variant=original';await opened(opener);check(document.querySelector('#image-preview-dialog img').src.includes('/'+provider+'/uploads/'),'Existing '+provider+' draft attachment opens the shared preview');await close();}
  for(const denied of ['https://example.com/photo.png','data:image/png;base64,aGVsbG8=','blob:'+location.origin+'/unregistered','/api/desktop-sessions/other/uploads/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee/preview?sessionId=x','/api/desktop-sessions/claude/uploads/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee/preview','/api/desktop-sessions/claude/uploads/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee/thumb?sessionId=x']){opener.dataset.imagePreview=denied;opener.click();check(!document.querySelector('#image-preview-dialog').open,'Preview rejects unregistered source '+denied);}
  const canvas=document.createElement('canvas');canvas.width=600;canvas.height=400;canvas.getContext('2d').fillRect(0,0,600,400);const data=canvas.toDataURL('image/png').split(',')[1];
  const wb=Object.create(Workbench.prototype),session={id:'a',host:'local',key:'fixture',provider:'claude',files:[],active:'chat',splitTab:'file:preview.png'};
  wb.current=session;wb.sessions=new Map([[session.key,session]]);wb.notify=()=>{};wb.request=async()=>({kind:'image',mime:'image/png',data,name:'preview.png',size:100});wb.select=id=>{session.active=id;const tab=session.files.find(t=>t.id===id);document.body.append(tab.body);};
  await wb.preview(session,{path:'preview.png',name:'preview.png'});const image=session.files[0].body.querySelector('img');await opened(image);check(button('fit')&&session.active==='file:preview.png'&&session.splitTab==='file:preview.png','Workspace image opens the same viewer without changing its file tab or split target');await close();
  check(document.activeElement===image&&image.isConnected,'Closing a workspace image returns focus to its original file tab');
  image.focus();image.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true,cancelable:true}));await wait(()=>document.querySelector('#image-preview-dialog').open);check(document.querySelector('#image-preview-title').textContent==='preview.png','Keyboard opens the registered workspace image');await close();
  const forged=document.createElement('img');forged.dataset.imagePreview=image.src;forged.className='image-preview-source';document.body.append(forged);forged.click();check(!document.querySelector('#image-preview-dialog').open,'Copying a workspace data URL and class cannot bypass registration');
  const svg={kind:'image',mime:'image/svg+xml',data:btoa('<svg xmlns="http://www.w3.org/2000/svg"/>'),name:'vector.svg'};forged.src='data:'+svg.mime+';base64,'+svg.data;check(!BridgeImageViewer.registerWorkspaceImage(forged,svg),'Workspace registration refuses unsupported image MIME types');forged.remove();
  BridgeI18n.setLanguage('en');opener.dataset.imagePreview=source;await opened(opener);check(!/[\u4e00-\u9fff]/.test(button('zoom-in').getAttribute('aria-label')+button('zoom-out').getAttribute('aria-label')+button('fit').textContent),'Zoom controls use the selected English language');await close();BridgeI18n.setLanguage('zh');
  session.files[0].body.remove();opener.remove();return checks;
}
