'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const root=path.resolve(__dirname,'../..');
const android=fs.readFileSync(path.join(root,'mobile/android/src/io/github/try2love/codexbridge/MainActivity.java'),'utf8');
const ios=fs.readFileSync(path.join(root,'mobile/ios/BridgePreview/App.swift'),'utf8');
function method(source,signature){const start=source.indexOf(signature);assert.ok(start>=0);const open=source.indexOf('{',start);let depth=1,end=open+1;for(;depth;end++){if(source[end]==='{')depth++;if(source[end]==='}')depth--;}return source.slice(open+1,end-1);}
function fixture(){
 const pending=new Map(),calls=[],web={},scope={generation:4,web,connectionDeadline:null,hideEntryProgress:()=>calls.push(['hide-progress']),View:{VISIBLE:0},L:value=>value,status:{setVisibility:value=>calls.push(['visible',value]),setText:value=>calls.push(['text',value])},showConnectionError:(url,automatic)=>calls.push(['error',url,automatic]),recoverConnection:url=>calls.push(['recover',url])};
 scope.homeHandler={postDelayed:(fn,ms)=>pending.set(fn,ms),removeCallbacks:fn=>pending.delete(fn)};
 vm.createContext(scope);
 vm.runInContext('function cancelConnectionDeadline(){'+method(android,'void cancelConnectionDeadline()')+'}\nfunction watchConnection(url,current,ticket){'+method(android,'void watchConnection(').replace('()->','()=>')+'}',scope);
 return {scope,calls,pending};
}
test('initial native connection deadline shows a retry path for a document stalled for 30 seconds',()=>{
 const f=fixture();f.scope.watchConnection('https://computer/',f.scope.web,4);
 const [[deadline,ms]]=f.pending;assert.equal(ms,30000);assert.deepEqual(f.calls,[]);
 deadline();assert.equal(f.scope.connectionDeadline,null);
 assert.deepEqual(f.calls,[['error','https://computer/',true]]);
 assert.match(method(android,'void showConnectionError('),/if\(automatic\)offlinePages\.list\(address,true,current,\(\)->recoverConnection\(url\)\)/);
});
test('entry watchdog cannot update another computer, and document commit cancels its timer',()=>{
 for(const invalidate of [scope=>scope.generation++,scope=>scope.web={}]){
  const f=fixture();f.scope.watchConnection('https://old/',f.scope.web,4);const [deadline]=f.pending.keys();invalidate(f.scope);deadline();assert.deepEqual(f.calls,[]);
 }
 const f=fixture();f.scope.watchConnection('https://computer/',f.scope.web,4);f.scope.cancelConnectionDeadline();assert.equal(f.pending.size,0);assert.equal(f.scope.connectionDeadline,null);
 assert.match(method(android,'public void onPageCommitVisible('),/view==web&&!navigationFailed&&GatewayURL.sameOrigin\(target,pageOrigin\).*cancelConnectionDeadline\(\)/s);
});
test('matching retained bindings bypass the network queue and recovery retains every identity guard',()=>{
 const entry=method(android,'void loadWithRecovery(');
 assert.match(entry,/saved==null\|\|saved.cookie\(\).equals\(before\)\)\{current.loadUrl\(url\);return;\}/);
 assert.ok(entry.indexOf('current.loadUrl(url);return;')<entry.indexOf('connectionTask=worker.submit'));
 assert.match(entry,/connectionTask=worker.submit/);
 assert.match(entry,/connectionTask.cancel\(true\)/);assert.match(entry,/ticket!=generation\|\|Thread.currentThread\(\).isInterrupted\(\)/);
 assert.match(entry,/Boolean.FALSE.equals\(existing\)/);assert.match(entry,/Boolean.TRUE.equals\(valid\)/);
 assert.match(entry,/ticket!=generation\|\|web!=current\|\|!address.equals\(origin\)\|\|!saved\(\).contains\(address\)/);
 assert.match(entry,/!before.equals\(EventClient.sessionCookie/);
 assert.match(method(android,'void clear()'),/connectionTask.cancel\(true\)/);
});
test('iOS bounds document entry, ignores stale views, and cannot load late binding recovery over an error view',()=>{
 const connect=method(ios,'private func connect('),commit=method(ios,'didCommit navigation: WKNavigation!'),load=method(ios,'private func loadNative(');
 assert.match(connect,/ticket == self.generation, self.web === web, self.connectionError == nil/);
 assert.match(connect,/\.now\(\) \+ 30/);assert.match(connect,/web.stopLoading\(\); self.showConnectionError\(status: nil\); self.recoverConnection\(\)/);
 assert.match(commit,/webView === web, connectionError == nil/);assert.match(commit,/connectionDeadline\?\.cancel\(\)/);
 assert.match(load,/guard self.web === web, connectionError == nil else \{ return \}/);
 assert.match(method(ios,'private func clear()'),/connectionDeadline\?\.cancel\(\)/);
});

test('both native failure screens offer retry, manage and return without relying on a hidden menu',()=>{
 for(const screen of [method(android,'void showConnectionError('),method(ios,'private func showConnectionError(')]){
  for(const text of ['重试','管理这台电脑','返回电脑列表'])assert.ok(screen.includes(text),text);
  assert.ok(screen.includes('openComputer('));
 }
});

test('Android keeps failure actions over generated network error pages until a new navigation succeeds',()=>{
 assert.match(method(android,'public void onReceivedError('),/navigationFailed=true/);
 assert.match(method(android,'public void onReceivedHttpError('),/navigationFailed=true/);
 assert.match(method(android,'public void onPageFinished(WebView view,String target)'),/!navigationFailed/);
 assert.match(method(android,'public void onPageStarted('),/navigationFailed=false/);
});

test('Android offline reader separates the local document from live gateway navigation',()=>{
 const directory=path.join(root,'mobile/android/src/io/github/try2love/codexbridge');
 const pages=fs.readFileSync(path.join(directory,'OfflinePages.java'),'utf8');
 const archive=fs.readFileSync(path.join(directory,'OfflineArchive.java'),'utf8');
 assert.match(method(pages,'private WebResourceResponse resource('),/getEncodedAuthority\(\).*getEncodedPath\(\).*assets\.get\(path\).*mainFrame==document.*ASSET_ROOT\+file.*403/s);
 assert.match(method(pages,'private void open('),/setAllowFileAccess\(false\).*setAllowContentAccess\(false\).*setBlockNetworkLoads\(true\)/s);
 assert.match(method(pages,'private boolean current('),/readerDocument\(item.view.getUrl\(\)\)/);
 assert.match(method(pages,'private static boolean readerDocument('),/getEncodedAuthority\(\).*getEncodedPath|path=url.getEncodedPath\(\).*getEncodedAuthority\(\)/s);
 assert.match(method(pages,'void networkChanged()'),/window\.OfflineChatReader\.reconnect\(\)/);
 assert.match(method(pages,'private void request('),/listing\)\{item\.sessionId="";item\.anchor=null;\}/);
 assert.match(method(pages,'void pause()'),/visibility\+\+.*View\.INVISIBLE/s);
 assert.match(method(pages,'void resume()'),/readAccount\(archive,item\).*account==null.*setVisibility\(View\.VISIBLE\)/s);
 assert.match(method(android,'void reconnectOffline('),/before\.equals\(after\).*gateway\.equals.*identity\.equals.*EventClient\.verifyOffline\(route,target\[1\].*proof\.value\.optJSONArray\("index"\)==null.*connect\(destination,anchor\)/s);
 assert.match(method(archive,'JSONObject readerSession('),/t\.asked<CAST\(\? AS INTEGER\).*t\.id<\?.*ORDER BY t\.asked DESC,t\.id DESC LIMIT/s);
});

test('Android keeps the private offline anchor out of external URL validation and requires HTTP 200 proof',()=>{
 const client=fs.readFileSync(path.join(root,'mobile/android/src/io/github/try2love/codexbridge/EventClient.java'),'utf8');
 assert.match(method(client,'static JSONObject request('),/getResponseCode\(\)!=200\)throw/);
 assert.match(method(android,'private void connect(String target,JSONObject offlineAnchor)'),/GatewayURL\.targetBase\(target\).*\?offlineAnchor=.*connectDocument\(url,target,base\)/s);
 assert.match(method(android,'public void onPageFinished(WebView view,String target)'),/target\.equals\(offlineTarget\).*epoch==documentEpoch.*GatewayURL\.isChatPage\(loaded,document\).*offlineBridge\.attach/s);
});
