package io.github.try2love.codexbridge;

import java.io.*;
import java.net.*;
import java.util.regex.*;

/** Gateway attachments only. Never forward a login cookie across a redirect. */
public final class ArtifactDownload {
 public static boolean accepts(String url,String origin){
  try{return GatewayURL.sameOrigin(url,origin)&&new URI(url).getPath().matches("/api/sessions/[0-9a-f-]{36}/(?:files/[a-f0-9]{64}|workspace/download)");}catch(Exception e){return false;}
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
 public static final class Result {
  public final File file;public final String name;
  Result(File file,String name){this.file=file;this.name=name;}
 }
 public static Result fetch(String url,String origin,String cookie,File directory)throws IOException {
  if(!accepts(url,origin))throw new IOException("下载地址不是当前电脑的附件");
  HttpURLConnection connection=(HttpURLConnection)new URL(url).openConnection();
  connection.setConnectTimeout(15000);connection.setReadTimeout(30000);connection.setInstanceFollowRedirects(false);
  if(cookie!=null&&!cookie.isEmpty())connection.setRequestProperty("Cookie",cookie);
  File file=null;
  try{
   int status=connection.getResponseCode();
   if(status==401||status==403)throw new IOException("登录已失效，请重新连接电脑后下载");
   if(status!=200)throw new IOException("下载失败（HTTP "+status+"）");
   if(connection.getContentLengthLong()>50L*1024*1024)throw new IOException("附件超过 50 MB 下载限制");
   file=File.createTempFile("bridge-download-",".tmp",directory);
   long total=0;
   try(InputStream in=connection.getInputStream();OutputStream out=new FileOutputStream(file)){
    byte[] buffer=new byte[16384];int count;
    while((count=in.read(buffer))!=-1){if(Thread.currentThread().isInterrupted())throw new InterruptedIOException();total+=count;if(total>50L*1024*1024)throw new IOException("附件超过 50 MB 下载限制");out.write(buffer,0,count);}
   }
   if(connection.getContentLengthLong()>=0&&total!=connection.getContentLengthLong())throw new IOException("下载不完整，请重试");
   return new Result(file,filename(connection.getHeaderField("Content-Disposition")));
  }catch(IOException e){if(file!=null)file.delete();throw e;}finally{connection.disconnect();}
 }
}
