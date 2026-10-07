'use strict';
let sceneRevision=0;
window.demoScene=async scene=>{
 const revision=++sceneRevision;
 const w=window.BridgeWorkbench;if(!w)return;
 if(scene==='home'||scene==='list'){document.getElementById('back').click();return;}
 await openChat(window.demoIds[0],'local');if(revision!==sceneRevision)return;
 if(scene==='chat')return;
 const methods={files:'files',git:'git',terminal:'terminal',side:'sideChat',agents:'agents'};
 if(methods[scene]){w[methods[scene]]();if(new URLSearchParams(location.search).get('platform')==='desktop'){const id={side:'sidechat',agents:'agents',files:'files',git:'git',terminal:'terminal'}[scene];w.split(id);}}
 if(scene==='permissions')document.getElementById('permissions-button').click();
 if(scene==='model')document.getElementById('model-button').click();
};
window.addEventListener('message',e=>{if(e.source!==parent)return;if(e.data?.type==='bridge-demo-scene')window.demoScene(e.data.scene);});
