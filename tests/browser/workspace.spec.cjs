'use strict';
const {test, expect} = require('@playwright/test');
const {readFileSync} = require('node:fs');
const path = require('node:path');

test.beforeEach(async ({page, context, baseURL}) => {
  await context.route('**/*', route => new URL(route.request().url()).origin === baseURL ? route.continue() : route.abort());
  await page.goto('/?case=success');
  await page.waitForFunction(() => document.getElementById('bootstrap-status')?.hidden);
  await page.evaluate(readFileSync(path.join(__dirname,'../../web/features/workspace/git-panel.js'),'utf8')+'\nwindow.PagedGitPanel=GitPanel;');
  await page.evaluate(() => {
    const node=(tag,cls='',text)=>{const n=document.createElement(tag);n.className=cls;if(text!==undefined)n.textContent=text;return n;};
    const body=node('div');body.id='paged-git-fixture';body.style.cssText='position:fixed;inset:0;background:var(--page);z-index:99999;overflow:auto';document.body.append(body);
    const session={host:'local',files:[]},tab={body};session.files.push(tab);
    window.gitCalls=[];window.gitFixtureKind='folder';window.gitFixtureStale=false;
    const staged=Array.from({length:240},(_,i)=>({path:'file-'+String(i).padStart(3,'0')+'.txt',index:'A',worktree:'.'}));
    const base={available:true,branch:'main',project:'fixture',scope:'.',version:'canonical',commit:'abc'};
    const workbench={node,raw:n=>n,button(text,action,label){const n=node('button','',text);n.onclick=action;if(label)n.setAttribute('aria-label',label);return n;},url:(_,op)=>'/fixture?operation='+op,notify:()=>{},
      async request(url,body) {
        const q=new URL(url,location.href).searchParams;gitCalls.push({query:Object.fromEntries(q),body});
        if(body)return {outcome:'success',message:'done'};
        if(gitFixtureStale&&q.has('version'))throw Error('仓库已发生变化，请刷新并核对后继续查看');
        const offset=Number(q.get('offset')||0);
        if(gitFixtureKind==='staged')return {...base,counts:{total:240,staged:240,unstaged:0,untracked:0,conflict:0},entries:staged.slice(offset,offset+200),total:240,offset,nextOffset:offset?null:200};
        if(q.get('untrackedPath'))return {...base,counts:{total:10000,staged:0,unstaged:0,untracked:10000,conflict:0},entries:Array.from({length:200},(_,i)=>({path:'bulk/'+String(offset+i)+'.txt',index:'?',worktree:'?',untracked:true})),total:10000,offset,nextOffset:offset+200};
        return {...base,counts:{total:10000,staged:0,unstaged:0,untracked:10000,conflict:0},entries:[{path:'bulk/',index:'?',worktree:'?',untracked:true,directory:true,count:10000}],total:1,offset:0,nextOffset:null};
      }};
    window.pagedGit=new PagedGitPanel(workbench,session,tab);pagedGit.mode='changes';
  });
});

test('large Git lists expand directories and paginate without staging synthetic paths',async({page})=>{
  await page.evaluate(()=>pagedGit.load());
  const root=page.locator('#paged-git-fixture');
  await expect(root.locator('.wb-git-file')).toHaveCount(1);
  await expect(root).toContainText('10000');
  await expect(root.getByRole('button',{name:'暂存 bulk/',exact:true})).toHaveCount(0);
  await root.getByRole('button',{name:'展开未跟踪目录 bulk/',exact:true}).click();
  await expect(root.locator('.wb-git-file')).toHaveCount(200);
  await expect(root.locator('.wb-git-pages')).toContainText('1–200 / 10000');
  await root.getByRole('button',{name:'下一页',exact:true}).click();
  await expect(root.locator('.wb-git-pages')).toContainText('201–400 / 10000');
  expect(await page.evaluate(()=>gitCalls.at(-1).query)).toMatchObject({limit:'200',offset:'200',untrackedPath:'bulk/',version:'canonical'});
  expect(await page.evaluate(()=>gitCalls.some(call=>call.body))).toBe(false);
  await root.getByRole('button',{name:'返回项目变更',exact:true}).click();
  await expect(root.locator('.wb-git-file')).toHaveCount(1);
  expect(await root.evaluate(n=>n.scrollWidth<=n.clientWidth+1)).toBe(true);
});

test('commit review covers all staged pages and rejects a stale preview',async({page})=>{
  await page.evaluate(async()=>{gitFixtureKind='staged';await pagedGit.load();});
  const root=page.locator('#paged-git-fixture');
  await root.getByRole('button',{name:'下一页',exact:true}).click();
  await expect(root.locator('.wb-git-file')).toHaveCount(40);
  await root.getByRole('button',{name:'提交 · 240',exact:true}).click();
  const dialog=page.locator('.wb-git-dialog');
  await expect(dialog.locator('li')).toHaveCount(200);
  await expect(dialog).toContainText('整个项目');
  await dialog.getByRole('button',{name:'加载更多',exact:true}).click();
  await expect(dialog.locator('li')).toHaveCount(240);
  await dialog.getByRole('textbox',{name:'提交说明'}).fill('all staged files');
  await dialog.getByRole('button',{name:'确认提交',exact:true}).click();
  await expect(dialog).toHaveCount(0);
  expect(await page.evaluate(()=>gitCalls.filter(c=>c.body).map(c=>c.body))).toEqual([{action:'commit',message:'all staged files',version:'canonical',limit:200}]);
  await page.evaluate(()=>{gitFixtureStale=true;pagedGit.commitDialog();});
  await expect(dialog.locator('.wb-status.error')).toContainText('仓库已发生变化');
  await expect(dialog.getByRole('button',{name:'确认提交',exact:true})).toBeDisabled();
  expect(await page.evaluate(()=>gitCalls.filter(c=>c.body).length)).toBe(1);
});

test('upload follows the viewed host directory even without a project or at a filesystem root',async({page})=>{
  await page.evaluate(readFileSync(path.join(__dirname,'../../web/features/workspace/workbench.js'),'utf8')+'\nwindow.FileWorkbench=Workbench;');
  await page.evaluate(async()=>{
    const wb=window.fileWorkbench=Object.create(FileWorkbench.prototype),body=document.createElement('div');body.id='file-host-fixture';body.style.cssText='position:fixed;inset:0;background:var(--page);z-index:99999;overflow:auto';document.body.append(body);
    const tab=window.fileTab={id:'files',body},session=window.fileSession={id:'thread',host:'remote:ssh',key:'fixture',directory:'/outside',project:'',search:'',hidden:false,files:[tab]};
    Object.assign(wb,{current:session,sessions:new Map([['fixture',session]]),stopThumbnails(){},refreshThumbnails(){},notify(){},uploads:[],listQueries:[],
      async request(url){const params=new URL(url,location.href).searchParams;this.listQueries.push(Object.fromEntries(params));const path=params.get('path')||'/outside';return {path,project:'',projectPath:null,parentPath:path==='/'?null:'/',entries:[],total:0,nextOffset:null};},
      async upload(url,file){this.uploads.push({query:Object.fromEntries(new URL(url,location.href).searchParams),name:file.name});return {name:file.name};}
    });
    await wb.directory(session,tab,'/outside');
  });
  const root=page.locator('#file-host-fixture');
  await expect(root.getByRole('button',{name:'上传',exact:true})).toBeEnabled();
  for(const target of ['/outside','/']){
    if(target==='/')await page.evaluate(()=>fileWorkbench.directory(fileSession,fileTab,'/'));
    await root.getByRole('button',{name:'上传',exact:true}).click();
    await expect(page.locator('.wb-upload-target')).toHaveText('保存到：'+target);
    await page.getByLabel('选择上传文件',{exact:true}).setInputFiles({name:'test.txt',mimeType:'text/plain',buffer:Buffer.from('hello')});
    await page.getByRole('button',{name:'上传到此目录',exact:true}).click();
    await expect(page.locator('.wb-upload')).toHaveCount(0);
  }
  expect(await page.evaluate(()=>fileWorkbench.uploads)).toEqual([
    {query:{host:'remote:ssh',path:'/outside/test.txt',scope:'host'},name:'test.txt'},
    {query:{host:'remote:ssh',path:'/test.txt',scope:'host'},name:'test.txt'}]);
  await expect(root.getByLabel('文件排序')).toHaveValue('name-asc');
  expect(await root.locator('.wb-file-summary').evaluate(n=>n.children[0].classList.contains('wb-status')&&n.children[1].tagName==='SELECT'&&n.children[2].tagName==='BUTTON')).toBe(true);
  for(const sort of ['name-asc','name-desc','modified-asc','modified-desc','size-asc','size-desc']){
    await root.getByLabel('文件排序').selectOption(sort);
    await expect(root.getByLabel('文件排序')).toHaveValue(sort);
    expect(await page.evaluate(()=>fileWorkbench.listQueries.at(-1))).toMatchObject({sort,offset:'0',path:'/'});
  }
  expect(await root.evaluate(n=>n.scrollWidth<=n.clientWidth+1)).toBe(true);
});

test('both session lists display last user input as HH:mm MM/dd even today',async({page})=>{
  const result=await page.evaluate(()=>{
    const input=new Date();input.setHours(3,4,0,0);
    const pad=n=>String(n).padStart(2,'0'),expected='03:04 '+pad(input.getMonth()+1)+'/'+pad(input.getDate());
    listRows=[{id:'clock-fixture',host:'local',title:'Clock fixture',projectName:'Project',projectKey:'local|test',recency:Date.now(),lastUserInputAt:input.toISOString()}];
    renderList();const codex=document.querySelector('#sessions .session time').textContent;
    listRows[0].recency+=3600000;renderList();const afterOutput=document.querySelector('#sessions .session time').textContent;
    listRows[0].lastUserInputAt=null;renderList();const missing=document.querySelector('#sessions .session time').textContent;
    desktopSessions.provider='deepseek';desktopSessions.rows=[{id:'native-clock',title:'Clock fixture',cwd:'/project',updatedAt:Date.now()+3600000,lastUserInputAt:input.getTime()}];
    desktopSessions.renderList();const native=desktopSessions.rowsRoot.querySelector('time').textContent;
    return {codex,afterOutput,missing,native,expected};
  });
  expect(result.codex).toBe(result.expected);expect(result.native).toBe(result.expected);
  expect(result.afterOutput).toBe(result.expected);expect(result.missing).toBe('');
});
