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
