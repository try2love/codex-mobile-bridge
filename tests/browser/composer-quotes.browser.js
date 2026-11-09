// Run after the complete production app boots in the isolated bootstrap fixture.
// Clone its real composer, including all controls and styles; never submit a chat.
window.runComposerQuoteLayoutChecks=async function(Quotes=ComposerQuotes){
 const results=[],check=(ok,label)=>{if(!ok)throw Error(label);results.push(label);};
 const settle=()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
 for(const provider of ['codex','claude']){
  const root=document.createElement('section');root.style.cssText='position:fixed;inset:0;background:var(--page);z-index:100;overflow:auto';
  const messages=document.createElement('div');messages.innerHTML='<div class="message assistant"><div class="message-body">Selected reply text for the quote</div></div>';
  const original=document.querySelector(provider==='codex'?'#composer':'.ds-composer');
  check(!!original,provider+' production composer exists');
  const marker=document.createComment('composer test');original.replaceWith(marker);
  const form=original.cloneNode(true);form.querySelectorAll('.composer-quotes,.composer-quote-actions,.composer-quote-selection').forEach(node=>node.remove());
  const tools=form.querySelector('.composer-tools');for(const button of tools.children)button.hidden=false;
  const reminder=tools.querySelector('#notify-button');if(reminder)reminder.textContent='提醒已开';
  const permission=tools.querySelector('#permissions-button')||tools.children[2];permission.textContent='帮我批准';
  const model=tools.querySelector('#model-name,.ds-model-name');model.textContent=provider==='codex'?'gpt-6-astra':'Claude Opus 4.6';
  const effort=tools.querySelector('#model-effort,.ds-model-effort');effort.textContent=' · xhigh';effort.hidden=false;
  const input=form.querySelector('textarea'),container=input.parentElement;input.value='Existing draft';
  form.querySelector('#composer-body,.ds-composer-body').hidden=false;form.classList.remove('is-collapsed');
  root.append(messages,form);document.body.append(root);
  let changes=0;const scope={provider,session:'synthetic'};
  const quotes=new Quotes({messages,input,container,scope:()=>scope,onChange:()=>changes++,onPrompt(){}});
  try{
   await settle();
   const text=messages.querySelector('.message-body').firstChild,range=document.createRange(),selection=getSelection();range.setStart(text,0);range.setEnd(text,13);selection.removeAllRanges();selection.addRange(range);quotes.capture();
   check(!quotes.action.hidden&&quotes.root.hidden,provider+' selection action does not create an empty preview row');
   const actionBox=quotes.action.getBoundingClientRect(),contextBox=tools.querySelector('.context-usage').getBoundingClientRect();
   check(!tools.querySelector('.composer-toggle'),provider+' composer has no collapse control');
   if(tools.clientWidth>720)check(quotes.action.previousElementSibling===tools.querySelector('.context-usage')&&Math.abs(actionBox.top-contextBox.top)<2&&actionBox.left>=contextBox.right,provider+' wide action stays beside context usage');
   else check(actionBox.top>=tools.getBoundingClientRect().bottom&&actionBox.width<tools.clientWidth&&actionBox.height<40,provider+' narrow action occupies one compact row without moving toolbar controls');
   check(actionBox.right<=innerWidth,provider+' action fits viewport');
   const originalStyle=form.style.cssText,selectedAction=quotes.action,selectedSnapshot=quotes.snapshot;
   quotes.action.focus();form.style.width=(tools.clientWidth+(tools.clientWidth>720?8:-8))+'px';form.style.marginLeft=form.style.marginRight='0px';await settle();
   check(document.activeElement===selectedAction&&quotes.snapshot?.text===selectedSnapshot.text&&input.value==='Existing draft',provider+' same-mode pane resize preserves quote focus selection and draft');
   if(innerWidth>=1000){
    form.style.width='700px';await settle();check(quotes.action.parentElement===quotes.selectionRow&&!quotes.selectionRow.hidden,provider+' narrower pane moves action into compact slot');
    form.style.width='900px';await settle();check(quotes.action.previousElementSibling===tools.querySelector('.context-usage')&&quotes.selectionRow.hidden,provider+' wider pane restores action beside context usage');
    check(quotes.action===selectedAction&&quotes.snapshot?.text===selectedSnapshot.text&&input.value==='Existing draft'&&!quotes.quotes.length,provider+' mode crossing preserves selection identity and draft without adding quotes');
   }
   form.style.cssText=originalStyle;await settle();
   quotes.action.dispatchEvent(new Event('pointerdown'));selection.removeAllRanges();quotes.capture();quotes.action.click();
   check(quotes.quotes[0]?.text==='Selected repl'&&changes===1,provider+' pointer selection survives focus collapse');
   check(input.value==='Existing draft',provider+' quoting preserves draft');
   const multiline='First line\nSecond line\n'+('Long quote content '.repeat(30));quotes.restore([{text:multiline,provider}]);
   const details=quotes.root.querySelector('details'),summary=details.querySelector('summary'),full=details.querySelector('.composer-quote-full');
   const style=getComputedStyle(summary),lineHeight=parseFloat(style.lineHeight)||parseFloat(style.fontSize)*1.5;
   check(!details.open&&style.whiteSpace==='nowrap'&&summary.getBoundingClientRect().height<=lineHeight+2,provider+' collapsed multiline quote occupies one line');
   check(summary.getBoundingClientRect().right<=innerWidth,provider+' collapsed preview fits viewport');
   summary.click();check(details.open&&full.textContent===multiline&&full.getBoundingClientRect().height>0,provider+' full quote remains expandable');
   input.focus();check(quotes.action.hidden&&quotes.quotes[0].text===multiline,provider+' input focus hides action while retaining quote');
  }finally{quotes.layoutObserver?.disconnect();quotes.reset();root.remove();marker.replaceWith(original);getSelection().removeAllRanges();}
 }
 return results;
};
