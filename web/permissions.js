'use strict';
// Shared presentation only; callers retain their own permission/confirmation flow.
function permissionOption(button,preset,label,help,selected){
  button.className='permission-option';button.type='button';
  button.setAttribute('aria-pressed',String(selected));
  const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');
  svg.setAttribute('viewBox','0 0 24 24');svg.setAttribute('aria-hidden','true');
  const path=document.createElementNS(svg.namespaceURI,'path');
  path.setAttribute('d','M12 3 3 7v5c0 5 9 9 9 9s9-4 9-9V7l-9-4Z'+(preset==='ask'?'M12 8v5m0 3h.01':preset==='auto-review'?'M8 12l3 3 5-6':'M13 7l-4 6h4l-2 4 5-6h-4l1-4'));
  svg.append(path);
  const copy=document.createElement('span'),title=document.createElement('strong'),description=document.createElement('small');
  title.textContent=BridgeI18n.t(label);description.textContent=BridgeI18n.t(help);copy.append(title,description);
  const check=document.createElement('span');check.className='permission-check';check.textContent=selected?'✓':'';check.setAttribute('aria-hidden','true');
  button.append(svg,copy,check);return button;
}
