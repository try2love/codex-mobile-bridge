package io.github.try2love.codexbridge;

import com.sun.net.httpserver.HttpServer;
import java.net.InetSocketAddress;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.Arrays;
import java.util.Random;
import java.util.concurrent.atomic.AtomicInteger;

/** Real loopback HTTP transfers; no cloud account, gateway or model service. */
public final class CloudDownloadTests {
 static final String ROUTE="/api/sessions/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee/workspace/download";
 static final String CAP="abcdef0123456789abcdef0123456789";
 static void check(boolean value,String message){if(!value)throw new AssertionError(message);}
 public static void main(String[] args)throws Exception {
  Path directory=Paths.get(args[0]);Files.createDirectories(directory);
  HttpServer server=HttpServer.create(new InetSocketAddress("127.0.0.1",0),0);
  String base="http://127.0.0.1:"+server.getAddress().getPort();
  byte[] payload=new byte[(int)ArtifactDownload.CHUNK*2+179];new Random(1325).nextBytes(payload);
  AtomicInteger requests=new AtomicInteger(),headers=new AtomicInteger(),cookieReads=new AtomicInteger(),leaked=new AtomicInteger();
  server.createContext("/",exchange->{
   if(exchange.getRequestURI().getPath().equals("/redirect-target")){leaked.incrementAndGet();exchange.sendResponseHeaders(200,-1);exchange.close();return;}
   requests.incrementAndGet();
   if(!CAP.equals(exchange.getRequestHeaders().getFirst("X-CMB-Cloud-View"))){exchange.sendResponseHeaders(403,-1);exchange.close();return;}
   headers.incrementAndGet();check(exchange.getRequestHeaders().getFirst("Cookie")==null,"cloud transfer must not send gateway cookies");
   if("redirect".equals(exchange.getRequestURI().getQuery())){exchange.getResponseHeaders().set("Location",base+"/redirect-target");exchange.sendResponseHeaders(302,-1);exchange.close();return;}
   String range=exchange.getRequestHeaders().getFirst("Range");check(range!=null&&range.startsWith("bytes="),"same native range downloader");
   String[] parts=range.substring(6).split("-");int first=Integer.parseInt(parts[0]),last=Math.min(Integer.parseInt(parts[1]),payload.length-1);
   if(first>0)check("\"fixture-etag\"".equals(exchange.getRequestHeaders().getFirst("If-Range")),"resume validator forwarded");
   exchange.getResponseHeaders().set("ETag","\"fixture-etag\"");
   exchange.getResponseHeaders().set("Content-Range","bytes "+first+"-"+last+"/"+payload.length);
   exchange.getResponseHeaders().set("Content-Disposition","attachment; filename*=UTF-8''%E4%BA%91%E4%B8%8B%E8%BD%BD.txt");
   exchange.sendResponseHeaders(206,last-first+1);exchange.getResponseBody().write(payload,first,last-first+1);exchange.close();
  });server.start();
  try{
   // The old Cookie-only path reaches the capability gate and fails before any bytes.
   ArtifactDownload.Task old=new ArtifactDownload.Task(base+ROUTE,base,directory.toFile());
   old.run(()->"fixture-cookie",null);check(old.state==ArtifactDownload.State.FAILED&&old.downloaded==0,"legacy cloud request denied without view header");old.cancel();
   CloudDownloadGrant grant=new CloudDownloadGrant(base+"/#cmb-cloud="+CAP);
   ArtifactDownload.Task task=new ArtifactDownload.Task(base+ROUTE,base,directory.toFile(),grant);
   int before=headers.get();task.run(()->{cookieReads.incrementAndGet();return "must-not-read";},null);
   check(task.state==ArtifactDownload.State.COMPLETE,"cloud transfer completes");
   check(headers.get()-before==3,"capability provided for every range");
   check(Arrays.equals(payload,Files.readAllBytes(task.result().file.toPath())),"all bytes retained exactly");
   check("云下载.txt".equals(task.name),"upstream Unicode filename retained");
   check(cookieReads.get()==0,"cloud bypasses Cookie provider entirely");task.cancel();
   for(String provider:new String[]{"claude","deepseek","zcode"}){
    String target=base+"/api/desktop-sessions/"+provider+"/workspace/download?sessionId=fixture";
    check(CAP.equals(grant.read(target).get("X-CMB-Cloud-View")),"shared grant supports "+provider);
   }
   for(String target:new String[]{base+"/api/auth","http://127.0.0.1:1"+ROUTE,"https://example.invalid"+ROUTE,base+ROUTE+"#fragment"}){
    try{grant.read(target);throw new AssertionError("non-download target accepted");}catch(java.io.IOException expected){}
   }
   ArtifactDownload.Task redirect=new ArtifactDownload.Task(base+ROUTE+"?redirect",base,directory.toFile(),grant);
   redirect.run(()->null,null);check(redirect.state==ArtifactDownload.State.FAILED&&leaked.get()==0,"cloud auth never follows redirects");redirect.cancel();
   CloudDownloadGrant closed=new CloudDownloadGrant(base+"/#cmb-cloud="+CAP);
   AtomicInteger segments=new AtomicInteger();
   ArtifactDownload.Task revoked=new ArtifactDownload.Task(base+ROUTE,base,directory.toFile(),target->{if(segments.incrementAndGet()==2)closed.close();return closed.read(target);});
   before=requests.get();revoked.run(()->null,null);
   check(revoked.state==ArtifactDownload.State.FAILED&&revoked.downloaded==ArtifactDownload.CHUNK,"leaving view blocks next segment");
   check(requests.get()-before==1,"closed view sends no further request");revoked.cancel();
   for(String entry:new String[]{base+"/",base+"/?x=1#cmb-cloud="+CAP,base+"/other#cmb-cloud="+CAP,"https://127.0.0.1:123/#cmb-cloud="+CAP,"http://localhost:123/#cmb-cloud="+CAP,base+"/#cmb-cloud=bad","http://user@127.0.0.1:123/#cmb-cloud="+CAP}){
    try{new CloudDownloadGrant(entry);throw new AssertionError("invalid cloud entry accepted");}catch(java.io.IOException expected){}
   }
   try(java.util.stream.Stream<Path> files=Files.list(directory)){check(files.count()==0,"all fixture parts cleaned");}
   System.out.println("Cloud download checks passed (capability per range, no cookies, exact bytes/name, all agents, revoke, origin, redirect)");
  }finally{server.stop(0);}
 }
}
