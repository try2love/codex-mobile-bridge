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
 static long forgetHistory(File root,java.util.Set<String> scopes)throws IOException {
  if(scopes.isEmpty())return 0;
  long released=0;
  File[] bindings=root.listFiles(File::isDirectory);
  if(bindings==null)return 0;
  for(File dir:bindings){
   File[] records=dir.listFiles(file->file.getName().endsWith(".json"));
   if(records==null)continue;
   for(File file:records){
    try{
     byte[] bytes=Files.readAllBytes(file.toPath());
     String key=new JSONObject(new String(bytes,StandardCharsets.UTF_8)).optString("key");
     if(key.startsWith("history:")&&scopes.contains(new JSONArray(key.substring(8)).getString(0))&&Files.deleteIfExists(file.toPath()))released+=bytes.length;
    }catch(JSONException invalid){/* An unrelated display record cannot identify an archive scope. */}
   }
  }
  return released;
 }
 static Object execute(File root,String binding,JSONObject command)throws Exception {
  File dir=new File(root,digest(binding));String op=command.getString("op"),key=command.optString("key");
  File legacyDir=dir;
  // Only non-sensitive display preferences survive credential rotation.
  int separator=binding.indexOf('\0');
  if((key.equals("appearance")||key.equals("composer-preferences"))&&separator>=0)
   dir=new File(root,digest(binding.substring(0,separator)+"\\0preferences-v1"));
  if(key.length()>8192)throw new IOException("Invalid display key");
  if(op.equals("list")){
   JSONArray keys=new JSONArray();File[] files=dir.listFiles();if(files!=null)for(File file:files){
    if(!file.getName().endsWith(".json"))continue;
    try{JSONObject row=new JSONObject(new String(Files.readAllBytes(file.toPath()),StandardCharsets.UTF_8));String stored=row.getString("key");if(stored.startsWith(key))keys.put(stored);}catch(Exception ignored){}
   }return keys;
  }
  File file=new File(dir,digest(key)+".json");
  if(op.equals("read")){
   if(!file.isFile()){
    File legacy=new File(legacyDir,file.getName());
    if(dir.equals(legacyDir)||!legacy.isFile())return JSONObject.NULL;
    Files.createDirectories(dir.toPath());Files.copy(legacy.toPath(),file.toPath());
   }
   return new JSONObject(new String(Files.readAllBytes(file.toPath()),StandardCharsets.UTF_8)).get("value");}
  if(op.equals("remove")){Files.deleteIfExists(file.toPath());return true;}
  if(!op.equals("write"))throw new IOException("Invalid display operation");
  Files.createDirectories(dir.toPath());File pending=new File(dir,file.getName()+".pending");
  Files.write(pending.toPath(),new JSONObject().put("key",key).put("value",command.get("value")).toString().getBytes(StandardCharsets.UTF_8));
  Files.move(pending.toPath(),file.toPath(),StandardCopyOption.REPLACE_EXISTING,StandardCopyOption.ATOMIC_MOVE);return true;
 }
}
