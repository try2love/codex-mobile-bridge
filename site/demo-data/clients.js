'use strict';
// Public examples are isolated from installed clients and contain no credentials.
window.demoClients=['codex','claude','deepseek'].map((id,i)=>({id,name:['Codex','Claude','DeepSeek Harness'][i],enabled:true,installed:true,configured:true,selectable:true,connected:true,running:true,setupStatus:'ready',reason:'已连接桌面'}));
window.demoClientAccounts=provider=>({provider,activeId:provider+'-demo',accounts:[{id:provider+'-demo',name:provider==='claude'?'Claude · Personal':'Harness · Workspace',active:true,kind:provider==='claude'?'oauth':'official',configured:true,usage:provider==='claude'?{limits:[{name:'Claude',windows:[{windowDurationMins:300,remainingPercent:73}]}]}:{balance:{wallets:[{currency:'CNY',remaining:'128.50'}]}}}],supported:true,canAdd:true,canEdit:true,canDelete:true});
window.demoClientSessions=provider=>[0,1].map((i)=>({id:provider+'-demo-'+i,title:demoText(i?'整理发布检查清单':'优化产品首页',i?'Review the release checklist':'Refine the product homepage'),cwd:'/projects/website',backend:provider==='claude'?(i?'cowork':'code'):'code',status:i?'active':'idle',updatedAt:1791644400000-i*600000,requests:[],model:provider==='claude'?'Claude Sonnet':'DeepSeek',effort:'high',permissionMode:'default',contextUsage:provider==='claude'&&i?null:{usedTokens:24800,contextWindow:200000},contextUsageStatus:provider==='claude'&&i?'unsupported':'available',capabilities:{send:true,stop:true,models:true,permissions:true,attachments:true}}));
window.demoClientMessages=new Map();
window.demoClientRequest=(provider,action,body={},sid)=>{
 const sessions=demoClientSessions(provider),session=sessions.find(s=>s.id===sid)||sessions[0];
 const key=provider+':'+session.id;
 if(!demoClientMessages.has(key))demoClientMessages.set(key,[{id:'user-demo',role:'user',text:demoText('帮我整理首页的优化建议。','Review the homepage improvements.')},{id:'assistant-demo',role:'assistant',text:demoText('已整理好三个重点：\n\n1. 保留清晰的导航与内容层级。\n2. 让手机与宽屏都能舒适阅读。\n3. 通过文件、Git 和终端标签检查改动。\n\n这是示例会话，可以继续输入，体验跨设备工作台。','Three priorities are ready:\n\n1. Keep navigation and content hierarchy clear.\n2. Make reading comfortable on phones and wide screens.\n3. Review changes in Files, Git and Terminal tabs.\n\nThis is a sample chat. Send a message to explore the workbench.')}]);
 if(action==='list')return {sessions,connected:true};
 if(action==='detail')return {connected:true,session,capabilities:session.capabilities,messages:demoClientMessages.get(key)};
 if(action==='send'){demoClientMessages.get(key).push({id:'user-'+Date.now(),role:'user',text:body.text},{id:'assistant-'+Date.now(),role:'assistant',text:demoText('收到。这是本地模拟回复，不会调用模型。','Received. This simulated reply does not call a model.')});return {status:'accepted'};}
 if(action==='catalog'||action==='account')return {models:[{id:session.model,name:session.model,efforts:['low','medium','high']}],currentModel:session.model,currentEffort:'high',skills:[]};
 if(action==='accounts')return demoClientAccounts(provider);
 if(action==='access')return {mode:'default',options:[{value:'default',label:demoText('默认权限','Default permissions')},{value:'acceptEdits',label:demoText('允许文件编辑','Allow file edits')}]};
 if(action==='notifications')return {requests:true,completion:true,available:true};
 if(action==='projects')return {projects:[{id:'website',name:'website',cwd:'/projects/website'}],surfaces:provider==='claude'?['code','cowork']:['code']};
 throw Error(demoText('此操作未在演示中开放；不会修改真实数据。','This operation is unavailable in the demo; real data is never changed.'));
};
