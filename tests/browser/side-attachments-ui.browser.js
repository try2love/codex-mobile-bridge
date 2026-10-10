// Synthetic gateway only. Tests actual binary upload, preview, and child-scoped send.
async function runSideAttachmentTests(){
 const wb=window.BridgeWorkbench,checks=[],check=(ok,label)=>{if(!ok)throw Error(label);checks.push(label);};
 wb.sideChat();const panel=wb.current.files.find(t=>t.id==='sidechat').sideChat;await panel.load();if(!panel.state.id)await panel.create();
 const wait=async f=>{for(let i=0;i<200;i++){if(f())return;await new Promise(r=>setTimeout(r,25));}throw Error('Attachment upload did not settle');};
 panel.attachments.clear(panel.attachments.key,panel.attachments.ids());
 const canvas=document.createElement('canvas');canvas.width=200;canvas.height=120;const ctx=canvas.getContext('2d');ctx.fillStyle='#4a61eb';ctx.fillRect(0,0,200,120);ctx.fillStyle='#fff';ctx.font='24px sans-serif';ctx.fillText('Bridge',50,70);
 const blob=await new Promise(r=>canvas.toBlob(r,'image/png'));const file=new File([blob],'preview.png',{type:'image/png'});
 const data=new DataTransfer();data.items.add(file);panel.input.dispatchEvent(new ClipboardEvent('paste',{clipboardData:data,bubbles:true,cancelable:true}));
 await wait(()=>panel.attachments.rows.length===1&&panel.attachments.ready()&&panel.attachmentList.querySelector('img[data-image-preview]')); 
 check(panel.attachments.rows[0].status==='ready','Pasted image uploads through the side-chat scope');
 const img=panel.attachmentList.querySelector('img');img.click();await wait(()=>document.querySelector('#image-preview-dialog')?.open);
 check(document.querySelector('#image-preview-close')&&!document.querySelector('#image-preview-status').hidden,'Draft image opens an in-page preview with a close button');
 document.querySelector('#image-preview-close').click();
 panel.input.value='';panel.controls();check(!panel.sendButton.disabled,'Image-only messages can be sent');await panel.send();await panel.load();
 check(panel.attachments.rows.length===0&&panel.messages.querySelector('img[data-image-preview]'),'Accepted image appears in transcript and clears the draft');
 const image=panel.messages.querySelector('img[data-image-preview]');image.click();await wait(()=>document.querySelector('#image-preview-dialog')?.open);
 check(document.querySelector('#image-preview-title').textContent==='preview.png','Sent image opens using its original filename');document.querySelector('#image-preview-close').click();
 const state=await panel.request();check(state.turns.some(t=>t.messages.some(m=>m.attachments?.some(a=>a.name==='preview.png'))),'A separate state read contains shared attachment metadata');
 return checks;
}
