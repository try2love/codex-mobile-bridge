// Run in the isolated Git fixture after opening its Git panel.
async function runGitPanelTests() {
  const workbench = window.BridgeWorkbench, session = workbench.current, tab = session.files.find(t => t.id === 'git'), checks = [];
  const check = (value, label) => { if (!value) throw Error(label); checks.push(label); };
  const frames = () => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
  const wait = async predicate => { for (let i = 0; i < 200; i++) { if (predicate()) return; await new Promise(r => setTimeout(r, 20)); } throw Error('Git fixture did not settle'); };
  const row = (section, path) => [...tab.body.querySelectorAll('.wb-git-file')].find(n => n.dataset.section === section && n.dataset.path === path);
  check(!!row('staged', 'src/app.py') && !!row('unstaged', 'src/app.py'), 'Same file has separate staged and unstaged entries');
  row('staged', 'src/app.py').click(); await wait(() => tab.body.querySelector('.wb-diff-summary'));
  check(tab.body.querySelector('.wb-git-diff').textContent.includes('Hello, mobile') && !tab.body.querySelector('.wb-git-diff').textContent.includes('Hello, workbench'), 'Staged selection compares HEAD with the index');
  row('unstaged', 'src/app.py').click(); await wait(() => tab.body.querySelector('.wb-diff-summary'));
  check(tab.body.querySelector('.wb-git-diff').textContent.includes('Hello, workbench'), 'Unstaged selection shows working tree changes');
  check(tab.body.querySelector('.wb-diff-line.added .wb-diff-number:nth-child(2)').textContent !== '', 'Added lines display new line numbers');
  row('untracked', 'new-image.png').click(); await wait(() => tab.body.textContent.includes('二进制'));
  check(!!tab.body.querySelector('.wb-git-diff'), 'Binary files keep the panel and show an explanation');
  row('staged', 'notes-renamed.txt').click(); await wait(() => tab.body.textContent.includes('没有文本差异'));
  check(tab.body.textContent.includes('原路径：notes.txt'), 'Renames show original path even without text changes');
  const input = document.getElementById('message'); workbench.select('chat'); await frames();
  input.value = 'Git review draft'; input.dispatchEvent(new Event('input'));
  const timeline = document.getElementById('timeline'); timeline.scrollTop = 80; await frames(); const position = timeline.scrollTop;
  workbench.select('git'); await frames(); workbench.select('chat'); await frames();
  check(input.value === 'Git review draft' && Math.abs(timeline.scrollTop - position) < 2, 'Git tab round trip preserves draft and reading position');
  workbench.select('git'); document.getElementById('back').click();
  check(!document.getElementById('app').classList.contains('chat-open'), 'Header back from Git returns directly to chat list');
  document.getElementById('app').classList.add('chat-open'); workbench.select('git');
  workbench.open(session.id, 'other-host'); check(!workbench.current.files.some(t => t.id === 'git'), 'Git tabs remain isolated by host'); workbench.open(session.id, session.host);
  await tab.git.load(); check(tab.body.querySelector('.wb-git-file.selected')?.dataset.path === 'notes-renamed.txt', 'Refresh restores the selected file when it is still changed');
  await wait(() => tab.body.textContent.includes('没有文本差异'));
  // Detached test panel covers delayed response races and text-only rendering.
  const body = document.createElement('div'), fakeTab = {body}, pending = [];
  const fake = new GitPanel({node: workbench.node.bind(workbench), button: workbench.button.bind(workbench), url:()=>'/fixture', request:()=>new Promise(resolve=>pending.push(resolve))}, session, fakeTab);
  const load = fake.load(); pending.shift()({available:true,branch:'test',commit:'abc',project:'fixture',scope:'.',entries:[{path:'a.txt',index:'M',worktree:'.'},{path:'b.txt',index:'M',worktree:'.'}]}); await load;
  const buttons = body.querySelectorAll('.wb-git-file'); buttons[0].click(); buttons[1].click();
  pending[1]({kind:'diff',text:'--- b.txt\n+++ b.txt\n@@ -1 +1 @@\n-old\n+<img src=x onerror=alert(1)>\n'}); await frames();
  pending[0]({kind:'diff',text:'stale response'}); await frames();
  check(body.textContent.includes('<img src=x') && !body.textContent.includes('stale response'), 'Late diff response cannot overwrite newer selection');
  check(!body.querySelector('img'), 'File content is rendered as text, never HTML');
  const clean = fake.load(); pending[2]({available:true,branch:'test',commit:'abc',project:'fixture',scope:'.',entries:[]}); await clean;
  check(body.textContent.includes('没有未提交'), 'Clean repository has an explicit empty state');
  fake.dispose();
  return checks;
}
