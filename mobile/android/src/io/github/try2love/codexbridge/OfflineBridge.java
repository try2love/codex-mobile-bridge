package io.github.try2love.codexbridge;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.net.ConnectivityManager;
import android.net.NetworkCapabilities;
import android.net.Network;
import java.util.Objects;
import java.util.UUID;
import android.net.Uri;
import android.webkit.CookieManager;
import android.util.Base64;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.util.Arrays;
import java.util.HashSet;
import java.util.Set;
import android.webkit.WebMessage;
import android.webkit.WebMessagePort;
import android.webkit.WebView;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.function.BooleanSupplier;
import java.util.function.Consumer;
import org.json.JSONObject;

/** The Android port is transferred directly to the active document's main frame. */
final class OfflineBridge {
 private final MainActivity owner;
 private final ExecutorService work=Executors.newSingleThreadExecutor();
 private final OfflineArchive archive;
 private final ExecutorService transfer=Executors.newSingleThreadExecutor();
 private boolean transferActive;
 private volatile boolean closing;
 private final Set<Long> migrating=new HashSet<>();
 private final java.util.Map<Long,String> migrationFocus=new java.util.HashMap<>();
 private static final Set<String> TRANSFER_COMMANDS=new HashSet<>(Arrays.asList(
    "transferStats","transferTaskLoad","transferTaskSave","transferCached","transferChunkHas","transferChunkGet",
    "transferChunkPut","transferChunkFetch","transferRecordCommit","transferAttachmentCommit","transferSessionComplete","transferAttachment","transferAttachmentOpen"));
 private volatile Link link;
 private volatile boolean foreground, eligible, transferAllowed, networkAvailable;
 private volatile String networkEpoch=UUID.randomUUID().toString();
 private Network transferNetwork;
 private boolean previousMetered;
 private int previousRestriction=-1;
 private boolean watching;
 private final BroadcastReceiver dataSaver=new BroadcastReceiver(){
  @Override public void onReceive(Context context,Intent intent){networkChanged();}
 };

 private static final class Link {
  final WebMessagePort port;
  final String address,name;
  final BooleanSupplier current;
  boolean ready;
  String verifiedCookie="",legacyLogin="";
  OfflineArchive.Account account;
  Link(WebMessagePort port,String address,String name,BooleanSupplier current){
   this.port=port;this.address=address;this.name=name;this.current=current;
  }
 }
 OfflineBridge(MainActivity owner){this.owner=owner;archive=new OfflineArchive(owner.getApplicationContext());}
 void attach(WebView view,String address,String name,String token,BooleanSupplier current) {
  if(link!=null||!current.getAsBoolean())return;
  WebMessagePort[] channel=view.createWebMessageChannel();
  Link local=new Link(channel[0],address,name,current);
  link=local;
  channel[0].setWebMessageCallback(new WebMessagePort.WebMessageCallback(){
   @Override public void onMessage(WebMessagePort port,WebMessage message) {
    if(link!=local||!current.getAsBoolean())return;
    String value=message.getData();
    if("ready".equals(value)){local.ready=true;publish();return;}
    if(!local.ready||!foreground||value==null||value.length()>2_000_000)return;
    try {
     JSONObject command=new JSONObject(value);
     String id=command.getString("requestId");
     if(!id.matches("[0-9]{1,16}"))return;
     if("state".equals(command.optString("type"))||"auth".equals(command.optString("type"))) {
      work.execute(()->handle(local,command,id));
     } else if(view.hasWindowFocus()) work.execute(()->handle(local,command,id));
    }catch(Exception ignored){}
   }
  });
  view.postWebMessage(new WebMessage(token,new WebMessagePort[]{channel[1]}),Uri.parse(GatewayURL.httpOrigin(address)));
 }
 void detach(){
  Link previous=link;link=null;
  if(previous!=null)previous.port.close();
 }
 void foreground(boolean value) {
  foreground=value;
  if(value&&!watching){
   IntentFilter filter=new IntentFilter(ConnectivityManager.ACTION_RESTRICT_BACKGROUND_CHANGED);
   if(android.os.Build.VERSION.SDK_INT>=33)owner.registerReceiver(dataSaver,filter,Context.RECEIVER_NOT_EXPORTED);
   else owner.registerReceiver(dataSaver,filter);
   watching=true;
  }else if(!value&&watching){owner.unregisterReceiver(dataSaver);watching=false;}
  networkChanged();
 }
 boolean networkAvailable(){return networkAvailable;}
 void networkChanged() {
  ConnectivityManager manager=owner.getSystemService(ConnectivityManager.class);
  Network active=manager.getActiveNetwork();
  NetworkCapabilities path=manager.getNetworkCapabilities(active);
  boolean metered=manager.isActiveNetworkMetered();
  int restriction=manager.getRestrictBackgroundStatus();
  if(!Objects.equals(transferNetwork,active)||previousMetered!=metered||previousRestriction!=restriction){
   networkEpoch=UUID.randomUUID().toString();
   transferNetwork=active;previousMetered=metered;previousRestriction=restriction;
  }
  networkAvailable=path!=null;
  transferAllowed=path!=null&&(path.hasTransport(NetworkCapabilities.TRANSPORT_WIFI)||path.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET))
      &&path.hasCapability(NetworkCapabilities.NET_CAPABILITY_NOT_METERED)&&!metered
      &&restriction==ConnectivityManager.RESTRICT_BACKGROUND_STATUS_DISABLED;
  eligible=foreground&&transferAllowed&&path.hasTransport(NetworkCapabilities.TRANSPORT_WIFI);
  publish();
 }
 private JSONObject state(OfflineArchive.Account account,String session)throws Exception {
  JSONObject result=new JSONObject().put("recordPages",1).put("foreground",foreground).put("allowPrefetch",eligible&&foreground).put("networkAvailable",networkAvailable).put("allowTransfer",transferAllowed).put("networkEpoch",networkEpoch);
  if(account!=null){
   result.put("clearRevision",Long.toString(account.clearRevision)).put("tombstones",archive.tombstones(account,session));
   if(!session.isEmpty())result.put("hasCachedSession",archive.hasCachedSession(account,session));
  }
  return result;
 }
 void publish() {
  Link local=link;
  if(local==null||!local.ready)return;
  try{send(local,new JSONObject().put("state",state(null,"")));}catch(Exception ignored){}
 }
 private void send(Link local,JSONObject reply) {
  if(link==local&&local.current.getAsBoolean())local.port.postMessage(new WebMessage(reply.toString()));
 }
 private static boolean same(OfflineArchive.Account account,String gateway,String identity) {
  return account!=null&&account.gateway.equals(gateway)&&account.identity.equals(identity);
 }
 private void handle(Link local,JSONObject command,String id) {
  JSONObject reply=new JSONObject();
  try {
   reply.put("id",id);
   if(link!=local||!foreground)return;
   String type=command.getString("type"),gateway=command.optString("gatewayId"),identity=command.optString("identity");
   JSONObject result;
   if("auth".equals(type)&&!"valid".equals(command.optString("status"))) {
    String status=command.getString("status");
    if(!status.equals("expired")&&!status.equals("loggedOut")&&!status.equals("revoked")&&!status.equals("unknownRejected"))throw new IllegalArgumentException();
    OfflineArchive.Account account=archive.forAddress(local.address);
    if(same(account,gateway,identity)||(gateway.isEmpty()&&identity.isEmpty()))setStatus(account,status);
    local.verifiedCookie="";local.account=null;result=state(null,"");
   }else{
    if(gateway.isEmpty()||identity.isEmpty()||gateway.length()>256||identity.length()>256)throw new IllegalArgumentException();
    if(!TRANSFER_COMMANDS.contains(type)&&!OfflineArchive.composerAction(type)&&!"auth".equals(type)&&!"state".equals(type)&&!"merge".equals(type)&&!"read".equals(type)&&!"list".equals(type)&&!"session".equals(type)&&!"appearance".equals(type))throw new IllegalArgumentException();
    OfflineArchive.Account account=authorize(local,gateway,identity,"auth".equals(type));
    if(link!=local||!foreground)return;
    if(account==null)throw new IllegalStateException("Login required");
    if(OfflineArchive.composerAction(type)){
     reply.put("ok",true).put("value",archive.composer(account,type,command));
     owner.runOnUiThread(()->send(local,reply));
     return;
    }
    if("transferChunkFetch".equals(type)){
     fetchTransferChunk(local,account,command,id);
     return;
    }
    if("transferAttachmentOpen".equals(type)){
     Uri target=archive.exportTransferAttachment(account,command);
     owner.runOnUiThread(()->{
      boolean opened=link==local&&local.current.getAsBoolean()&&owner.openOfflineTransferAttachment(target);
      try{transferReply(local,id,new JSONObject().put("opened",opened));}catch(Exception ignored){}
     });
     return;
    }
    scheduleMigration(account,command.optString("sessionId"));
    JSONObject appearance=command.optJSONObject("appearance");
    if(("auth".equals(type)||"appearance".equals(type))&&appearance!=null)archive.saveAppearance(account,appearance);
    if("merge".equals(type)){
     String source=command.optString("source");
     if(!"manual".equals(source)&&!"live".equals(source)&&!("prefetch".equals(source)&&eligible))throw new IllegalArgumentException();
     archive.merge(account,command,"manual".equals(source),legacyScope(local,command));
    }
    if(TRANSFER_COMMANDS.contains(type))result=transferResult(account,type,command);
    else if("appearance".equals(type))result=new JSONObject().put("saved",true);
    else if("list".equals(type)){
    org.json.JSONArray sessions=new org.json.JSONArray();
    for(OfflineArchive.Session row:archive.sessions(account))
     sessions.put(new JSONObject().put("id",row.id).put("title",row.title).put("syncedAt",row.syncedAt));
    result=new JSONObject().put("name",account.name).put("lang",owner.offlineLanguage()).put("sessions",sessions);
   }else if("session".equals(type)){
    String session=command.getString("sessionId"),before=command.isNull("before")?"":command.optString("before");
    if(session.isEmpty()||session.length()>4096||before.length()>4096)throw new IllegalArgumentException();
    result=archive.recordPage(account,session,command);
   }else if("read".equals(type)){
     if(command.optBoolean("page")){
      result=new JSONObject().put("page",archive.recordPage(account,command.getString("sessionId"),command));
     }else if(command.optJSONObject("body")!=null){
      JSONObject part=archive.body(account,command);
      result=new JSONObject().put("body",part==null?JSONObject.NULL:part);
     }else result=new JSONObject().put("turns",archive.seed(account,command));
    }else result=state(account,command.optString("sessionId"));
   }
   reply.put("ok",true).put("value",result);
  }catch(Exception e){try{reply.put("ok",false);}catch(Exception ignored){}}
  owner.runOnUiThread(()->send(local,reply));
 }

 private JSONObject transferResult(OfflineArchive.Account account,String type,JSONObject command)throws Exception {
  switch(type){
   case "transferStats":
    return new JSONObject().put("recordBatch",1).put("freeBytes",archive.freeBytes()).put("clearRevision",Long.toString(account.clearRevision))
      .put("allowTransfer",transferAllowed).put("networkAvailable",networkAvailable).put("networkEpoch",networkEpoch);
   case "transferTaskLoad":return archive.transferTask(account);
   case "transferTaskSave":return archive.saveTransferTask(account,command);
   case "transferCached":return archive.transferCached(account,command);
   case "transferChunkHas":
    return new JSONObject().put("present",archive.transferChunkHas(account,OfflineArchive.transferVersion(command),OfflineArchive.transferIndex(command)));
   case "transferChunkGet":
    byte[] block=archive.transferChunk(account,OfflineArchive.transferVersion(command),OfflineArchive.transferIndex(command));
    return new JSONObject().put("data",block==null?JSONObject.NULL:Base64.encodeToString(block,Base64.NO_WRAP));
   case "transferChunkPut":
    String data=command.getString("data");
    if(data.length()>700_000)throw new IllegalArgumentException("Invalid transfer chunk");
    return archive.putTransferChunk(account,command,Base64.decode(data,Base64.NO_WRAP),command.getString("sha256"));
   case "transferRecordCommit":return archive.commitTransfer(account,command,false);
   case "transferAttachmentCommit":return archive.commitTransfer(account,command,true);
   case "transferSessionComplete":return archive.completeTransferSession(account,command);
   case "transferAttachment":
    JSONObject attachment=archive.transferAttachment(account,command);
    return attachment==null?new JSONObject():attachment;
   default:throw new IllegalArgumentException("Invalid transfer command");
  }
 }

 private void fetchTransferChunk(Link local,OfflineArchive.Account account,JSONObject command,String id)throws Exception {
  String reason=archive.transferAdmission(account,command);
  if(reason==null&&transferActive)reason="busy";
  boolean sameOverride=command.optBoolean("allowMetered")&&networkEpoch.equals(command.optString("networkEpoch"));
  if(reason==null&&(!networkAvailable||!transferAllowed&&!sameOverride))reason="network";
  if(reason==null&&archive.freeBytes()<2_097_152)reason="space";
  if(reason!=null){transferReply(local,id,OfflineArchive.transferOutcome(reason));return;}
  String route=command.getString("url");
  if(route.length()>512||!route.startsWith("/api/history-transfer/content?"))throw new IllegalArgumentException("Invalid transfer URL");
  Uri components=Uri.parse(route);
  long offset=command.getLong("offset"),total=command.getLong("bytes");
  int index=OfflineArchive.transferIndex(command);
  int chunk=OfflineArchive.TRANSFER_CHUNK_BYTES;
  String manifest=components.getQueryParameter("id"),item=components.getQueryParameter("item");
  if(components.getFragment()!=null||components.getQueryParameterNames().size()!=4||components.getEncodedQuery().split("&",-1).length!=4||
     manifest==null||!manifest.matches("[a-f0-9]{32}")||item==null||!item.matches("[a-f0-9]{64}")||
     offset<0||offset>=total||offset!=(long)index*chunk||
     !Long.toString(offset).equals(components.getQueryParameter("offset"))||
     !Integer.toString(chunk).equals(components.getQueryParameter("length"))&&
        !Long.toString(Math.min(chunk,total-offset)).equals(components.getQueryParameter("length")))throw new IllegalArgumentException("Invalid transfer range");
  String target=local.address+route,version=OfflineArchive.transferVersion(command);
  if(!GatewayURL.sameOrigin(target,local.address)||!"/api/history-transfer/content".equals(GatewayURL.apiPath(target,local.address)))
   throw new IllegalArgumentException("Invalid transfer URL");
  String cookie=local.verifiedCookie;
  if(cookie.isEmpty())throw new IllegalArgumentException("Login required");
  int expected=(int)Math.min(chunk,total-offset);
  String range="bytes "+offset+"-"+(offset+expected-1)+"/"+total;
  transferActive=true;
  transfer.execute(()->{
   HttpURLConnection connection=null;
   byte[] data=null;
   String hash="",failure=null;
   try{
    connection=(HttpURLConnection)new URL(target).openConnection();
    connection.setInstanceFollowRedirects(false);
    connection.setConnectTimeout(15_000);connection.setReadTimeout(20_000);
    connection.setUseCaches(false);
    connection.setRequestProperty("Cookie",cookie);
    connection.setRequestProperty("Origin",GatewayURL.httpOrigin(local.address));
    connection.setRequestProperty("User-Agent","BridgeMobile/0.1-Android");
    connection.setRequestProperty("Accept-Encoding","identity");
    int status=connection.getResponseCode();
    hash=connection.getHeaderField("X-Chunk-SHA256");
    if(status==401||status==403)failure="permission";
    else if(status==404||status==409||status==410)failure="manifest";
    else if(status>=500)failure="network";
    else if(status!=206||!range.equals(connection.getHeaderField("Content-Range"))||
       !("\"sha256-"+version+"\"").equals(connection.getHeaderField("ETag"))||
       hash==null||!hash.matches("[a-f0-9]{64}")||
       !command.isNull("sha256")&&!hash.equals(command.optString("sha256")))failure="integrity";
    else{
     byte[] block=new byte[expected];
     try(InputStream input=connection.getInputStream()){
      int read=0;
      while(read<expected){
       int count=input.read(block,read,expected-read);
       if(count<0)break;
       read+=count;
      }
      if(read!=expected||input.read()!=-1)failure="integrity";
      else data=block;
     }
    }
   }catch(Exception error){failure="network";}
   finally{if(connection!=null)connection.disconnect();}
   final byte[] received=data;
   final String verifiedHash=hash,result=failure;
   work.execute(()->{
    try{
     OfflineArchive.Account linked=archive.forAddress(local.address);
     String problem=result;
     if(problem==null&&(linked==null||linked.id!=account.id))problem="permission";
     transferReply(local,id,problem==null?archive.putTransferChunk(account,command,received,verifiedHash):OfflineArchive.transferOutcome(problem));
    }catch(Exception error){transferReply(local,id,null);}
    finally{
     transferActive=false;
     if(closing){archive.close();work.shutdown();}
    }
   });
  });
 }
 private void transferReply(Link local,String id,JSONObject value) {
  try{
   JSONObject envelope=new JSONObject().put("id",id).put("ok",value!=null).put("value",value);
   owner.runOnUiThread(()->send(local,envelope));
  }catch(Exception ignored){}
 }

 private OfflineArchive.Account authorize(Link local,String gateway,String identity,boolean refresh)throws Exception {
  CookieManager cookies=CookieManager.getInstance();
  String header=cookies.getCookie(local.address+"/");
  String before=EventClient.sessionCookie(header,local.address);
  if(!refresh&&!before.isEmpty()&&before.equals(local.verifiedCookie)&&same(local.account,gateway,identity)) {
   OfflineArchive.Account current=archive.account(local.account.id);
   return current!=null&&"valid".equals(current.status)?current:null;
  }
  if(header==null||header.isEmpty())throw new IllegalStateException("Missing login");
  JSONObject auth=EventClient.request(local.address,"/api/auth",null,null);
  JSONObject scope=auth.optJSONObject("sessionSync");
  String authenticatedName=auth.optString("cookieName");
  String after=EventClient.sessionCookie(cookies.getCookie(local.address+"/"),local.address);
  String original=BindingSelection.cookie(header,authenticatedName);
  if(original.isEmpty()&&!before.isEmpty()&&before.startsWith("codex_mobile_session="))original=authenticatedName+"="+EventClient.tokenValue(before);
  if(link!=local||!foreground||original.isEmpty()||!original.equals(after))throw new IllegalStateException("Changed login");
  if(!auth.optBoolean("authenticated")){
   setStatus(archive.forAddress(local.address),rejection(auth.optString("authStatus")));
   throw new IllegalStateException("Login rejected");
  }
  if(scope==null||scope.optInt("version")!=1||!gateway.equals(scope.optString("gatewayId"))||!identity.equals(scope.optString("identity")))throw new IllegalStateException("Wrong login scope");
  local.account=archive.bind(local.address,local.name,gateway,identity);
  local.verifiedCookie=after;
  local.legacyLogin=auth.optString("listCacheIdentity");
  return local.account;
 }

 private String legacyScope(Link link,JSONObject command) {
  String legacy=command.optString("legacyScope");
  if(link.legacyLogin.isEmpty()||legacy.isEmpty()||legacy.length()>8192)return "";
  try{
   org.json.JSONArray parts=new org.json.JSONArray(legacy),session=new org.json.JSONArray(command.getString("sessionId"));
   if(session.length()!=3||!link.legacyLogin.equals(parts.optString(0))||
       !parts.optString(parts.length()-1).equals(session.optString(2)))return "";
   String provider=session.optString(0);
   boolean codex="codex".equals(provider)||"wsl".equals(provider);
   return (codex?parts.length()==3&&parts.optString(1).equals(session.optString(1)):
       parts.length()==4&&parts.optString(2).equals(provider))?legacy:"";
  }catch(org.json.JSONException invalid){return "";}
 }
 static String rejection(String status){return "expired".equals(status)||"revoked".equals(status)?status:"unknownRejected";}
 void status(String address,String status) {work.execute(()->setStatus(archive.forAddress(address),status));}
 void reconnectStatus(String route,String gateway,String identity,String cookie,String status) {
  work.execute(()->{
   OfflineArchive.Account account=archive.forAddress(route);
   if(!same(account,gateway,identity)||!cookie.equals(EventClient.sessionCookie(CookieManager.getInstance().getCookie(route+"/"),route)))return;
   archive.status(account,status);
   owner.runOnUiThread(()->owner.offlineReaderRejected(account.id,status));
  });
 }
 private void setStatus(OfflineArchive.Account account,String status){
  archive.status(account,status);
  if(account!=null&&!"valid".equals(status))owner.runOnUiThread(()->owner.closeOfflineReader(account.id));
 }
 void linkRoute(String from,String to){work.execute(()->archive.linkRoute(from,to));}
 void unlink(String address,boolean erase,Runnable done) {
  work.execute(()->{
   final boolean retained;
   try{
    OfflineArchive.Account account=archive.forAddress(address);
    if(erase&&account!=null)owner.forgetOfflineHistory(archive.legacyScopes(java.util.Collections.singletonList(account.id),Long.MIN_VALUE,Long.MAX_VALUE));
    retained=archive.unlink(address,erase);
    if(account!=null)owner.runOnUiThread(()->owner.closeOfflineReader(account.id));
   }catch(Exception error){owner.runOnUiThread(()->owner.message("无法更新离线缓存，请重试"));return;}
   owner.runOnUiThread(()->{
    if(retained)android.widget.Toast.makeText(owner,owner.L("已保留离线聊天记录"),android.widget.Toast.LENGTH_SHORT).show();
    done.run();
   });
  });
 }
 // Each conversion yields the archive executor to foreground reads and authorization changes.
 void scheduleMigration(OfflineArchive.Account account,String session){
  if(session!=null&&!session.isEmpty())migrationFocus.put(account.id,session);
  if(OfflineArchive.locked(account)||!migrating.add(account.id))return;
  migrateNext(account.id);
 }
 private void migrateNext(long id){
  owner.runOnUiThread(()->owner.getWindow().getDecorView().postDelayed(()->{
   if(closing)return;
   work.execute(()->{
   try{
    OfflineArchive.Account current=archive.account(id);
    if(current!=null&&!OfflineArchive.locked(current)&&(migrationFocus.containsKey(id)&&archive.migrateBatch(current,migrationFocus.get(id))||archive.migrateBatch(current,null))){migrateNext(id);return;}
   }catch(Exception unavailable){android.util.Log.w("OfflineArchive","Legacy conversion paused",unavailable);}
   migrating.remove(id);migrationFocus.remove(id);
   });
  },50));
 }
 void run(Consumer<OfflineArchive> action){work.execute(()->action.accept(archive));}
 void destroy(){
  closing=true;
  foreground(false);detach();
  work.execute(()->{
   closing=true;
   transfer.shutdown();
   if(!transferActive){archive.close();work.shutdown();}
  });
 }
}
