// Run only against the isolated synthetic workbench fixture.
async function runTerminalTests(){
 const wb=window.BridgeWorkbench;wb.terminal();let p=wb.current.files.find(t=>t.id==='terminal').terminal,checks=[];
 const check=(ok,label)=>{if(!ok)throw Error(label);checks.push(label);};
 const wait=async f=>{for(let i=0;i<600;i++){if(f())return;await new Promise(r=>setTimeout(r,25));}throw Error('PTY did not settle: '+p.status?.textContent);};
 const output=()=>{const lines=[];for(let i=0;i<p.term.buffer.active.length;i++)lines.push(p.term.buffer.active.getLine(i)?.translateToString(true)||'');return lines.join('\n');};
 await wait(()=>p.running);await wait(()=>p.term.modes.bracketedPasteMode);
 check(p.location.textContent.endsWith('zsh'),'Server reports actual default zsh');
 check(p.tab.body.classList.contains('wb-terminal-touch'),'Phone uses explicit multiline composer');
 check(getComputedStyle(p.input).fontSize==='16px','Mobile composer avoids input zoom');
 p.input.value="printf 'LINE_%s\\n' one\nprintf 'LINE_%s\\n' two";
 // Keyboard newline must not submit the form or enqueue any input.
 const before=p.cursor;p.input.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true}));
 check(p.input.value.includes('\n')&&!p.flushing&&!p.operations.length,'Mobile newline leaves the command draft unsent');
 p.form.requestSubmit();await wait(()=>output().includes('LINE_one')&&output().includes('LINE_two'));
 check(true,'Multiline input executes only after explicit send');
 p.input.value="mkdir -p terminal-child; cd terminal-child; export BRIDGE_UI_VALUE=kept";p.form.requestSubmit();await wait(()=>!p.flushing);
 p.input.value="printf 'STATE:%s:%s\\n' \"${PWD##*/}\" \"$BRIDGE_UI_VALUE\"";p.form.requestSubmit();await wait(()=>output().includes('STATE:terminal-child:kept'));
 check(true,'Separate sends retain cwd and environment');
 const id=p.id;wb.select('chat');wb.select('terminal');check(p.id===id&&p.running,'Switching tabs keeps the same PTY');
 p.input.value="read reply; printf 'REPLY:%s\\n' \"$reply\"";p.form.requestSubmit();await wait(()=>!p.flushing);
 p.input.value='mobile answer';p.form.requestSubmit();await wait(()=>output().includes('REPLY:mobile answer'));check(true,'Composer can answer a running interactive program');
 p.input.value='sleep 90';p.form.requestSubmit();await wait(()=>!p.flushing);p.enqueue('\x03');await wait(()=>!p.flushing);
 p.input.value="printf 'CONTINUE_%s\\n' ok";p.form.requestSubmit();await wait(()=>output().includes('CONTINUE_ok'));check(true,'Ctrl+C interrupts the job and preserves shell');
 // Simulate a lost HTTP reply after a successful write. Retry must reuse its id.
 const original=wb.request;let lost=true;wb.request=async(url,body,signal)=>{const result=await original(url,body,signal);if(body?.action==='input'&&lost){lost=false;throw Error('fixture lost reply');}return result;};
 try{p.enqueue("printf x >> retry-once\r");await wait(()=>p.inputError);const inputId=p.operations[0].id;p.retry.click();await wait(()=>!p.inputError&&!p.flushing);check(!p.operations.some(x=>x.id===inputId),'Lost input response can be retried with the same id');}finally{wb.request=original;}
 p.input.value="printf 'COUNT:%s\\n' $(wc -c < retry-once | tr -d ' ')";p.form.requestSubmit();await wait(()=>output().includes('COUNT:1'));check(true,'Retry does not execute terminal input twice');
 wb.close('terminal');await wait(()=>p.disposed);
 const closed=await original(p.url('&id='+id+'&after=0'));check(!closed.running,'Closing tab ends its shell');
 return checks;
}
