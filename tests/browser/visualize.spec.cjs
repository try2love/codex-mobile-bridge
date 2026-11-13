'use strict';
const {test, expect} = require('@playwright/test');
const fragment = `<section id="viz-test" class="viz-grid"><label class="form-label">数量<input id="amount" class="form-range" type="range" min="1" max="10" value="2"></label><output id="result" class="tabular-nums">4</output><button id="follow" class="btn">继续分析</button><button id="volatile" class="btn">0</button><div class="nav nav-pills" role="tablist" aria-label="图表模式"><button id="tab-a" class="nav-link active" role="tab" aria-selected="true" aria-controls="panel-a">数值</button><button id="tab-b" class="nav-link" role="tab" aria-selected="false" aria-controls="panel-b">图形</button></div><div id="panel-a" role="tabpanel">数值面板</div><div id="panel-b" role="tabpanel" hidden>图形面板</div></section>
<script>
const input=document.getElementById('amount'), result=document.getElementById('result');
const saved=window.openai.widgetState?.privateContent?.amount;if(saved)input.value=saved;
function paint(){result.textContent=String(Number(input.value)*2)}paint();
document.getElementById('volatile').onclick=event=>event.target.textContent=String(Number(event.target.textContent)+1);
input.oninput=async()=>{paint();await window.openai.setWidgetState({privateContent:{amount:Number(input.value)}});result.dataset.saved='yes'};
document.getElementById('follow').onclick=()=>window.openai.sendFollowUpMessage({prompt:'分析数量 '+input.value,title:'分析'});
</script>`;
const file = {id:'a'.repeat(64), name:'slider.html', reference:'/fixture/slider.html', references:['/fixture/slider.html'], image:false, visualization:true};
const marker = '\ue200visualize\ue202' + JSON.stringify({path:file.reference, mode:'wide', title:'交互测试'}) + '\ue201';

async function setup(page, context, baseURL, {source=fragment, text=marker, failed=false}={}) {
  const requests = {writes:[], reads:0, blocked:[]};
  await context.route('**/*', route => {
    const request = route.request(), url = new URL(request.url());
    if (url.origin !== baseURL) { requests.blocked.push(url.href); return route.abort(); }
    if (request.method() === 'POST') requests.writes.push(url.pathname);
    return route.continue();
  });
  await page.route('**/api/sessions/*/visualizations/*', route => {
    requests.reads++;
    return route.fulfill(failed && requests.reads === 1 ? {status:503, json:{error:'fixture'}} : {json:{text:source,revision:'b'.repeat(64)}});
  });
  await page.goto('/?case=success');
  await expect(page.locator('#bootstrap-status')).toBeHidden();
  await page.evaluate(async ({marker,file,text}) => {
    await BridgeFeatures.load('chat');
    document.getElementById('login').hidden=true;
    const host=document.createElement('div');host.id='visualize-test-host';host.className='markdown-body';
    host.style.cssText='position:relative;padding:8px;max-width:736px;margin:auto';
    document.body.prepend(host);
    const abort=window.visualizeAbort=new AbortController();
    const context={key:'fixture-chat',url:id=>'/api/sessions/00000000-0000-4000-a000-000000000001/visualizations/'+id,signal:abort.signal,canCompose:()=>!abort.signal.aborted};
    window.visualizePrompt=[];host.addEventListener('bridge-compose-prompt',event=>window.visualizePrompt.push(event.detail.text));
    window.paintVisualize=(text=marker,assistant=true)=>{
      host.replaceChildren();host.__visualizeContext=context;host.__visualizeAssistant=assistant;
      renderMarkdown(host,text,[file],()=>'',{});
    };
    paintVisualize(text);
  }, {marker,file,text});
  return requests;
}

const frame = page => page.frameLocator('.visualize-stage iframe');
test('visualize is interactive, themed, persistent, fullscreen and isolated', async ({page,context,baseURL}) => {
  const requests=await setup(page,context,baseURL,{text:'说明\n'+marker});
  await expect(frame(page).locator('#result')).toHaveText('4');
  await expect(page.getByRole('button',{name:'全屏查看'})).toBeEnabled();
  await expect(page.locator('.visualize-surface[open]')).toHaveCount(0);
  await frame(page).locator('#amount').fill('7');
  await frame(page).locator('#amount').dispatchEvent('input');
  await expect(frame(page).locator('#result')).toHaveText('14');
  await expect(frame(page).locator('#result')).toHaveAttribute('data-saved','yes');
  await frame(page).locator('#tab-b').click();
  await expect(frame(page).locator('#panel-b')).toBeVisible();
  await expect(frame(page).locator('#panel-a')).toBeHidden();
  await page.evaluate(()=>document.documentElement.dataset.theme='dark');
  await expect(frame(page).locator('html')).toHaveAttribute('data-theme','dark');
  expect(await frame(page).locator('body').evaluate(node=>getComputedStyle(node).color)).toBe('rgb(236, 238, 241)');
  expect(await frame(page).locator('#amount').evaluate(()=>{try{return parent.document.body ? 'exposed' : 'missing';}catch{return 'isolated';}})).toBe('isolated');
  await frame(page).locator('#follow').click();
  await expect(page.locator('.visualize-followup')).toBeVisible();
  expect(await page.evaluate(()=>visualizePrompt)).toEqual([]);
  await page.getByRole('button',{name:'放入输入框'}).click();
  expect(await page.evaluate(()=>visualizePrompt)).toEqual(['分析数量 7']);
  expect(requests.writes).toEqual([]);
  await frame(page).locator('#volatile').click();
  await page.getByRole('button',{name:'全屏查看'}).click();
  await expect(page.locator('.visualize-dialog')).toBeVisible();
  await expect(frame(page).locator('#volatile')).toHaveText('1');
  await expect(frame(page).locator('#result')).toHaveText('14');
  await page.locator('.visualize-dialog').getByRole('button',{name:'关闭',exact:true}).click();
  await expect(page.locator('.visualize-dialog')).toHaveCount(0);
  await expect(page.locator('.visualize-surface[open]')).toHaveCount(0);
  await expect(frame(page).locator('#volatile')).toHaveText('1');
  await expect(frame(page).locator('#result')).toHaveText('14');
  await expect(page.locator('.visualize-stage iframe')).toHaveAttribute('sandbox','allow-scripts');
  expect(await page.locator('#visualize-test-host').evaluate(node=>node.scrollWidth<=node.clientWidth)).toBe(true);
});

test('visualize retries source failures and keeps user text and fenced references inert',async({page,context,baseURL})=>{
  const requests=await setup(page,context,baseURL,{failed:true});
  await expect(page.getByRole('button',{name:'重试',exact:true})).toBeVisible();
  await page.getByRole('button',{name:'重试',exact:true}).click();
  await expect(frame(page).locator('#result')).toHaveText('4');
  const count=requests.reads;
  await page.evaluate(()=>paintVisualize('```\n'+String.fromCharCode(0xe200)+'visualize'+String.fromCharCode(0xe202)+'{"path":"/fixture/slider.html"}'+String.fromCharCode(0xe201)+'\n```'));
  await expect(page.locator('.visualize-card')).toHaveCount(0);
  await page.evaluate(({marker})=>paintVisualize(marker,false),{marker});
  await expect(page.locator('.visualize-card')).toHaveCount(0);
  expect(requests.reads).toBe(count);
  expect(requests.writes).toEqual([]);
});

test('visualize CSP blocks APIs, nested frames, and parent navigation while allowing inline scripts',async({page,context,baseURL})=>{
  const hostile=`<output id="security">running</output><script>
  Promise.allSettled([fetch('/api/auth'),fetch('https://example.com/private')]).then(values=>document.getElementById('security').textContent=values.map(value=>value.status).join(','));
  try{top.location='https://example.com/escape'}catch{}
  </script><iframe src="https://example.com/frame"></iframe>`;
  const requests=await setup(page,context,baseURL,{source:hostile});
  await expect(frame(page).locator('#security')).toHaveText('rejected,rejected');
  expect(page.url()).toContain('/?case=success');
  expect(requests.blocked).toEqual([]);
});

test('the production timeline keeps live frames stable and restores widget state after message updates',async({page,context,baseURL})=>{
  await setup(page,context,baseURL);
  await expect(frame(page).locator('#result')).toHaveText('4');
  await page.evaluate(({file,marker})=>{
    visualizeAbort.abort();
    const host=document.getElementById('visualize-test-host');host.replaceChildren();
    const viewport=document.createElement('div'),messages=document.createElement('div'),older=document.createElement('button'),newer=document.createElement('button');
    viewport.style.cssText='height:700px;overflow:auto';viewport.append(older,messages,newer);host.append(viewport);
    const timeline=window.visualizeTimeline=new ChatTimeline({url:action=>'/api/sessions/00000000-0000-4000-a000-000000000001/'+action+'?host=local',request:async()=>({}),renderMeta:()=>{},renderText:(node,text,files)=>renderMarkdown(node,text,files),status:()=>{},elements:{viewport,container:messages,older,newer},cacheKey:'fixture-timeline'});
    const row={key:'viz-live',version:'v1',role:'assistant',order:0,text:marker,turnId:'turn',turnStatus:'completed'};
    window.visualizePage={epoch:'fixture',sequence:1,rows:[row],files:[file],meta:{requests:[]},hasMore:false,before:null};
    timeline.apply(visualizePage,true);
  },{file,marker});
  await expect(frame(page).locator('#result')).toHaveText('4');
  await frame(page).locator('#amount').fill('8');await frame(page).locator('#amount').dispatchEvent('input');
  await expect(frame(page).locator('#result')).toHaveAttribute('data-saved','yes');
  await frame(page).locator('#volatile').click();
  await page.evaluate(()=>visualizeTimeline.apply({...visualizePage,sequence:2},false));
  await expect(frame(page).locator('#volatile')).toHaveText('1');
  await page.evaluate(()=>visualizeTimeline.apply({...visualizePage,sequence:3,rows:[{...visualizePage.rows[0],version:'v2',text:'追加说明\n\n'+visualizePage.rows[0].text}]},false));
  await expect(frame(page).locator('#result')).toHaveText('16');
  await page.evaluate(()=>visualizeTimeline.apply({...visualizePage,sequence:4,rows:[{...visualizePage.rows[0],key:'another-message'}]},true));
  await expect(frame(page).locator('#result')).toHaveText('4');
  await page.evaluate(()=>visualizeTimeline.apply({...visualizePage,sequence:5},true));
  await expect(frame(page).locator('#result')).toHaveText('16');
  await page.getByRole('button',{name:'全屏查看'}).click();
  await expect(page.locator('.visualize-dialog')).toBeVisible();
  await page.evaluate(()=>visualizeTimeline.dispose());
  await expect(page.locator('.visualize-stage iframe')).toHaveCount(0);
  await expect(page.locator('.visualize-dialog')).toHaveCount(0);
});
