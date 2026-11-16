package io.github.try2love.codexbridge;

import java.io.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.security.MessageDigest;
import org.json.*;

/** Private display data only. Binding-derived directories are never supplied by a page. */
final class DisplayStorage {
 static String digest(String value)throws Exception {
  StringBuilder out=new StringBuilder();for(byte b:MessageDigest.getInstance("SHA-256").digest(value.getBytes(StandardCharsets.UTF_8)))out.append(String.format("%02x",b&255));return out.toString();
 }
 static Object execute(File root,String binding,JSONObject command)throws Exception {
  File dir=new File(root,digest(binding));String op=command.getString("op"),key=command.optString("key");
  if(key.length()>8192)throw new IOException("Invalid display key");
  if(op.equals("list")){
   JSONArray keys=new JSONArray();File[] files=dir.listFiles();if(files!=null)for(File file:files){
    if(!file.getName().endsWith(".json"))continue;
    try{JSONObject row=new JSONObject(new String(Files.readAllBytes(file.toPath()),StandardCharsets.UTF_8));String stored=row.getString("key");if(stored.startsWith(key))keys.put(stored);}catch(Exception ignored){}
   }return keys;
  }
  File file=new File(dir,digest(key)+".json");
  if(op.equals("read")){if(!file.isFile())return JSONObject.NULL;return new JSONObject(new String(Files.readAllBytes(file.toPath()),StandardCharsets.UTF_8)).get("value");}
  if(op.equals("remove")){Files.deleteIfExists(file.toPath());return true;}
  if(!op.equals("write"))throw new IOException("Invalid display operation");
  Files.createDirectories(dir.toPath());File pending=new File(dir,file.getName()+".pending");
  Files.write(pending.toPath(),new JSONObject().put("key",key).put("value",command.get("value")).toString().getBytes(StandardCharsets.UTF_8));
  Files.move(pending.toPath(),file.toPath(),StandardCopyOption.REPLACE_EXISTING,StandardCopyOption.ATOMIC_MOVE);return true;
 }
}
