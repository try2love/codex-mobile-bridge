import io.github.try2love.codexbridge.ArtifactDownload;
import com.sun.net.httpserver.*;
import java.io.*;
import java.net.*;
import java.nio.file.*;
import java.util.*;
import java.util.concurrent.atomic.*;

/** Real HTTP, including incomplete bodies and resume after authentication renewal. */
public class ArtifactRangeTests {
 private static int checks;
 private static final String ATTACHMENT="/api/sessions/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee/files/"+String.join("",Collections.nCopies(64,"a"));
 private static final String WORKSPACE="/api/desktop-sessions/claude/workspace/download";
 public static void main(String[] args)throws Exception {
  Path directory=Paths.get(args[0]);Files.createDirectories(directory);
  byte[] payload=new byte[(int)ArtifactDownload.CHUNK*3+913];new Random(19).nextBytes(payload);
  HttpServer server=HttpServer.create(new InetSocketAddress("127.0.0.1",0),0);
  String base="http://127.0.0.1:"+server.getAddress().getPort();
  AtomicInteger requests=new AtomicInteger(),cookieReads=new AtomicInteger();AtomicBoolean changed=new AtomicBoolean(),ignoreRange=new AtomicBoolean(),interruptFirst=new AtomicBoolean();
  AtomicReference<String> expectedCookie=new AtomicReference<>("fixture=initial"),cookie=new AtomicReference<>("fixture=initial");
  AtomicReference<String> conflict=new AtomicReference<>();
  AtomicReference<Throwable> serverFailure=new AtomicReference<>();
  server.createContext("/api/",e->{
   try{
    requests.incrementAndGet();String scenario=e.getRequestURI().getQuery();
    require("BridgeMobile/0.1-Android".equals(e.getRequestHeaders().getFirst("User-Agent")),"native user agent");
    if(!expectedCookie.get().equals(e.getRequestHeaders().getFirst("Cookie"))){e.sendResponseHeaders(401,-1);return;}
    String range=e.getRequestHeaders().getFirst("Range");require(range!=null&&range.matches("bytes=[0-9]+-[0-9]+"),"range requested");
    String[] ends=range.substring(6).split("-");int first=Integer.parseInt(ends[0]),last=Integer.parseInt(ends[1]);require(last-first+1<=ArtifactDownload.CHUNK,"relay sized chunk");
    String tag=changed.get()?"\"changed\"":"\"fixture\"";
    if(first>0)require("\"fixture\"".equals(e.getRequestHeaders().getFirst("If-Range")),"strong If-Range on resume");
    e.getResponseHeaders().set("Content-Disposition","attachment; filename=\"range.bin\"");e.getResponseHeaders().set("ETag",tag);
    if(conflict.get()!=null){byte[] body=("{\"code\":\""+conflict.get()+"\",\"error\":\"fixture\"}").getBytes("UTF-8");e.getResponseHeaders().remove("ETag");e.getResponseHeaders().set("Content-Type","application/json");e.sendResponseHeaders(409,body.length);e.getResponseBody().write(body);return;}
    if("empty".equals(scenario)){e.getResponseHeaders().set("Content-Range","bytes */0");e.sendResponseHeaders(416,-1);return;}
    if("oversize".equals(scenario)){long size=9007199254740992L;e.getResponseHeaders().set("Content-Range","bytes 0-1023/"+size);e.sendResponseHeaders(206,1024);return;}
    if("large".equals(scenario)){int size=55*1024*1024;last=Math.min(last,size-1);e.getResponseHeaders().set("Content-Range","bytes "+first+"-"+last+"/"+size);e.sendResponseHeaders(206,last-first+1);e.getResponseBody().write(new byte[last-first+1]);return;}
    if("weak".equals(scenario))e.getResponseHeaders().set("ETag","W/\"fixture\"");
    last=Math.min(last,payload.length-1);
    boolean full=first>0&&(changed.get()||ignoreRange.get());
    if(full){e.sendResponseHeaders(200,payload.length);e.getResponseBody().write(payload);return;}
    e.getResponseHeaders().set("Content-Range","bytes "+("invalid".equals(scenario)?first+1:first)+"-"+last+"/"+payload.length);
    e.sendResponseHeaders(206,last-first+1);
    int finish=interruptFirst.getAndSet(false)?first+(last-first)/2:last;
    for(int offset=first;offset<=finish;offset+=16384){int count=Math.min(16384,finish-offset+1);e.getResponseBody().write(payload,offset,count);e.getResponseBody().flush();if("slow".equals(scenario))try{Thread.sleep(6);}catch(InterruptedException interrupted){Thread.currentThread().interrupt();}}
   }catch(IOException cancelled){/* An intentional pause can close an in-flight response. */}catch(Throwable failure){serverFailure.compareAndSet(null,failure);}finally{try{e.close();}catch(Exception ignored){}}
  });
  server.start();ArtifactDownload.Cookies cookies=()->{cookieReads.incrementAndGet();return cookie.get();};
  try{
   check(ArtifactDownload.accepts(base+WORKSPACE+"?path=test",base),"Claude workspace accepted");
   check(ArtifactDownload.accepts(base+WORKSPACE.replace("claude","deepseek"),base),"DeepSeek workspace accepted");
   check(!ArtifactDownload.accepts(base+WORKSPACE.replace("claude","other"),base),"unknown app rejected");
   ArtifactDownload.Task complete=new ArtifactDownload.Task(base+ATTACHMENT,base,directory.toFile());complete.run(cookies,null);
   check(complete.state==ArtifactDownload.State.COMPLETE,"multi-chunk complete");check(Arrays.equals(Files.readAllBytes(complete.result().file.toPath()),payload),"multi-chunk exact bytes");
   check(requests.get()==4&&cookieReads.get()==4,"cookie reread every 1 MiB chunk");complete.cancel();clean(directory);

   ArtifactDownload.Task paused=new ArtifactDownload.Task(base+ATTACHMENT+"?slow",base,directory.toFile());AtomicBoolean once=new AtomicBoolean();AtomicBoolean speed=new AtomicBoolean();
   paused.run(cookies,t->{if(t.bytesPerSecond>0)speed.set(true);if(t.downloaded>0&&once.compareAndSet(false,true))t.pause();});
   check(paused.state==ArtifactDownload.State.PAUSED,"pause settles");check(paused.downloaded>0&&paused.downloaded<ArtifactDownload.CHUNK,"pause inside chunk keeps partial bytes");check(speed.get(),"transfer speed reported");
   long offset=paused.downloaded;check(paused.resumable(),"strong tag permits resume");expectedCookie.set("fixture=renewed");cookie.set("fixture=renewed");paused.run(cookies,null);
   check(paused.state==ArtifactDownload.State.COMPLETE&&paused.downloaded>offset,"resume with new cookie completes");check(Arrays.equals(Files.readAllBytes(paused.result().file.toPath()),payload),"resume never duplicates bytes");paused.cancel();clean(directory);

   ArtifactDownload.Task auth=new ArtifactDownload.Task(base+ATTACHMENT,base,directory.toFile());cookie.set("fixture=expired");auth.run(cookies,null);check(auth.state==ArtifactDownload.State.FAILED&&auth.resumable(),"401 recoverable without deleting partial");cookie.set("fixture=renewed");auth.run(cookies,null);check(auth.state==ArtifactDownload.State.COMPLETE,"401 continues after sign-in");auth.cancel();clean(directory);

   ArtifactDownload.Task broken=new ArtifactDownload.Task(base+ATTACHMENT,base,directory.toFile());interruptFirst.set(true);broken.run(cookies,null);check(broken.state==ArtifactDownload.State.FAILED&&broken.downloaded>0&&broken.resumable(),"truncated body retains resumable partial");broken.run(cookies,null);check(broken.state==ArtifactDownload.State.COMPLETE&&Arrays.equals(Files.readAllBytes(broken.result().file.toPath()),payload),"truncated body resumes exactly");broken.cancel();clean(directory);

   for(boolean change:new boolean[]{true,false}){
    ArtifactDownload.Task task=new ArtifactDownload.Task(base+ATTACHMENT,base,directory.toFile());AtomicBoolean stop=new AtomicBoolean();task.run(cookies,t->{if(t.downloaded>=ArtifactDownload.CHUNK&&stop.compareAndSet(false,true))t.pause();});long partial=task.downloaded;
    if(change)changed.set(true);else ignoreRange.set(true);task.run(cookies,null);
    check(task.state==ArtifactDownload.State.FAILED&&task.restartRequired&&!task.resumable(),change?"changed file requires restart":"ignored range requires restart");check(task.downloaded==partial,"200 was never appended to partial");task.cancel();changed.set(false);ignoreRange.set(false);clean(directory);
   }
   for(String code:new String[]{"download_changed","desktop_unavailable"}){
    ArtifactDownload.Task task=new ArtifactDownload.Task(base+ATTACHMENT,base,directory.toFile());AtomicBoolean stop=new AtomicBoolean();task.run(cookies,t->{if(t.downloaded>=ArtifactDownload.CHUNK&&stop.compareAndSet(false,true))t.pause();});long partial=task.downloaded;
    conflict.set(code);task.run(cookies,null);boolean changedFile=code.equals("download_changed");
    check(task.state==ArtifactDownload.State.FAILED&&task.restartRequired==changedFile&&task.resumable()!=changedFile,"409 "+code+" classified");check(task.downloaded==partial,"409 error body never appended");
    if(changedFile)check(task.error.equals("文件已发生变化，请重新下载"),"409 changed message");
    conflict.set(null);if(!changedFile){task.run(cookies,null);check(task.state==ArtifactDownload.State.COMPLETE&&Arrays.equals(Files.readAllBytes(task.result().file.toPath()),payload),"temporary 409 resumes existing partial");}
    task.cancel();clean(directory);
   }
   for(String query:new String[]{"invalid","weak","oversize"}){
    ArtifactDownload.Task task=new ArtifactDownload.Task(base+ATTACHMENT+"?"+query,base,directory.toFile());task.run(cookies,null);check(task.state==ArtifactDownload.State.FAILED&&task.restartRequired,query+" rejected");task.cancel();clean(directory);
   }
   ArtifactDownload.Task large=new ArtifactDownload.Task(base+WORKSPACE+"?large",base,directory.toFile());large.run(cookies,null);check(large.state==ArtifactDownload.State.COMPLETE&&large.total==55L*1024*1024&&large.result().file.length()==large.total,"gateway-approved large workspace file streams to disk");large.cancel();clean(directory);
   ArtifactDownload.Task empty=new ArtifactDownload.Task(base+ATTACHMENT+"?empty",base,directory.toFile());empty.run(cookies,null);check(empty.state==ArtifactDownload.State.COMPLETE&&empty.total==0&&empty.result().file.length()==0,"416 empty file completes");empty.cancel();clean(directory);
   ArtifactDownload.Task cancelled=new ArtifactDownload.Task(base+ATTACHMENT+"?slow",base,directory.toFile());cancelled.run(cookies,t->{if(t.downloaded>0)t.cancel();});check(cancelled.state==ArtifactDownload.State.CANCELLED,"cancel active response");clean(directory);cancelled.run(cookies,null);check(cancelled.state==ArtifactDownload.State.CANCELLED,"cancelled task cannot restart");
   if(serverFailure.get()!=null)throw new AssertionError("server assertion",serverFailure.get());
   System.out.println(checks+" resumable download checks passed (real HTTP Range, pause, renewed cookies, ETag, limits, cleanup)");
  }finally{server.stop(0);}
 }
 static void clean(Path directory)throws IOException{try(java.util.stream.Stream<Path> files=Files.list(directory)){check(files.count()==0,"temporary files cleaned");}}
 static void require(boolean value,String label){if(!value)throw new AssertionError(label);}
 static void check(boolean value,String label){checks++;require(value,label);}
}
