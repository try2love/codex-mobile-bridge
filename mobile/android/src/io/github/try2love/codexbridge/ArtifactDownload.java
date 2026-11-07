package io.github.try2love.codexbridge;

import java.io.*;
import java.net.*;
import java.util.regex.*;

/** Gateway attachments and allowlisted release packages. Credentials never follow a redirect. */
public final class ArtifactDownload {
 public static final long CHUNK=1024L*1024;
 public static boolean accepts(String url,String origin){
  try{return GatewayURL.sameOrigin(url,origin)&&(GatewayURL.apiPath(url,origin).matches("(?:/api/sessions/[0-9a-f-]{36}/(?:files/[a-f0-9]{64}|workspace/download)|/api/desktop-sessions/(?:claude|deepseek|zcode)/workspace/download)")||imageDownload(url,origin)||diagnostics(url,origin));}catch(Exception e){return false;}
 }
 static boolean imageDownload(String url,String origin){
  try{
   URI value=new URI(url);if(value.getRawFragment()!=null||value.getRawQuery()==null)return false;
   int flags=0;for(String part:value.getRawQuery().split("&")){if(part.startsWith("download=")){if(!part.equals("download=1"))return false;flags++;}}
   return flags==1&&GatewayURL.apiPath(url,origin).matches("(?:/api/sessions/[0-9a-f-]{36}/(?:uploads/[0-9a-f-]{36}/preview|desktop-images/[a-f0-9]{64})|/api/desktop-sessions/(?:claude|deepseek|zcode)/(?:uploads/[0-9a-f-]{36}/preview|images/[a-f0-9]{64}))");
  }catch(Exception e){return false;}
 }
 static boolean diagnostics(String url,String origin){
  try{URI value=new URI(url);String query=value.getRawQuery();return GatewayURL.sameOrigin(url,origin)&&value.getRawFragment()==null&&GatewayURL.apiPath(url,origin).equals("/api/sessions/diagnostics")&&(query==null||query.matches("thread=[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"));}catch(Exception e){return false;}
 }
 public static String filename(String disposition){
  String name="download";
  if(disposition!=null){
   Matcher utf=Pattern.compile("filename\\*=UTF-8''([^;]+)",Pattern.CASE_INSENSITIVE).matcher(disposition);
   Matcher plain=Pattern.compile("filename=\"([^\"]+)\"|filename=([^;]+)",Pattern.CASE_INSENSITIVE).matcher(disposition);
   try{if(utf.find())name=URLDecoder.decode(utf.group(1).trim().replace("+","%2B"),"UTF-8");else if(plain.find())name=plain.group(1)!=null?plain.group(1):plain.group(2).trim();}catch(Exception ignored){}
  }
  name=name.replace('\\','/');name=name.substring(name.lastIndexOf('/')+1).replaceAll("[\\p{Cntrl}]", "_");
  return name.isEmpty()||name.equals(".")||name.equals("..")?"download":name;
 }
 public interface Cookies {String read();}
 public interface Observer {void changed(Task task);}
 public enum State {READY,RUNNING,PAUSING,PAUSED,FAILED,COMPLETE,CANCELLED}
 public static final class Result {
  public final File file;public final String name;
  Result(File file,String name){this.file=file;this.name=name;}
 }
 private static final class Failure extends IOException {
  final boolean restart;
  Failure(String message,boolean restart){super(message);this.restart=restart;}
 }
 /** One resumable task, independent of the current WebView or chat. */
 public static final class Task {
  public final String url,origin;private final File directory;private final long limit;
  private final long expectedSize;private final String expectedSha256;private final String fallbackName;private final boolean update,diagnosticReport;
  public final boolean gatewayUpdate;
  public volatile State state=State.READY;
  public volatile long downloaded,total=-1;public volatile double bytesPerSecond;
  public volatile String name="download",error="";public volatile boolean restartRequired;
  private volatile HttpURLConnection connection;private volatile boolean pause,cancelled;
  private File file;private String etag;private boolean running;private Observer observer;
  private long lastSample,lastBytes;
  public Task(String url,String origin,File directory)throws IOException {this(url,origin,directory,-1,null,null);}
  public Task(String url,String origin,File directory,long expectedSize,String expectedSha256,String fallbackName)throws IOException {
   this(url,origin,directory,expectedSize,expectedSha256,fallbackName,false);
  }
  public static Task gatewayUpdate(String base,String identifier,MobileRelease release,File directory)throws IOException {
   try{if(!GatewayURL.origin(base).equals(base)||identifier==null||!identifier.matches("[a-f0-9]{64}"))throw new IOException("电脑更新地址无效");}
   catch(Exception e){throw new IOException("电脑更新地址无效",e);}
   return new Task(base+"/api/mobile/update/packages/"+identifier,base,directory,release.size,release.sha256,MobileRelease.asset(release.version),true);
  }
  private Task(String url,String origin,File directory,long expectedSize,String expectedSha256,String fallbackName,boolean gatewayUpdate)throws IOException {
   this.gatewayUpdate=gatewayUpdate;update=gatewayUpdate||MobileRelease.isDownload(url);diagnosticReport=diagnostics(url,origin);
   if(!accepts(url,origin)&&!update)throw new IOException("下载地址不是当前电脑的附件");
   if(update&&(expectedSize<=0||expectedSize>MobileRelease.MAX_SIZE||expectedSha256==null||!expectedSha256.matches("[a-fA-F0-9]{64}")))throw new IOException("更新缺少有效的大小或 SHA-256 校验信息，请打开版本页面下载。");
   this.url=url;this.origin=origin;this.directory=directory;this.expectedSize=expectedSize;this.expectedSha256=expectedSha256;this.fallbackName=fallbackName==null||fallbackName.isEmpty()?"download":fallbackName;name=this.fallbackName;limit=update?MobileRelease.MAX_SIZE:9007199254740991L; // Gateway policy is enforced by the authenticated relay; update packages have a local size ceiling.
  }
  public synchronized boolean resumable(){return !restartRequired&&(downloaded==0||!diagnosticReport&&etag!=null);}
  public void pause(){HttpURLConnection active;synchronized(this){if(state!=State.RUNNING)return;pause=true;state=State.PAUSING;active=connection;}if(active!=null)active.disconnect();}
  public void cancel(){HttpURLConnection active;synchronized(this){cancelled=true;state=State.CANCELLED;active=connection;if(!running)removeFile();}if(active!=null)active.disconnect();}
  private void removeFile(){if(file!=null){file.delete();file=null;}}
  public synchronized Result result()throws IOException {if(state!=State.COMPLETE||file==null)throw new IOException("下载尚未完成");return new Result(file,name);}
  public static Task restoreUpdate(MobileRelease release,File directory,String filename)throws IOException {
   if(filename==null||!filename.matches("bridge-download-[a-zA-Z0-9-]+\\.part"))throw new IOException("Invalid pending update path");
   File file=new File(directory,filename);
   if(java.nio.file.Files.isSymbolicLink(file.toPath())||!directory.getCanonicalFile().equals(file.getCanonicalFile().getParentFile())||!file.isFile())throw new IOException("Missing pending update");
   Task task=new Task(release.download,MobileRelease.REPO,directory,release.size,release.sha256,MobileRelease.asset(release.version));
   task.file=file;task.downloaded=task.total=file.length();task.verifyUpdate();task.state=State.COMPLETE;return task;
  }
  public void verifyCompletedUpdate()throws IOException {if(!update||state!=State.COMPLETE||file==null)throw new IOException("Update not ready");verifyUpdate();}
  private void stopped()throws InterruptedIOException {if(pause||cancelled||Thread.currentThread().isInterrupted())throw new InterruptedIOException();}
  private void update(boolean force){long now=System.nanoTime();if(force||now-lastSample>=250_000_000L){double seconds=(now-lastSample)/1e9;if(seconds>0&&downloaded>lastBytes){double sample=(downloaded-lastBytes)/seconds;bytesPerSecond=bytesPerSecond==0?sample:bytesPerSecond*.5+sample*.5;}lastSample=now;lastBytes=downloaded;if(observer!=null)observer.changed(this);}}
  public void run(Cookies cookies,Observer observer){
   synchronized(this){if(running||cancelled||state==State.COMPLETE)return;if(!resumable()){state=State.FAILED;return;}running=true;pause=false;this.observer=observer;state=State.RUNNING;error="";bytesPerSecond=0;lastSample=System.nanoTime();lastBytes=downloaded;}
   update(true);
   try{while(total<0||downloaded<total){stopped();part(cookies);}stopped();verifyUpdate();synchronized(this){if(!cancelled)state=State.COMPLETE;}}
   catch(IOException failure){synchronized(this){if(cancelled)state=State.CANCELLED;else if(pause){state=State.PAUSED;}else{state=State.FAILED;restartRequired=failure instanceof Failure&&((Failure)failure).restart;error=failure instanceof Failure?failure.getMessage():"下载中断，请检查网络和存储后重试";}}}
   finally{synchronized(this){running=false;bytesPerSecond=0;if(cancelled)removeFile();}update(true);}
  }
  private void verifyUpdate()throws IOException {
   if(!update)return;
   if(file==null||file.length()!=expectedSize||downloaded!=expectedSize||total!=expectedSize)throw new Failure("更新文件大小与发布信息不一致，请重新下载。",true);
   if(expectedSha256!=null){try{java.security.MessageDigest digest=java.security.MessageDigest.getInstance("SHA-256");try(InputStream input=new FileInputStream(file)){byte[] buffer=new byte[16384];int count;while((count=input.read(buffer))!=-1){stopped();digest.update(buffer,0,count);}}StringBuilder actual=new StringBuilder();for(byte value:digest.digest())actual.append(String.format(java.util.Locale.ROOT,"%02x",value));if(!expectedSha256.equalsIgnoreCase(actual.toString()))throw new Failure("更新文件校验失败，请重新下载。",true);}catch(java.security.NoSuchAlgorithmException impossible){throw new IOException(impossible);}}
  }
  private boolean downloadChanged(HttpURLConnection active)throws IOException {
   // Relay errors have a small JSON body. Do not mistake another 409 (e.g. an
   // offline SSH desktop) for a changed file or buffer an unbounded error body.
   try(InputStream input=active.getErrorStream();ByteArrayOutputStream body=new ByteArrayOutputStream()){
    if(input==null)return false;byte[] buffer=new byte[512];int count;
    while((count=input.read(buffer))!=-1){stopped();if(body.size()+count>4096)return false;body.write(buffer,0,count);}
    return Pattern.compile("\"code\"\\s*:\\s*\"download_changed\"").matcher(body.toString("UTF-8")).find();
   }
  }
  private void part(Cookies cookies)throws IOException {
   long start=downloaded,end=Math.min(start+CHUNK-1,limit-1);if(total>=0)end=Math.min(end,total-1);
   HttpURLConnection active=null;String target=url;
   try{
    for(int redirect=0;;redirect++){
     active=(HttpURLConnection)new URL(target).openConnection();
     active.setConnectTimeout(15000);active.setReadTimeout(30000);active.setInstanceFollowRedirects(false);
     active.setRequestProperty("Range","bytes="+start+"-"+end);active.setRequestProperty("Accept-Encoding","identity");active.setRequestProperty("User-Agent","BridgeMobile/0.1-Android");
     if(!update||gatewayUpdate){String cookie=cookies.read();if(cookie!=null&&!cookie.isEmpty())active.setRequestProperty("Cookie",cookie);}if(etag!=null)active.setRequestProperty("If-Range",etag);
     synchronized(this){stopped();connection=active;}
     int redirectStatus=active.getResponseCode();
     // GitHub release assets redirect to their CDN. Only this credential-free
     // release path follows the fixed GitHub host allowlist; gateway redirects
     // still fail below and never carry cookies to another origin.
     if(update&&!gatewayUpdate&&redirectStatus>=300&&redirectStatus<400){String location=active.getHeaderField("Location");active.disconnect();active=null;if(redirect>=3||location==null||!MobileRelease.redirectAllowed(location))throw new Failure("更新下载地址发生跳转，已取消。",true);target=location;continue;}
     break;
    }
   try{
    int status=active.getResponseCode();stopped();
    if(gatewayUpdate&&(status==400||status==404))throw new Failure("电脑更新缓存已过期，请重新下载",true);
    if(status==409){boolean changed=downloadChanged(active);throw new Failure(changed?"文件已发生变化，请重新下载":"下载失败，请检查网络后重试",changed);}
    if(status==401||status==403)throw new Failure(update?"更新下载被拒绝，请稍后重试":"登录已失效，请重新连接电脑后继续下载",false);
    if(status>=300&&status<400)throw new Failure("下载地址发生跳转，已取消。请重新打开电脑附件。",true);
    String tag=active.getHeaderField("ETag");boolean strong=tag!=null&&tag.matches("\"[^\"\\r\\n]+\"");
    if((status==200||status==206)&&etag!=null&&!etag.equals(tag))throw new Failure("文件已发生变化，请重新下载",true);
    long expected;String range=active.getHeaderField("Content-Range");
    if(status==416&&start==0&&"bytes */0".equals(range)){total=0;expected=0;}
    else if(status==206){
     Matcher match=Pattern.compile("bytes ([0-9]+)-([0-9]+)/([0-9]+)").matcher(range==null?"":range);
     if(!match.matches()||!strong)throw new Failure("网关返回的续传信息无效，请重新下载",true);
     long first,last,size;try{first=Long.parseLong(match.group(1));last=Long.parseLong(match.group(2));size=Long.parseLong(match.group(3));}catch(NumberFormatException invalid){throw new Failure("网关返回的续传信息无效，请重新下载",true);}
     if(first!=start||last<first||last>end||last>=size||(total>=0&&total!=size))throw new Failure("网关返回的续传信息无效，请重新下载",true);
     total=size;expected=last-first+1;etag=tag;
    }else if(status==200){
     if(start>0)throw new Failure("当前网关不支持续传，请重新下载",true);
     total=active.getContentLengthLong();expected=total;etag=strong?tag:null;
    }else throw new Failure("下载失败，请检查网络后重试",false);
    if(total>limit)throw new Failure("下载响应格式不正确，请重新下载",true);
    if(update&&total>=0&&total!=expectedSize)throw new Failure("更新文件大小与发布信息不一致，请重新下载。",true);
    long length=active.getContentLengthLong();if(status==206&&length>=0&&length!=expected)throw new Failure("网关返回的续传信息无效，请重新下载",true);
    if(file==null)file=File.createTempFile("bridge-download-",".part",directory);
    String suggested=filename(active.getHeaderField("Content-Disposition"));if(!suggested.equals("download")||fallbackName.equals("download"))name=suggested;update(true);
    if(expected==0)return;
    if(file.length()!=downloaded)throw new Failure("下载文件大小与响应不一致，请重新下载",true);
    long received=0;
    try(InputStream input=active.getInputStream();OutputStream output=new FileOutputStream(file,true)){
     byte[] buffer=new byte[16384];int count;
     while((count=input.read(buffer))!=-1){stopped();if(downloaded+count>limit||(expected>=0&&received+count>expected))throw new Failure("下载文件大小与响应不一致，请重新下载",true);output.write(buffer,0,count);received+=count;downloaded+=count;update(false);}
    }
    stopped();if(expected>=0&&received!=expected)throw new IOException("incomplete response");
    if(status==200&&total<0)total=downloaded;
   }finally{synchronized(this){if(connection==active)connection=null;}if(active!=null)active.disconnect();}
   }catch(IOException error){if(active!=null)active.disconnect();throw error;}
  }
 }
 /** Compatibility helper for callers that need a single, blocking transfer. */
 public static Result fetch(String url,String origin,String cookie,File directory)throws IOException {
  Task task=new Task(url,origin,directory);task.run(()->cookie,null);
  if(task.state==State.COMPLETE)return task.result();String error=task.error;task.cancel();throw new IOException(error.isEmpty()?"下载失败，请重试":error);
 }
}
