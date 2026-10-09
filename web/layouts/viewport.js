'use strict';

// Screen and pane sizes are independent of the browser or native App host.
const BridgeLayout=Object.freeze({
  isCompactViewport:width=>width===undefined?matchMedia('(max-width:720px)').matches:width<=720,
  canSplit:width=>width>=720,
  prefersTouchInput:()=>matchMedia('(pointer:coarse)').matches,
});
if(typeof module==='object'&&module.exports)module.exports=BridgeLayout;
