// Execute against the isolated workbench fixture only.
async function runTerminalTests(){
 const wb=window.BridgeWorkbench;wb.terminal();const p=wb.current.files.find(t=>t.id==='terminal').terminal,checks=[];
 const check=(ok,label)=>{if(!ok)throw Error(label);checks.push(label);};
 const wait=async f=>{for(let i=0;i<400;i++){if(f())return;await new Promise(r=>setTimeout(r,25));}throw Error('Terminal did not settle');};
 await wait(()=>!p.run.disabled);check(getComputedStyle(p.tab.body).backgroundColor==='rgb(17, 21, 27)','Terminal has a dark console surface');check(getComputedStyle(p.command).fontSize==='16px','Mobile input stays at a readable non-zooming size');check(p.prompt.textContent==='$','Shell prompt is visible');p.command.value='printf "<script>literal</script>\\n"; pwd';p.form.requestSubmit();await wait(()=>!p.running);
 check(p.output.includes('<script>literal</script>'),'Command output is shown as literal text');check(!p.screen.querySelector('script'),'Output cannot create executable HTML');check(p.output.includes(p.cwd),'Command runs in selected project');
 p.command.value='printf "started\\n"; sleep 60';p.form.requestSubmit();await wait(()=>!p.stop.disabled&&p.output.includes('started'));const id=p.id;
 wb.select('chat');wb.select('terminal');check(p.id===id&&p.running,'Tab switching preserves a running job');await p.cancel();await wait(()=>!p.running);check(p.status.textContent.includes('停止'),'Stop ends the command');
 p.command.value='printf "saved output\\n"';p.form.requestSubmit();await wait(()=>!p.running);
 wb.close('terminal');wb.terminal();const restored=wb.current.files.find(t=>t.id==='terminal').terminal;await wait(()=>restored.output.includes('saved output'));
 check(restored.id===p.id,'Reopening reads the same job instead of executing again');
 return checks;
}
