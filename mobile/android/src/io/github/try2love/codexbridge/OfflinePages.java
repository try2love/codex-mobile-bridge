package io.github.try2love.codexbridge;

import android.app.Activity;
import android.app.AlertDialog;
import android.app.DatePickerDialog;
import android.app.KeyguardManager;
import android.content.Intent;
import android.view.View;
import android.webkit.JavascriptInterface;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import java.io.ByteArrayInputStream;
import java.io.IOException;
import java.text.DateFormat;
import java.util.ArrayList;
import java.util.Calendar;
import java.util.Map;
import java.util.function.BooleanSupplier;
import java.util.List;
import org.json.JSONArray;
import org.json.JSONObject;

/** Presents cached history in a dedicated WebView with a packaged-resource allowlist. */
final class OfflinePages {
 static final int VERIFY_DEVICE=71;
 private final MainActivity owner;
 private final OfflineBridge bridge;
 private Runnable verified, verifiedAfterResume;
 private static final String HOST="https://offline-reader.invalid/";
 private static final String ENTRY=HOST+"offline-reader.html";
 private static final String ASSET_ROOT="offline-shell/";
 private static final Map<String,String> HEADERS=new java.util.HashMap<>();
 static {
  HEADERS.put("Content-Security-Policy","default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self'; connect-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'");
  HEADERS.put("Cache-Control","no-store");HEADERS.put("X-Content-Type-Options","nosniff");
 }
 private Reader reader;
 private int opening;

 private final class Reader {
  final WebView view;
  final long account,epoch;
  final String address,gateway,identity;
  final boolean authenticated;
  final BooleanSupplier current;
  long revision;
  boolean ready,reconnecting;
  long visibility;
  String sessionId="";
  JSONObject anchor;
  Reader(OfflineArchive.Account saved,String address,boolean authenticated,BooleanSupplier current){
   this.account=saved.id;this.epoch=saved.epoch;this.revision=saved.clearRevision;
   this.gateway=saved.gateway;this.identity=saved.identity;this.address=address;
   this.authenticated=authenticated;this.current=current;this.view=new WebView(owner);
  }
 }

 OfflinePages(MainActivity owner,OfflineBridge bridge){this.owner=owner;this.bridge=bridge;}
 private String l(String text){return owner.L(text);}
 private boolean active(){return !owner.isDestroyed()&&!owner.isFinishing();}
 private String[] names(List<OfflineArchive.Account> accounts) {
  return accounts.stream().map(account->account.name).toArray(String[]::new);
 }
 void list(String address,boolean automatic,BooleanSupplier current,Runnable missing) {
  int request=++opening;
  bridge.run(archive->{
   OfflineArchive.Account account=archive.forAddress(address);
   boolean available=archive.hasContent(account);
   owner.runOnUiThread(()->{
    if(!active()||request!=opening||!current.getAsBoolean())return;
    if(!available||automatic&&OfflineArchive.locked(account)){
     if(automatic)missing.run();else owner.message(l("此电脑暂无离线缓存"));
    }else browse(account,address,current);
   });
  });
 }
 void retained() {
  bridge.run(archive->{
   List<OfflineArchive.Account> accounts=archive.available(false);
   accounts.removeIf(account->!archive.hasContent(account));
   owner.runOnUiThread(()->{
    if(!active())return;
    AlertDialog.Builder page=new AlertDialog.Builder(owner).setTitle(l("保留的离线记录"))
      .setNegativeButton(l("关闭"),null).setNeutralButton(l("清除缓存"),(d,w)->clearSelection());
    if(accounts.isEmpty())page.setMessage(l("暂无保留的离线记录"));
    else page.setItems(names(accounts),(d,w)->browse(accounts.get(w),null,()->true));
    page.show();
   });
  });
 }
 private void browse(OfflineArchive.Account account,String address,BooleanSupplier current) {
  if(OfflineArchive.locked(account)){
   new AlertDialog.Builder(owner).setTitle(account.name)
     .setMessage(l("登录状态需要验证。请使用原账号重新连接并打开要恢复的会话。"))
     .setPositiveButton(l("关闭"),null).setNeutralButton(l("清除缓存"),(d,w)->clearSelection()).show();
  }else if("valid".equals(account.status))open(account.id,address,false,current);
  else{
   KeyguardManager keyguard=owner.getSystemService(KeyguardManager.class);
   Intent prompt=keyguard.isDeviceSecure()?keyguard.createConfirmDeviceCredentialIntent(
       l("查看离线聊天"),l("使用设备屏幕锁验证身份")):null;
   if(prompt==null){owner.message(l("请先设置系统屏幕锁"));return;}
   verified=()->open(account.id,address,true,current);
   try{owner.startActivityForResult(prompt,VERIFY_DEVICE);}catch(RuntimeException error){verified=null;owner.message(l("系统身份验证不可用"));}
  }
 }
 void verificationResult(int result) {
  Runnable action=verified;verified=null;
  if(action!=null&&result==Activity.RESULT_OK){if(owner.offlineForeground())action.run();else verifiedAfterResume=action;}
 }
 private boolean canRead(OfflineArchive.Account account,boolean authenticated) {
  return account!=null&&!OfflineArchive.locked(account)&&("valid".equals(account.status)||authenticated);
 }
 private OfflineArchive.Account readAccount(OfflineArchive archive,Reader item) {
  OfflineArchive.Account account=item.address==null?archive.account(item.account):archive.forAddress(item.address);
  return canRead(account,item.authenticated)&&account.id==item.account&&account.epoch==item.epoch?account:null;
 }
 private void open(long id,String address,boolean authenticated,BooleanSupplier current) {
  int request=++opening;
  bridge.run(archive->{
   OfflineArchive.Account account=address==null?archive.account(id):archive.forAddress(address);
   if(!canRead(account,authenticated)||account.id!=id||!archive.hasContent(account))return;
   owner.runOnUiThread(()->{
    if(!active()||!owner.offlineForeground()||request!=opening||!current.getAsBoolean())return;
    final Map<String,String> assets;
    try{assets=shellAssets();}
    catch(Exception badPackage){owner.message(l("无法读取离线缓存"));return;}
    destroyReader();
    Reader opened=new Reader(account,address,authenticated,current);
    reader=opened;
    WebSettings settings=opened.view.getSettings();
    settings.setJavaScriptEnabled(true);
    // The regular chat shell uses local/session storage for navigation and tabs.
    settings.setDomStorageEnabled(true);
    settings.setUserAgentString(settings.getUserAgentString()+" "+owner.mobileUserAgent());
    settings.setAllowFileAccess(false);
    settings.setAllowContentAccess(false);
    settings.setBlockNetworkLoads(true);
    settings.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
    settings.setSupportMultipleWindows(false);
    settings.setCacheMode(WebSettings.LOAD_NO_CACHE);
    opened.view.setWebViewClient(new WebViewClient(){
     @Override public WebResourceResponse shouldInterceptRequest(WebView view,WebResourceRequest request){
      return resource(assets,request.getUrl(),request.isForMainFrame(),request.getMethod());
     }
     @Override public boolean shouldOverrideUrlLoading(WebView view,WebResourceRequest request){return true;}
     @Override public void onPageStarted(WebView view,String url,android.graphics.Bitmap favicon){
      if(!readerDocument(url)&&reader==opened)destroyReader();
     }
    });
    opened.view.addJavascriptInterface(new LocalHost(opened),"OfflineChatHost");
    owner.presentOffline(opened.view);
    opened.view.loadUrl(ENTRY);
   });
  });
 }
 private Map<String,String> shellAssets()throws Exception {
  java.io.ByteArrayOutputStream data=new java.io.ByteArrayOutputStream();
  try(java.io.InputStream in=owner.getAssets().open(ASSET_ROOT+"manifest.json")){
   byte[] block=new byte[4096];int read;
   while((read=in.read(block))!=-1){if(data.size()+read>256*1024)throw new IOException("Manifest too large");data.write(block,0,read);}
  }
  JSONObject entries=new JSONObject(data.toString("UTF-8"));
  Map<String,String> files=new java.util.HashMap<>();
  java.util.Iterator<String> keys=entries.keys();
  while(keys.hasNext()){
   String path=keys.next(),file=entries.getString(path);
   if((!"/".equals(path)&&!safePath(path.startsWith("/")?path.substring(1):""))||!safePath(file))throw new IOException("Invalid manifest");
   files.put(path,file);
  }
  if(!files.containsKey("/offline-reader.html"))throw new IOException("Missing offline entry");
  return files;
 }
 private static boolean safePath(String value){
  return value.matches("[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*")&&!value.matches("(?:^|.*/)\\.{1,2}(?:/.*|$)");
 }
 private WebResourceResponse resource(Map<String,String> assets,android.net.Uri url,boolean mainFrame,String method){
  if("GET".equals(method)&&"https".equals(url.getScheme())&&"offline-reader.invalid".equals(url.getEncodedAuthority())){
   String path=url.getEncodedPath(),file=assets.get(path);
   boolean document="/offline-reader.html".equals(path)||"/index.html".equals(path)||"/".equals(path);
   if(file!=null&&mainFrame==document)try{
    String type=document||path.endsWith(".html")?"text/html":path.endsWith(".css")?"text/css":path.endsWith(".js")?"text/javascript":
      android.webkit.MimeTypeMap.getSingleton().getMimeTypeFromExtension(android.webkit.MimeTypeMap.getFileExtensionFromUrl(file));
    if(type==null)type="application/octet-stream";
    String charset=type.startsWith("text/")||type.endsWith("javascript")?"UTF-8":null;
    return new WebResourceResponse(type,charset,200,"OK",HEADERS,owner.getAssets().open(ASSET_ROOT+file));
   }catch(IOException ignored){}
  }
  return new WebResourceResponse("text/plain","UTF-8",403,"Forbidden",HEADERS,new ByteArrayInputStream(new byte[0]));
 }
 private final class LocalHost {
  private final Reader item;
  LocalHost(Reader item){this.item=item;}
  @JavascriptInterface public void postMessage(String message){
   if(message==null||message.length()>2_000_000)return;
   owner.runOnUiThread(()->request(item,message));
  }
 }
 private static boolean readerDocument(String value){
  if(value==null)return false;
  android.net.Uri url=android.net.Uri.parse(value);
  String path=url.getEncodedPath();
  return "https".equals(url.getScheme())&&"offline-reader.invalid".equals(url.getEncodedAuthority())&&
    ("/offline-reader.html".equals(path)||"/index.html".equals(path)||"/".equals(path));
 }
 private boolean current(Reader item){
  return reader==item&&active()&&owner.offlineForeground()&&item.current.getAsBoolean()&&readerDocument(item.view.getUrl());
 }
 private void request(Reader item,String message) {
  if(!current(item))return;
  try{
   JSONObject input=new JSONObject(message);
   String id=input.getString("id"),action=input.getString("action");
   if(!id.matches("[0-9]{1,16}"))return;
   if("close".equals(action)){close();if(item.address!=null)owner.offlineHome();return;}
   if("reconnect".equals(action)){
    String selected=input.optString("sessionId");
    item.anchor=selected.equals(item.sessionId)?anchor(input.optJSONObject("anchor")):null;
    reconnect(item,id,selected);
    return;
   }
   boolean listing="list".equals(action), attachment="attachment".equals(action), open="attachmentOpen".equals(action);
   boolean composer=OfflineArchive.composerAction(action);
   if(!composer&&!listing&&!attachment&&!open&&!"session".equals(action))return;
   if(listing){item.sessionId="";item.anchor=null;}
   String session=input.optString("sessionId"),before=input.isNull("before")?"":input.optString("before");
   if(!composer&&!listing&&(session.isEmpty()||session.length()>4096||before.length()>4096))return;
   String lang=owner.offlineLanguage();
   long visibility=item.visibility;
   bridge.run(archive->{
    OfflineArchive.Account account=readAccount(archive,item);
    if(account==null){owner.runOnUiThread(()->closeReader(item.account));return;}
    try{
     if(composer){
      JSONObject value=archive.composer(account,action,input);
      owner.runOnUiThread(()->{if(current(item)&&item.visibility==visibility)reply(item,id,value);});
      return;
     }
     if(open){
      android.net.Uri target=archive.exportTransferAttachment(account,input);
      owner.runOnUiThread(()->{
       if(!current(item)||item.visibility!=visibility)return;
       try{reply(item,id,new JSONObject().put("opened",owner.openOfflineTransferAttachment(target)));}catch(Exception ignored){}
      });
      return;
     }
     bridge.scheduleMigration(account,session);
     JSONObject value;
     if(listing){
      JSONArray sessions=new JSONArray();
      for(OfflineArchive.Session row:archive.sessions(account))
       sessions.put(new JSONObject().put("id",row.id).put("title",row.title).put("syncedAt",row.syncedAt));
      JSONObject appearance=null;
      try{appearance=owner.offlineAppearance(item.address);}catch(Exception ignored){}
      if(appearance==null)appearance=archive.appearance(account);
      value=new JSONObject().put("name",account.name).put("lang",lang).put("sessions",sessions).put("composerWritable","valid".equals(account.status))
        .put("appearance",appearance).put("nativeTheme",owner.offlineNativeTheme());
     }else if(attachment){
      JSONObject part=archive.transferAttachment(account,input);
      value=part==null?new JSONObject():part;
     }else value=archive.recordPage(account,session,input);
     owner.runOnUiThread(()->{
      if(!current(item)||item.visibility!=visibility)return;
      if(!attachment){
       item.revision=account.clearRevision;
       item.sessionId=listing?"":session;
       item.ready=true;
      }
      reply(item,id,value);
     });
    }catch(Exception unavailable){owner.runOnUiThread(()->{if(current(item)&&item.visibility==visibility)error(item,id,"无法读取离线缓存");});}
   });
  }catch(Exception invalid){}
 }
 private static JSONObject anchor(JSONObject value)throws org.json.JSONException {
  if(value==null)return null;
  String key=value.optString("key"),turn=value.optString("turnId");
  double offset=value.optDouble("offset");
  if(!key.matches("[A-Za-z0-9_-]{1,128}")||turn.length()>512||!Double.isFinite(offset)||Math.abs(offset)>100_000)return null;
  JSONObject result=new JSONObject().put("key",key).put("turnId",turn).put("offset",offset);
  String question=value.optString("questionKey");
  if(question.matches("[A-Za-z0-9_-]{1,128}"))result.put("questionKey",question);
  return result;
 }
 private void reply(Reader item,String id,JSONObject value){
  item.view.evaluateJavascript("window.OfflineChatReader.receive("+JSONObject.quote(id)+","+value+")",null);
 }
 private void error(Reader item,String id,String text){
  if(id.isEmpty())return;
  try{reply(item,id,new JSONObject().put("error",l(text)));}catch(Exception ignored){}
 }
 boolean back(){
  Reader item=reader;
  if(item==null)return false;
  if(current(item)&&item.ready)item.view.evaluateJavascript("window.OfflineChatReader.back()",null);
  else{close();if(item.address!=null)owner.offlineHome();}
  return true;
 }
 boolean showing(){return reader!=null;}
 void pause(){
  if(reader!=null){reader.visibility++;reader.view.setVisibility(View.INVISIBLE);reader.view.onPause();}
 }
 void resume(){
  Runnable action=verifiedAfterResume;verifiedAfterResume=null;
  if(action!=null)action.run();
  Reader item=reader;
  if(item==null)return;
  bridge.run(archive->{
   OfflineArchive.Account account=readAccount(archive,item);
   owner.runOnUiThread(()->{
    if(reader!=item||!active()||!owner.offlineForeground()||!item.current.getAsBoolean())return;
    if(account==null){closeReader(item.account);return;}
    item.view.onResume();
    if(!item.ready||item.revision!=account.clearRevision){item.revision=account.clearRevision;item.ready=false;item.view.loadUrl(ENTRY);}
    item.view.setVisibility(View.VISIBLE);
    networkChanged();
   });
  });
 }
 void networkChanged(){
  Reader item=reader;
  if(item!=null&&current(item)&&item.ready&&!item.reconnecting)
   item.view.evaluateJavascript("window.OfflineChatReader.reconnect()",null);
 }
 private void reconnect(Reader item,String id,String session) {
  if(!current(item)||item.reconnecting)return;
  if(item.address==null){error(item,id,"登录状态需要验证。请使用原账号重新连接并打开要恢复的会话。");return;}
  item.reconnecting=true;
  String selected=session.equals(item.sessionId)?session:"";
  owner.reconnectOffline(item.address,item.gateway,item.identity,selected,item.anchor,()->current(item),result->{
   item.reconnecting=false;
   if(current(item))error(item,id,result);
  });
 }
 private void destroyReader(){
  Reader item=reader;reader=null;
  if(item==null)return;
  item.view.removeJavascriptInterface("OfflineChatHost");
  item.view.stopLoading();
  owner.dismissOffline(item.view);
  item.view.destroy();
 }
 void close(){opening++;verified=null;verifiedAfterResume=null;destroyReader();}
 void rejected(long id,String status){
  Reader item=reader;
  if(item==null||item.account!=id)return;
  destroyReader();
  if(!owner.offlineForeground()||!"expired".equals(status)&&!"loggedOut".equals(status))return;
  bridge.run(archive->{
   OfflineArchive.Account account=archive.account(id);
   owner.runOnUiThread(()->{
    if(active()&&owner.offlineForeground()&&reader==null&&item.current.getAsBoolean()&&account!=null)
     browse(account,item.address,item.current);
   });
  });
 }
 void closeReader(long id){if(reader!=null&&reader.account==id)destroyReader();}
 void clearSelection() {
  bridge.run(archive->{
   List<OfflineArchive.Account> accounts=archive.available(false);
   owner.runOnUiThread(()->{
    if(!active())return;
    if(accounts.isEmpty()){owner.message(l("没有可清除的离线缓存"));return;}
    boolean[] choices=new boolean[accounts.size()];
    new AlertDialog.Builder(owner).setTitle(l("选择要清除缓存的电脑"))
      .setMultiChoiceItems(names(accounts),choices,(d,w,checked)->choices[w]=checked)
      .setNegativeButton(l("取消"),null).setPositiveButton(l("继续"),(d,w)->{
       List<Long> selected=new ArrayList<>();
       for(int i=0;i<accounts.size();i++)if(choices[i])selected.add(accounts.get(i).id);
       if(!selected.isEmpty())clearDates(selected);
      }).show();
   });
  });
 }
 private void clearDates(List<Long> selected) {
  new AlertDialog.Builder(owner).setTitle(l("按原始提问日期清除"))
    .setItems(new String[]{l("选择日期范围"),l("全部日期")},(d,w)->{
     if(w==1)confirmClear(selected,Long.MIN_VALUE,Long.MAX_VALUE,l("全部日期"));
     else chooseDate(l("开始日期"),Calendar.getInstance(),start->chooseDate(l("结束日期"),start,end->{
      if(end.before(start)){owner.message(l("结束日期不能早于开始日期"));return;}
      Calendar limit=(Calendar)end.clone();limit.add(Calendar.DATE,1);
      DateFormat format=DateFormat.getDateInstance();
      confirmClear(selected,start.getTimeInMillis(),limit.getTimeInMillis(),format.format(start.getTime())+" – "+format.format(end.getTime()));
     }));
    }).setNegativeButton(l("取消"),null).show();
 }
 private void chooseDate(String title,Calendar selected,java.util.function.Consumer<Calendar> next) {
  DatePickerDialog picker=new DatePickerDialog(owner,(view,year,month,day)->{
   Calendar value=Calendar.getInstance();value.clear();value.set(year,month,day);next.accept(value);
  },selected.get(Calendar.YEAR),selected.get(Calendar.MONTH),selected.get(Calendar.DAY_OF_MONTH));
  picker.setTitle(title);picker.show();
 }
 private void confirmClear(List<Long> accounts,long start,long end,String dates) {
  new AlertDialog.Builder(owner).setTitle(l("清除离线缓存？"))
    .setMessage(dates+"\n\n"+l("选定日期内的整轮聊天会从本机缓存移除。"))
    .setNegativeButton(l("取消"),null).setPositiveButton(l("清除"),(d,w)->{
     AlertDialog progress=new AlertDialog.Builder(owner).setMessage(l("正在释放空间…")).setCancelable(false).show();
     bridge.run(archive->{
      try{
       long released=owner.forgetOfflineHistory(archive.legacyScopes(accounts,start,end))+archive.clearTurns(accounts,start,end);
       owner.runOnUiThread(()->{
        if(!active())return;
        progress.dismiss();
        if(reader!=null&&accounts.contains(reader.account))closeReader(reader.account);
        bridge.publish();
        owner.message(String.format(l("清理完成，已释放 %.2f MB"),released/1_000_000.0));
       });
      }catch(Exception error){owner.runOnUiThread(()->{if(active()){progress.dismiss();owner.message(l("无法更新离线缓存，请重试"));}});}
     });
    }).show();
 }
}
