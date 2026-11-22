package io.github.try2love.codexbridge;

import android.content.Context;
import android.database.Cursor;
import android.database.DatabaseUtils;
import android.database.sqlite.SQLiteDatabase;
import android.database.sqlite.SQLiteOpenHelper;
import android.database.sqlite.SQLiteFullException;
import android.os.StatFs;
import android.util.Base64;
import android.util.JsonReader;
import java.io.BufferedOutputStream;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStreamReader;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;
import java.io.File;
import java.util.ArrayList;
import java.util.List;
import java.util.HashSet;
import java.util.Set;

/** Private storage shared by the origins that have authenticated as one gateway/account. */
final class OfflineArchive extends SQLiteOpenHelper {
 static final class Account {
  final long id, epoch, clearRevision;
  final String gateway, identity, name, status;
  final boolean retained;
  Account(long id,String gateway,String identity,String name,String status,boolean retained,long epoch,long clearRevision){
   this.id=id;this.gateway=gateway;this.identity=identity;this.name=name;this.status=status;this.retained=retained;this.epoch=epoch;this.clearRevision=clearRevision;
  }
 }
 static final class Session {
  final String id,title;
  final long syncedAt;
  final boolean complete;
  Session(String id,String title,long syncedAt,boolean complete){this.id=id;this.title=title;this.syncedAt=syncedAt;this.complete=complete;}
 }
 private static final int READER_PAGE=12, READER_CHARS=384*1024;
 private final Context context;


 OfflineArchive(Context context) {
  super(context, new File(context.getNoBackupFilesDir(), "offline-archive.db").getPath(), null, 3);
  this.context=context;
 }
 @Override public void onConfigure(SQLiteDatabase db) {
  db.setForeignKeyConstraintsEnabled(true);
  DatabaseUtils.longForQuery(db,"PRAGMA secure_delete=ON",null);
 }
 @Override public void onCreate(SQLiteDatabase db) {
  db.execSQL("CREATE TABLE accounts(id INTEGER PRIMARY KEY, gateway TEXT NOT NULL, identity TEXT NOT NULL, name TEXT NOT NULL, status TEXT NOT NULL, retained INTEGER NOT NULL DEFAULT 0, epoch INTEGER NOT NULL DEFAULT 0, clear_revision INTEGER NOT NULL DEFAULT 0, UNIQUE(gateway,identity))");
  db.execSQL("CREATE TABLE routes(address TEXT PRIMARY KEY, account INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE)");
  db.execSQL("CREATE TABLE sessions(account INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE, id TEXT NOT NULL, title TEXT NOT NULL, synced INTEGER NOT NULL, complete INTEGER NOT NULL, epoch INTEGER NOT NULL, PRIMARY KEY(account,id))");
  db.execSQL("CREATE TABLE legacy_scopes(account INTEGER NOT NULL,session TEXT NOT NULL,scope TEXT NOT NULL,PRIMARY KEY(account,session,scope),FOREIGN KEY(account,session) REFERENCES sessions(account,id) ON DELETE CASCADE)");
  db.execSQL("CREATE TABLE turns(account INTEGER NOT NULL, session TEXT NOT NULL, id TEXT NOT NULL, asked INTEGER NOT NULL, content TEXT, suppressed INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(account,session,id), FOREIGN KEY(account,session) REFERENCES sessions(account,id) ON DELETE CASCADE)");
  createOfflinePreferences(db);
  createTransfers(db);
  createTransferAliases(db);
 }
 @Override public void onUpgrade(SQLiteDatabase db, int oldVersion, int newVersion) {
  if(newVersion!=3||oldVersion<1||oldVersion>2)throw new IllegalStateException("Unsupported offline archive version");
  if(oldVersion==1){createOfflinePreferences(db);createTransfers(db);}
  createTransferAliases(db);
 }
 private static void createOfflinePreferences(SQLiteDatabase db) {
  db.execSQL("CREATE TABLE offline_preferences(account INTEGER PRIMARY KEY REFERENCES accounts(id) ON DELETE CASCADE, appearance TEXT)");
 }
 private static void createTransfers(SQLiteDatabase db) {
  db.execSQL("CREATE TABLE transfer_clears(account INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE, revision INTEGER NOT NULL, start INTEGER NOT NULL, end INTEGER NOT NULL, PRIMARY KEY(account,revision))");
  db.execSQL("CREATE TABLE transfer_tasks(account INTEGER PRIMARY KEY REFERENCES accounts(id) ON DELETE CASCADE, task TEXT NOT NULL)");
  db.execSQL("CREATE TABLE transfer_chunks(account INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE, version TEXT NOT NULL, part INTEGER NOT NULL, data BLOB NOT NULL, PRIMARY KEY(account,version,part))");
  db.execSQL("CREATE TABLE transfer_items(account INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE, version TEXT NOT NULL, session TEXT NOT NULL, turn TEXT NOT NULL, asked INTEGER NOT NULL, media TEXT, size INTEGER, attachment_key TEXT, PRIMARY KEY(account,version,session,turn))");
  db.execSQL("CREATE INDEX transfer_items_dates ON transfer_items(account,asked)");
 }
 private static void createTransferAliases(SQLiteDatabase db) {
  db.execSQL("CREATE TABLE transfer_attachment_keys(account INTEGER NOT NULL, version TEXT NOT NULL, session TEXT NOT NULL, turn TEXT NOT NULL, key TEXT NOT NULL, "+
      "PRIMARY KEY(account,version,session,turn,key), FOREIGN KEY(account,version,session,turn) REFERENCES transfer_items(account,version,session,turn) ON DELETE CASCADE)");
 }
 Account forAddress(String address) {
  try (Cursor row=getReadableDatabase().rawQuery("SELECT a.* FROM accounts a JOIN routes r ON r.account=a.id WHERE r.address=?",new String[]{address})) {
   return row.moveToFirst()?account(row):null;
  }
 }
 private Account forIdentity(String gateway,String identity) {
  try (Cursor row=getReadableDatabase().rawQuery("SELECT * FROM accounts WHERE gateway=? AND identity=?",new String[]{gateway,identity})) {
   return row.moveToFirst()?account(row):null;
  }
 }
 private static Account account(Cursor row) {
  return new Account(row.getLong(0),row.getString(1),row.getString(2),row.getString(3),row.getString(4),row.getInt(5)!=0,row.getLong(6),row.getLong(7));
 }
 Account bind(String address,String name,String gateway,String identity) {
  SQLiteDatabase db=getWritableDatabase();
  db.beginTransaction();
  try {
   Account previous=forAddress(address);
   if(previous!=null&&(!previous.gateway.equals(gateway)||!previous.identity.equals(identity))) {
    db.execSQL("UPDATE accounts SET retained=1, status=CASE WHEN status='valid' THEN 'loggedOut' ELSE status END WHERE id=?", new Object[]{previous.id});
   }
   db.execSQL("INSERT OR IGNORE INTO accounts(gateway,identity,name,status) VALUES(?,?,?,'valid')",new Object[]{gateway,identity,name});
   Account account=forIdentity(gateway,identity);
   db.execSQL("UPDATE accounts SET name=?,epoch=epoch+CASE WHEN status IN ('loggedOut','expired','unlinked') THEN 1 ELSE 0 END,status='valid',retained=0 WHERE id=?",new Object[]{name,account.id});
   db.execSQL("INSERT OR REPLACE INTO routes(address,account) VALUES(?,?)",new Object[]{address,account.id});
   db.setTransactionSuccessful();
   return forAddress(address);
  } finally { db.endTransaction(); }
 }
 void linkRoute(String from,String to) {
  getWritableDatabase().execSQL("INSERT OR IGNORE INTO routes(address,account) SELECT ?,account FROM routes WHERE address=?",new Object[]{to,from});
 }
 void status(Account account,String status) {
  if(account==null) return;
  SQLiteDatabase db=getWritableDatabase();
  if("revoked".equals(status)||"unknownRejected".equals(status)) {
   db.execSQL("UPDATE accounts SET status=?,epoch=epoch+1 WHERE id=? AND status NOT IN ('revoked','unknownRejected')",new Object[]{status,account.id});
  } else {
   db.execSQL("UPDATE accounts SET status=? WHERE id=? AND status NOT IN ('revoked','unknownRejected')",new Object[]{status,account.id});
  }
 }
 boolean unlink(String address,boolean erase)throws IOException {
  Account current=forAddress(address);
  if(current==null) return false;
  SQLiteDatabase db=getWritableDatabase();
  db.beginTransaction();
  try {
   if(erase){
    clearAccountTurns(db,current.id,Long.MIN_VALUE,Long.MAX_VALUE);
    db.execSQL("DELETE FROM transfer_tasks WHERE account=?",new Object[]{current.id});
   }
   status(current,"unlinked");
   db.execSQL("UPDATE accounts SET retained=1 WHERE id=?",new Object[]{current.id});
   db.delete("routes","account=?",new String[]{Long.toString(current.id)});
   db.setTransactionSuccessful();
  } finally { db.endTransaction(); }
  if(erase){
   List<Long> ids=java.util.Collections.singletonList(current.id);
   DownloadProvider.clearOffline(context,ids,Long.MIN_VALUE,Long.MAX_VALUE);
   clearStagedRecords(db,ids,Long.MIN_VALUE,Long.MAX_VALUE);
   compact(db);
  }
  return !erase && hasContent(current);
 }
 List<Account> available(boolean retainedOnly) {
  List<Account> values=new ArrayList<>();
  String filter=retainedOnly?"a.retained=1 AND ":"";
  String stored="EXISTS(SELECT 1 FROM turns t WHERE t.account=a.id AND t.suppressed=0)";
  String partial=retainedOnly?"":" OR EXISTS(SELECT 1 FROM transfer_items t WHERE t.account=a.id) OR EXISTS(SELECT 1 FROM transfer_tasks j WHERE j.account=a.id)";
  try(Cursor row=getReadableDatabase().rawQuery("SELECT a.* FROM accounts a WHERE "+filter+
        "("+stored+partial+") ORDER BY a.name,a.id",null)) {
   while(row.moveToNext()) values.add(account(row));
  }
  return values;
 }
 Account account(long id) {
  try(Cursor row=getReadableDatabase().rawQuery("SELECT * FROM accounts WHERE id=?",new String[]{Long.toString(id)})) {
   return row.moveToFirst()?account(row):null;
  }
 }
 JSONObject appearance(Account account)throws JSONException {
  try(Cursor row=getReadableDatabase().rawQuery("SELECT appearance FROM offline_preferences WHERE account=?",new String[]{Long.toString(account.id)})){
   return row.moveToFirst()?new JSONObject(row.getString(0)):null;
  }
 }
 void saveAppearance(Account account,JSONObject value)throws JSONException {
  String data=value.toString();
  if(data.length()>4096)throw new JSONException("Appearance too large");
  getWritableDatabase().execSQL("INSERT OR REPLACE INTO offline_preferences(account,appearance) VALUES(?,?)",new Object[]{account.id,data});
 }
 JSONObject tombstones(Account account,String sessionId) throws JSONException {
  JSONObject result=new JSONObject();
  if(account==null) return result;
  String sql="SELECT session,id FROM turns WHERE account=? AND suppressed=1";
  String accountId=Long.toString(account.id);
  String[] parameters=sessionId.isEmpty()?new String[]{accountId}:new String[]{accountId,sessionId};
  try(Cursor row=getReadableDatabase().rawQuery(sql+(sessionId.isEmpty()?"":" AND session=?")+" ORDER BY session",parameters)) {
   while(row.moveToNext()) {
    String session=row.getString(0);
    JSONArray ids=result.optJSONArray(session);
    if(ids==null) { ids=new JSONArray();result.put(session,ids); }
    ids.put(row.getString(1));
   }
  }
  return result;
 }
 private JSONObject existingTurn(SQLiteDatabase db,long account,String session,String id,boolean manual) throws JSONException {
  try(Cursor row=db.rawQuery("SELECT length(content),suppressed FROM turns WHERE account=? AND session=? AND id=?",new String[]{Long.toString(account),session,id})) {
   if(!row.moveToFirst())return new JSONObject();
   if(row.getInt(1)!=0&&!manual)return null;
   return row.isNull(0)?new JSONObject():readTurnContent(db,Long.toString(account),session,id,row.getInt(0));
  }
 }
 void merge(Account previous,JSONObject event,boolean manual,String legacy) throws JSONException {
  Account account=previous==null?null:account(previous.id);
  if(account==null||!"valid".equals(account.status))throw new JSONException("Login required");
  if(!Long.toString(account.clearRevision).equals(event.optString("clearRevision")))throw new JSONException("Archive was cleared");
  String session=event.getString("sessionId");
  JSONArray turns=event.getJSONArray("turns");
  long synced=event.getLong("syncedAt");
  SQLiteDatabase db=getWritableDatabase();
  db.beginTransaction();
  try {
   db.execSQL("INSERT OR IGNORE INTO sessions(account,id,title,synced,complete,epoch) VALUES(?,?,?,?,?,?)",new Object[]{account.id,session,event.optString("title"),synced,event.optBoolean("complete")?1:0,account.epoch});
   db.execSQL("UPDATE sessions SET title=CASE WHEN synced<=? AND ?<>'' THEN ? ELSE title END,synced=MAX(synced,?),complete=MAX(complete,?),epoch=? WHERE account=? AND id=?",
      new Object[]{synced,event.optString("title"),event.optString("title"),synced,event.optBoolean("complete")?1:0,account.epoch,account.id,session});
   boolean canRestore=manual;
   if(!legacy.isEmpty())db.execSQL("INSERT OR IGNORE INTO legacy_scopes(account,session,scope) VALUES(?,?,?)",new Object[]{account.id,session,legacy});
   for(int i=0;i<turns.length();i++) {
    JSONObject incoming=turns.getJSONObject(i);
    String id=incoming.getString("id");
    if(id.isEmpty()) throw new JSONException("Missing turn identity");
    long asked=incoming.getLong("questionTime");
    JSONObject merged=existingTurn(db,account.id,session,id,canRestore);
    if(merged==null) continue;
    mergeTurn(merged,incoming,synced);
    db.execSQL("INSERT OR IGNORE INTO turns(account,session,id,asked,content,suppressed) VALUES(?,?,?,?,?,0)",new Object[]{account.id,session,id,asked,merged.toString()});
    db.execSQL("UPDATE turns SET asked=CASE WHEN asked=0 THEN ? ELSE asked END,content=?,suppressed=0 WHERE account=? AND session=? AND id=?", new Object[]{asked,merged.toString(),account.id,session,id});
   }
   removeMessages(db,account.id,session,event.optJSONArray("deletedKeys"));
   db.setTransactionSuccessful();
  } finally { db.endTransaction(); }
 }
 private static void mergeTurn(JSONObject old,JSONObject incoming,long synced)throws JSONException {
  JSONObject messages=old.optJSONObject("messages");
  if(messages==null) messages=new JSONObject();
  JSONArray updates=incoming.optJSONArray("messages");
  if(updates!=null)for(int i=0;i<updates.length();i++) {
   JSONObject update=updates.getJSONObject(i);
   String key=update.getString("key");
   JSONObject previous=messages.optJSONObject(key);
   String version=update.getString("version");
   if(version.isEmpty())throw new JSONException("Missing version");
   if(previous==null||synced>=previous.optLong("_syncedAt")){
    JSONObject parts=previous!=null&&version.equals(previous.optString("version"))?previous.optJSONObject("bodyParts"):null;
    JSONObject imported=update.optJSONObject("bodyParts");
    if(imported!=null){
     if(parts==null)parts=new JSONObject();
     for(java.util.Iterator<String> offsets=imported.keys();offsets.hasNext();){
      String offset=offsets.next();
      parts.put(offset,imported.getJSONObject(offset));
     }
    }
    JSONObject part=update.optJSONObject("bodyPart");
    if(part!=null){
     if(parts==null)parts=new JSONObject();
     parts.put(Long.toString(part.getLong("offset")),part);
    }
    update.remove("bodyPart");
    if(parts!=null)update.put("bodyParts",parts);
    update.put("_syncedAt",synced);messages.put(key,update);
   }else if(version.equals(previous.optString("version"))){
    JSONObject verified=update.optJSONObject("bodyParts");
    if(verified!=null){
     JSONObject parts=previous.optJSONObject("bodyParts");
     if(parts==null)parts=new JSONObject();
     for(java.util.Iterator<String> offsets=verified.keys();offsets.hasNext();){
      String offset=offsets.next();
      if(!parts.has(offset))parts.put(offset,verified.getJSONObject(offset));
     }
     previous.put("bodyParts",parts);
    }
   }
  }
  if(incoming.has("question"))old.put("question",incoming.getString("question"));
  if(incoming.has("answer"))old.put("answer",incoming.getString("answer"));
  old.put("messages",messages);
 }
 private static void removeMessages(SQLiteDatabase db,long account,String session,JSONArray deleted)throws JSONException {
  if(deleted==null||deleted.length()==0)return;
  String accountId=Long.toString(account);
  try(Cursor row=db.rawQuery("SELECT id,length(content) FROM turns WHERE account=? AND session=? AND suppressed=0",new String[]{accountId,session})) {
   while(row.moveToNext()) {
    JSONObject content=readTurnContent(db,accountId,session,row.getString(0),row.getInt(1)),messages=content.optJSONObject("messages");
    if(messages==null)continue;
    boolean changed=false;
    for(int i=0;i<deleted.length();i++)if(messages.remove(deleted.getString(i))!=null)changed=true;
    if(changed){
     content.remove("question");content.remove("answer");
     db.execSQL("UPDATE turns SET content=? WHERE account=? AND session=? AND id=?",new Object[]{content.toString(),account,session,row.getString(0)});
    }
   }
  }
 }
 List<Session> sessions(Account account) {
  List<Session> values=new ArrayList<>();
  if(account==null||locked(account))return values;
  try(Cursor row=getReadableDatabase().rawQuery("SELECT s.id,s.title,s.synced,s.complete FROM sessions s WHERE s.account=? AND s.epoch=? "+
     "AND EXISTS(SELECT 1 FROM turns t WHERE t.account=s.account AND t.session=s.id AND t.suppressed=0) ORDER BY s.synced DESC,s.id",
      new String[]{Long.toString(account.id),Long.toString(account.epoch)})) {
   while(row.moveToNext())values.add(new Session(row.getString(0),row.getString(1),row.getLong(2),row.getInt(3)!=0));
  }
  return values;
 }
 JSONObject readerSession(Account account,String session,String before)throws JSONException {
  if(account==null||locked(account))throw new JSONException("Archive unavailable");
  SQLiteDatabase db=getReadableDatabase();
  String[] scope={Long.toString(account.id),session,Long.toString(account.epoch)};
  Session result;
  try(Cursor row=db.rawQuery("SELECT id,title,synced,complete FROM sessions WHERE account=? AND id=? AND epoch=?",scope)){
   if(!row.moveToFirst())throw new JSONException("Session unavailable");
   result=new Session(row.getString(0),row.getString(1),row.getLong(2),row.getInt(3)!=0);
  }
  List<String> values=new ArrayList<>(java.util.Arrays.asList(scope));
  String seek="";
  if(!before.isEmpty()){
   JSONArray cursor=new JSONArray(before);
   if(cursor.length()!=2)throw new JSONException("Invalid cursor");
   String time=Long.toString(cursor.getLong(0)),id=cursor.getString(1);
   seek=" AND (t.asked<CAST(? AS INTEGER) OR (t.asked=CAST(? AS INTEGER) AND t.id<?))";
   values.add(time);values.add(time);values.add(id);
  }
  List<JSONObject> page=new ArrayList<>();
  int total=0;
  String next=null;
  boolean more=false;
  try(Cursor rows=db.rawQuery("SELECT t.id,t.asked,length(t.content) FROM turns t JOIN sessions s "+
     "ON t.account=s.account AND t.session=s.id WHERE t.account=? AND t.session=? AND s.epoch=? AND t.suppressed=0"+
     seek+" ORDER BY t.asked DESC,t.id DESC LIMIT "+(READER_PAGE+1),values.toArray(new String[0]))){
   while(rows.moveToNext()){
    String id=rows.getString(0);long asked=rows.getLong(1);int length=rows.getInt(2);
    if(page.size()>=READER_PAGE||!page.isEmpty()&&(long)total+length>READER_CHARS){more=true;break;}
    JSONObject content=readTurnContent(db,scope[0],session,id,length);
    page.add(new JSONObject().put("id",id).put("questionTime",asked).put("content",content));
    next=new JSONArray().put(asked).put(id).toString();
    total+=length;
   }
  }
  JSONArray turns=new JSONArray();
  for(int i=page.size()-1;i>=0;i--)turns.put(page.get(i));
  return new JSONObject().put("id",result.id).put("title",result.title).put("syncedAt",result.syncedAt)
    .put("gaps",!result.complete||hasGaps(account,session)).put("turns",turns)
    .put("before",more&&next!=null?next:JSONObject.NULL);
 }
 private static JSONObject readTurnContent(SQLiteDatabase db,String account,String session,String id,int length)throws JSONException {
  // Read a large stored turn across CursorWindows without imposing a history size limit.
  StringBuilder value=new StringBuilder();
  final int chunk=64*1024;
  for(int offset=1;offset<=length;offset+=chunk){
   try(Cursor row=db.rawQuery("SELECT substr(content,?,?) FROM turns WHERE account=? AND session=? AND id=? AND suppressed=0",
     new String[]{Integer.toString(offset),Integer.toString(chunk),account,session,id})){
    if(!row.moveToFirst()||row.isNull(0))throw new JSONException("Turn unavailable");
    value.append(row.getString(0));
   }
  }
  return value.length()==0?new JSONObject():new JSONObject(value.toString());
 }
 JSONObject body(Account account,JSONObject command)throws JSONException {
  if(account==null||!"valid".equals(account.status))return null;
  JSONObject wanted=command.getJSONObject("body");
  String key=wanted.getString("key"),version=wanted.getString("version");
  String offset=Long.toString(wanted.getLong("offset"));
  String accountId=Long.toString(account.id),session=command.getString("sessionId");
  SQLiteDatabase db=getReadableDatabase();
  try(Cursor rows=db.rawQuery("SELECT t.id,length(t.content) FROM turns t JOIN sessions s ON t.account=s.account AND t.session=s.id WHERE t.account=? AND t.session=? AND t.suppressed=0 AND s.epoch=?",
      new String[]{accountId,session,Long.toString(account.epoch)})){
   while(rows.moveToNext()){
    JSONObject messages=readTurnContent(db,accountId,session,rows.getString(0),rows.getInt(1)).optJSONObject("messages");
    JSONObject item=messages==null?null:messages.optJSONObject(key);
    if(item==null||!version.equals(item.optString("version")))continue;
    JSONObject parts=item.optJSONObject("bodyParts");
    JSONObject part=parts==null?null:parts.optJSONObject(offset);
    if(part!=null)return part.put("key",key).put("version",version);
   }
  }
  return null;
 }
 JSONArray seed(Account account,JSONObject command)throws JSONException {
  JSONArray result=new JSONArray();
  if(account==null||!"valid".equals(account.status))return result;
  Set<String> keys=stringSet(command.optJSONArray("keys")),ids=stringSet(command.optJSONArray("turnIds"));
  String session=command.getString("sessionId");
  int bytes=0;
  String accountId=Long.toString(account.id);
  SQLiteDatabase db=getReadableDatabase();
  try(Cursor rows=db.rawQuery("SELECT t.id,t.asked,length(t.content) FROM turns t JOIN sessions s ON t.account=s.account AND t.session=s.id WHERE t.account=? AND t.session=? AND t.suppressed=0 AND s.epoch=? ORDER BY t.asked DESC,t.rowid DESC",
      new String[]{accountId,session,Long.toString(account.epoch)})){
   while(result.length()<100&&rows.moveToNext()){
    String id=rows.getString(0);
    JSONObject stored=readTurnContent(db,accountId,session,id,rows.getInt(2)),messages=stored.optJSONObject("messages");
    if(messages==null)continue;
    boolean includeTurn=ids.isEmpty()&&keys.isEmpty()||ids.contains(id);
    JSONArray items=new JSONArray();
    for(java.util.Iterator<String> it=messages.keys();it.hasNext();){
     String key=it.next();
     if(!includeTurn&&!keys.contains(key))continue;
     JSONObject item=messages.getJSONObject(key);item.remove("_syncedAt");
     JSONObject parts=item.optJSONObject("bodyParts");item.remove("bodyParts");
     if(parts!=null){
      JSONArray chunks=new JSONArray();long offset=0;
      while(chunks.length()<2){
       JSONObject part=parts.optJSONObject(Long.toString(offset));
       if(part==null)break;
       chunks.put(part);long next=part.optLong("next",-1);if(next<=offset)break;offset=next;
      }
      if(chunks.length()>0)item.put("bodyParts",chunks);
     }
     items.put(item);
    }
    if(items.length()==0)continue;
    JSONObject turn=new JSONObject().put("id",id).put("questionTime",rows.getLong(1)).put("messages",items);
    int size=turn.toString().getBytes(java.nio.charset.StandardCharsets.UTF_8).length;
    if(bytes+size>512_000)continue;
    result.put(turn);bytes+=size;
   }
  }
  return result;
 }
 private static Set<String> stringSet(JSONArray input)throws JSONException {
  Set<String> values=new HashSet<>();
  if(input!=null)for(int i=0;i<input.length()&&i<100;i++)values.add(input.getString(i));
  return values;
 }
 boolean hasCachedSession(Account account,String session) {
  try(Cursor row=getReadableDatabase().rawQuery("SELECT 1 FROM turns WHERE account=? AND session=? AND suppressed=0 LIMIT 1",
      new String[]{Long.toString(account.id),session})){return row.moveToFirst();}
 }
 boolean hasGaps(Account account,String session) {
  try(Cursor row=getReadableDatabase().rawQuery("SELECT 1 FROM turns WHERE account=? AND session=? AND suppressed=1 LIMIT 1",new String[]{Long.toString(account.id),session})){return row.moveToFirst();}
 }
 boolean hasContent(Account account) {
  if(account==null)return false;
  try(Cursor row=getReadableDatabase().rawQuery("SELECT 1 FROM turns WHERE account=? AND suppressed=0 LIMIT 1",new String[]{Long.toString(account.id)})){return row.moveToFirst();}
 }
 Set<String> legacyScopes(List<Long> accounts,long start,long end) {
  Set<String> scopes=new HashSet<>();
  for(long id:accounts)try(Cursor rows=getReadableDatabase().rawQuery(
    "SELECT DISTINCT l.scope FROM legacy_scopes l JOIN turns t ON t.account=l.account AND t.session=l.session WHERE t.account=? AND t.asked>=? AND t.asked<? AND t.suppressed=0",
    new String[]{Long.toString(id),Long.toString(start),Long.toString(end)})){
   while(rows.moveToNext())scopes.add(rows.getString(0));
  }
  return scopes;
 }
 private static long databaseBytes(SQLiteDatabase db) {
  long bytes=0;
  for(String suffix:new String[]{"","-wal","-journal","-shm"})bytes+=new File(db.getPath()+suffix).length();
  return bytes;
 }
 private static void compact(SQLiteDatabase db) {
  db.execSQL("VACUUM");
  try(Cursor result=db.rawQuery("PRAGMA wal_checkpoint(TRUNCATE)",null)){result.moveToFirst();}
 }
 long clearTurns(List<Long> accounts,long start,long end)throws IOException {
  SQLiteDatabase db=getWritableDatabase();
  long before=databaseBytes(db);
  db.beginTransaction();
  try{
   for(long id:accounts)clearAccountTurns(db,id,start,end);
   db.setTransactionSuccessful();
  }finally{db.endTransaction();}
  long files=DownloadProvider.clearOffline(context,accounts,start,end)+clearStagedRecords(db,accounts,start,end);
  compact(db);
  return Math.max(0,before-databaseBytes(db))+files;
 }
 private static long clearStagedRecords(SQLiteDatabase db,List<Long> accounts,long start,long end)throws IOException {
  File folder=new File(db.getPath()).getParentFile();
  File[] files=folder.listFiles(file->file.isFile()&&file.getName().startsWith(".transfer-"));
  long released=0;
  if(files==null)return released;
  for(File file:files){
   String[] parts=file.getName().substring(10).split("-",3);
   if(parts.length!=3)continue;
   long account,asked;
   try{account=Long.parseLong(parts[0]);asked=Long.parseLong(parts[1]);}catch(NumberFormatException ignored){continue;}
   if(!accounts.contains(account)||asked<start||asked>=end)continue;
   long length=file.length();
   if(java.nio.file.Files.deleteIfExists(file.toPath()))released+=length;
  }
  return released;
 }
 private static void clearAccountTurns(SQLiteDatabase db,long id,long start,long end) {
  Object[] scope={id,start,end};
  db.execSQL("UPDATE accounts SET clear_revision=clear_revision+1 WHERE id=?",new Object[]{id});
  db.execSQL("INSERT INTO transfer_clears(account,revision,start,end) SELECT id,clear_revision,?,? FROM accounts WHERE id=?",new Object[]{start,end,id});
  db.execSQL("UPDATE turns SET content=NULL,suppressed=1 WHERE account=? AND asked>=? AND asked<? AND suppressed=0",scope);
  db.execSQL("INSERT OR IGNORE INTO sessions(account,id,title,synced,complete,epoch) SELECT t.account,t.session,'',0,0,a.epoch "+
      "FROM transfer_items t JOIN accounts a ON a.id=t.account WHERE t.account=? AND t.asked>=? AND t.asked<?",scope);
  db.execSQL("INSERT OR IGNORE INTO turns(account,session,id,asked,suppressed) SELECT t.account,t.session,t.turn,t.asked,1 "+
      "FROM transfer_items t JOIN sessions s ON s.account=t.account AND s.id=t.session WHERE t.account=? AND t.asked>=? AND t.asked<?",scope);
  db.execSQL("DELETE FROM transfer_items WHERE account=? AND asked>=? AND asked<?",scope);
  dropOrphanChunks(db,id);
 }
 private static void dropOrphanChunks(SQLiteDatabase db,long id) {
  db.execSQL("DELETE FROM transfer_chunks WHERE account=? AND NOT EXISTS(SELECT 1 FROM transfer_items i "+
      "WHERE i.account=transfer_chunks.account AND i.version=transfer_chunks.version)",new Object[]{id});
 }

 static final int TRANSFER_CHUNK_BYTES=524_288;
 long freeBytes() {
  File parent=new File(getReadableDatabase().getPath()).getParentFile();
  return new StatFs(parent.getPath()).getAvailableBytes();
 }
 static String transferVersion(JSONObject event)throws JSONException {
  String version=event.getString("version");
  if(!version.matches("[a-f0-9]{64}"))throw new JSONException("Invalid transfer version");
  return version;
 }
 static int transferIndex(JSONObject event)throws JSONException {
  long index=event.getLong("index");
  if(index<0||index>16_777_215)throw new JSONException("Invalid transfer index");
  return (int)index;
 }
 private static final class TransferItem {
  final String version,session,turn;
  final long asked,bytes,taskRevision;
  TransferItem(JSONObject event)throws JSONException {
   version=transferVersion(event);session=event.getString("sessionId");turn=event.getString("turnId");
   asked=event.getLong("questionTime");bytes=event.getLong("bytes");
   taskRevision=event.has("taskClearRevision")?event.getLong("taskClearRevision"):event.getLong("clearRevision");
   if(session.isEmpty()||session.length()>4096||turn.isEmpty()||turn.length()>4096||asked<0||bytes<1||bytes>8_796_093_022_208L||taskRevision<0)
    throw new JSONException("Invalid transfer item");
  }
  int count(){return (int)((bytes-1)/TRANSFER_CHUNK_BYTES+1);}
  String[] scope(Account account){return new String[]{Long.toString(account.id),version,session,turn};}
 }
 private boolean transferCleared(Account account,TransferItem item) {
  String asked=Long.toString(item.asked);
  try(Cursor row=getReadableDatabase().rawQuery("SELECT 1 FROM transfer_clears WHERE account=? AND revision>? AND start<=? AND end>? LIMIT 1",
      new String[]{Long.toString(account.id),Long.toString(item.taskRevision),asked,asked})){return row.moveToFirst();}
 }
 String transferAdmission(Account account,JSONObject event)throws JSONException {
  TransferItem item=new TransferItem(event);
  if(transferCleared(account,item))return "cleared";
  return Long.toString(account.clearRevision).equals(event.optString("clearRevision"))?null:"revision";
 }
 static JSONObject transferOutcome(String reason)throws JSONException {
  JSONObject value=new JSONObject().put("saved",reason==null);
  if(reason!=null)value.put("reason",reason);
  return value;
 }
 boolean transferChunkHas(Account account,String version,int index) {
  try(Cursor row=getReadableDatabase().rawQuery("SELECT 1 FROM transfer_chunks WHERE account=? AND version=? AND part=?",
     new String[]{Long.toString(account.id),version,Integer.toString(index)})){return row.moveToFirst();}
 }
 byte[] transferChunk(Account account,String version,int index) {
  try(Cursor row=getReadableDatabase().rawQuery("SELECT data FROM transfer_chunks WHERE account=? AND version=? AND part=?",
     new String[]{Long.toString(account.id),version,Integer.toString(index)})){return row.moveToFirst()?row.getBlob(0):null;}
 }
 private static void retainTransferItem(SQLiteDatabase db,Account account,TransferItem item) {
  db.execSQL("INSERT OR IGNORE INTO transfer_items(account,version,session,turn,asked) VALUES(?,?,?,?,?)",
      new Object[]{account.id,item.version,item.session,item.turn,item.asked});
 }
 JSONObject putTransferChunk(Account previous,JSONObject event,byte[] data,String hash)throws Exception {
  Account account=account(previous.id);
  if(account==null||account.epoch!=previous.epoch||!"valid".equals(account.status))return transferOutcome("permission");
  String reason=transferAdmission(account,event);
  if(reason!=null)return transferOutcome(reason);
  TransferItem item=new TransferItem(event);
  int index=transferIndex(event);
  long expected=Math.min(TRANSFER_CHUNK_BYTES,item.bytes-(long)index*TRANSFER_CHUNK_BYTES);
  if(index>=item.count()||data.length!=expected||!hex(MessageDigest.getInstance("SHA-256").digest(data)).equals(hash))return transferOutcome("integrity");
  if(!transferChunkHas(account,item.version,index)&&freeBytes()<data.length*2L+1_048_576)return transferOutcome("space");
  SQLiteDatabase db=getWritableDatabase();
  try{
   db.beginTransaction();
   try{
    retainTransferItem(db,account,item);
    db.execSQL("INSERT OR IGNORE INTO transfer_chunks(account,version,part,data) VALUES(?,?,?,?)",new Object[]{account.id,item.version,index,data});
    db.setTransactionSuccessful();
   }finally{db.endTransaction();}
  }catch(SQLiteFullException full){return transferOutcome("space");}
  return transferOutcome(null);
 }
 private static String hex(byte[] bytes) {
  StringBuilder value=new StringBuilder(bytes.length*2);
  for(byte b:bytes)value.append(Character.forDigit((b>>4)&15,16)).append(Character.forDigit(b&15,16));
  return value.toString();
 }
 private long storedTransferBytes(Account account,TransferItem item) {
  return DatabaseUtils.longForQuery(getReadableDatabase(),"SELECT COALESCE(SUM(length(data)),0) FROM transfer_chunks WHERE account=? AND version=? AND part<?",
      new String[]{Long.toString(account.id),item.version,Integer.toString(item.count())});
 }
 JSONObject transferCached(Account account,JSONObject event)throws JSONException {
  TransferItem item=new TransferItem(event);
  if(transferCleared(account,item)){
   if("record".equals(event.optString("kind"))){
    SQLiteDatabase db=getWritableDatabase();
    db.beginTransaction();
    try{
     db.execSQL("INSERT OR IGNORE INTO sessions(account,id,title,synced,complete,epoch) VALUES(?,?,'',0,0,?)",
         new Object[]{account.id,item.session,account.epoch});
     db.execSQL("INSERT OR IGNORE INTO turns(account,session,id,asked,suppressed) VALUES(?,?,?,?,1)",
         new Object[]{account.id,item.session,item.turn,item.asked});
     db.setTransactionSuccessful();
    }finally{db.endTransaction();}
   }
   return new JSONObject().put("present",false).put("cleared",true).put("missingBytes",0);
  }
  boolean present;
  SQLiteDatabase db=getReadableDatabase();
  if("attachment".equals(event.optString("kind"))){
   String key=transferAttachmentKey(event);
   try(Cursor row=db.rawQuery("SELECT 1 FROM transfer_items i WHERE i.account=? AND i.version=? AND i.session=? AND i.turn=? "+
       "AND i.media IS NOT NULL AND "+TRANSFER_ATTACHMENT_ALIAS,
       new String[]{Long.toString(account.id),item.version,item.session,item.turn,key,key})){present=row.moveToFirst();}
  }else{
   String[] scope={Long.toString(account.id),item.session,Long.toString(account.epoch)};
   try(Cursor row=db.rawQuery("SELECT length(t.content) FROM turns t JOIN sessions s ON t.account=s.account AND t.session=s.id "+
       "WHERE t.account=? AND t.session=? AND s.epoch=? AND t.id=? AND t.suppressed=0",new String[]{scope[0],scope[1],scope[2],item.turn})){
    present=false;
    if(row.moveToFirst()){
     JSONObject messages=readTurnContent(db,scope[0],item.session,item.turn,row.getInt(0)).optJSONObject("messages");
     JSONObject message=messages==null?null:messages.optJSONObject(event.optString("key"));
     present=message!=null&&message.optString("version").equals(event.optString("recordVersion"))&&completeMessage(message,event.optLong("textLength",-1));
    }
   }
  }
  long missing=present?0:Math.max(0,item.bytes-storedTransferBytes(account,item));
  return new JSONObject().put("present",present).put("cleared",false).put("missingBytes",missing);
 }
 private static boolean completeMessage(JSONObject message,long length) {
  JSONObject row=message.optJSONObject("row");
  if(row==null)return false;
  if(!row.optBoolean("truncated")&&(message.opt("text") instanceof String||row.opt("text") instanceof String)){
   return length<0||message.optString("text",row.optString("text")).length()==length;
  }
  JSONObject parts=message.optJSONObject("bodyParts");
  if(parts==null)return false;
  long offset=0,utf16Length=0;
  for(;;){
   JSONObject part=parts.optJSONObject(Long.toString(offset));
   if(part==null||!(part.opt("text") instanceof String))return false;
   String text=part.optString("text");
   int width=text.length();
   utf16Length+=width;
   if(part.has("next")&&part.isNull("next"))return length<0||length==utf16Length;
   long next=part.optLong("next",-1);
   if(next<=offset||next-offset!=width&&next-offset!=text.codePointCount(0,width))return false;
   offset=next;
  }
 }
 JSONObject completeTransferSession(Account account,JSONObject event)throws JSONException {
  String session=event.getString("sessionId");
  if(session.isEmpty()||session.length()>4096)throw new JSONException("Invalid transfer session");
  if(!Long.toString(account.clearRevision).equals(event.optString("clearRevision")))return transferOutcome("revision");
  long revision=event.getLong("taskClearRevision");
  if(revision<0)throw new JSONException("Invalid transfer revision");
  SQLiteDatabase db=getWritableDatabase();
  try(Cursor row=db.rawQuery("SELECT 1 FROM turns t WHERE t.account=? AND t.session=? AND "+
      "(t.suppressed=1 OR EXISTS(SELECT 1 FROM transfer_clears c WHERE c.account=t.account AND c.revision>? "+
      "AND c.start<=t.asked AND c.end>t.asked)) LIMIT 1",new String[]{Long.toString(account.id),session,Long.toString(revision)})){
   if(row.moveToFirst())return new JSONObject().put("saved",true).put("complete",false);
  }
  db.execSQL("UPDATE sessions SET complete=1 WHERE account=? AND id=? AND epoch=?",new Object[]{account.id,session,account.epoch});
  return new JSONObject().put("saved",true).put("complete",true);
 }
 JSONObject transferTask(Account account)throws JSONException {
  try(Cursor row=getReadableDatabase().rawQuery("SELECT task FROM transfer_tasks WHERE account=?",new String[]{Long.toString(account.id)})){
   return new JSONObject().put("task",row.moveToFirst()?new JSONObject(row.getString(0)):JSONObject.NULL);
  }
 }
 JSONObject saveTransferTask(Account account,JSONObject event)throws JSONException {
  SQLiteDatabase db=getWritableDatabase();
  if(event.has("task")&&event.isNull("task"))db.execSQL("DELETE FROM transfer_tasks WHERE account=?",new Object[]{account.id});
  else {
   String task=event.getJSONObject("task").toString();
   if(task.getBytes(StandardCharsets.UTF_8).length>262_144)throw new JSONException("Transfer task too large");
   db.execSQL("INSERT OR REPLACE INTO transfer_tasks(account,task) VALUES(?,?)",new Object[]{account.id,task});
  }
  return transferOutcome(null);
 }
 JSONObject commitTransfer(Account account,JSONObject event,boolean attachment)throws Exception {
  String reason=transferAdmission(account,event);
  if(reason!=null)return transferOutcome(reason);
  TransferItem item=new TransferItem(event);
  SQLiteDatabase db=getWritableDatabase();
  MessageDigest digest=MessageDigest.getInstance("SHA-256");
  File temp=null;
  try{
   if(!attachment){
    if(freeBytes()<item.bytes*3+1_048_576)return transferOutcome("space");
    temp=File.createTempFile(".transfer-"+account.id+"-"+item.asked+"-", ".json",new File(db.getPath()).getParentFile());
   }
   long count=0;
   try(BufferedOutputStream output=temp==null?null:new BufferedOutputStream(new FileOutputStream(temp))) {
    for(int index=0;index<item.count();index++){
     byte[] block=transferChunk(account,item.version,index);
     if(block==null)return transferOutcome("missing");
     count+=block.length;
     digest.update(block);
     if(output!=null)output.write(block);
    }
   }
   if(count!=item.bytes||!hex(digest.digest()).equals(item.version))return transferOutcome("integrity");
   if(attachment) {
    String media=event.optString("mediaType","application/octet-stream");
    String key=transferAttachmentKey(event);
    if(media.length()>128||!media.matches("[a-zA-Z0-9.+-]+/[a-zA-Z0-9.+-]+")||key.length()>4096)throw new JSONException("Invalid attachment");
    db.beginTransaction();
    try{
     retainTransferItem(db,account,item);
     db.execSQL("UPDATE transfer_items SET media=?,size=?,attachment_key=COALESCE(attachment_key,?) WHERE account=? AND version=? AND session=? AND turn=?",
         new Object[]{media,item.bytes,key,account.id,item.version,item.session,item.turn});
     db.execSQL("INSERT OR IGNORE INTO transfer_attachment_keys(account,version,session,turn,key) VALUES(?,?,?,?,?)",
         new Object[]{account.id,item.version,item.session,item.turn,key});
     db.setTransactionSuccessful();
    }finally{db.endTransaction();}
   }else{
    JSONObject turn;
    try(JsonReader input=new JsonReader(new InputStreamReader(new FileInputStream(temp),StandardCharsets.UTF_8))){
     turn=(JSONObject)readTransferJSON(input,0);
     if(input.peek()!=android.util.JsonToken.END_DOCUMENT)return transferOutcome("integrity");
    }
    if(!item.turn.equals(turn.optString("id"))||item.asked!=turn.getLong("questionTime"))return transferOutcome("integrity");
    merge(account,event.put("turns",new JSONArray().put(turn)),true,"");
    db.beginTransaction();
    try{
     db.execSQL("DELETE FROM transfer_items WHERE account=? AND version=? AND session=? AND turn=?",item.scope(account));
     dropOrphanChunks(db,account.id);
     db.setTransactionSuccessful();
    }finally{db.endTransaction();}
   }
   return transferOutcome(null);
  }catch(SQLiteFullException error){return transferOutcome("space");}
  catch(IOException error){if(freeBytes()<item.bytes+1_048_576)return transferOutcome("space");throw error;}
  finally{if(temp!=null)temp.delete();}
 }
 private static Object readTransferJSON(JsonReader input,int depth)throws IOException,JSONException {
  if(depth>64)throw new JSONException("Transfer record is nested too deeply");
  switch(input.peek()){
   case BEGIN_OBJECT:
    JSONObject object=new JSONObject();input.beginObject();
    while(input.hasNext())object.put(input.nextName(),readTransferJSON(input,depth+1));
    input.endObject();return object;
   case BEGIN_ARRAY:
    JSONArray array=new JSONArray();input.beginArray();
    while(input.hasNext())array.put(readTransferJSON(input,depth+1));
    input.endArray();return array;
   case NUMBER:
    String value=input.nextString();
    try{return Long.parseLong(value);}catch(NumberFormatException decimal){return Double.parseDouble(value);}
   case BOOLEAN:return input.nextBoolean();
   case STRING:return input.nextString();
   case NULL:input.nextNull();return JSONObject.NULL;
   default:throw new JSONException("Invalid transfer JSON");
  }
 }
 private static String transferAttachmentKey(JSONObject event){
  return event.optString("attachmentKey",event.optString("path",event.optString("key")));
 }
 private static final String TRANSFER_ATTACHMENT_ALIAS="(i.attachment_key=? OR EXISTS(SELECT 1 FROM transfer_attachment_keys k "+
     "WHERE k.account=i.account AND k.version=i.version AND k.session=i.session AND k.turn=i.turn AND k.key=?))";
 JSONObject transferAttachment(Account account,JSONObject event)throws JSONException {
  String session=event.optString("sessionId"),turn=event.optString("turnId");
  if(session.isEmpty()||session.length()>4096||turn.isEmpty()||turn.length()>4096)return null;
  boolean isVersion=event.has("version");
  String key=isVersion?transferVersion(event):event.optString("attachmentKey");
  if(key.isEmpty()||key.length()>4096)return null;
  String accountId=Long.toString(account.id),epoch=Long.toString(account.epoch);
  String[] scope=isVersion?new String[]{accountId,session,turn,key,epoch}:new String[]{accountId,session,turn,key,key,epoch};
  try(Cursor row=getReadableDatabase().rawQuery("SELECT i.version,i.size,i.media,i.asked FROM transfer_items i "+
      "JOIN sessions s ON s.account=i.account AND s.id=i.session JOIN turns t ON t.account=i.account AND t.session=i.session AND t.id=i.turn "+
      "WHERE i.account=? AND i.session=? AND i.turn=? AND "+(isVersion?"i.version=?":TRANSFER_ATTACHMENT_ALIAS)+" "+
      "AND i.media IS NOT NULL AND s.epoch=? AND t.suppressed=0 ORDER BY i.rowid DESC LIMIT 1",scope)){
   if(!row.moveToFirst())return null;
   String version=row.getString(0);
   if(event.has("index")){
    byte[] block=transferChunk(account,version,transferIndex(event));
    return block==null?null:new JSONObject().put("data",Base64.encodeToString(block,Base64.NO_WRAP));
   }
   return new JSONObject().put("version",version).put("bytes",row.getLong(1)).put("mediaType",row.getString(2))
      .put("chunkBytes",TRANSFER_CHUNK_BYTES).put("questionTime",row.getLong(3));
  }
 }
 android.net.Uri exportTransferAttachment(Account account,JSONObject event)throws Exception {
  if(event.has("index"))return null;
  JSONObject meta=transferAttachment(account,event);
  if(meta==null)return null;
  long bytes=meta.getLong("bytes"),asked=meta.getLong("questionTime");
  if(freeBytes()<bytes)return null;
  String version=meta.getString("version"),name=ArtifactDownload.safeName(event.optString("name"));
  if(name.getBytes(StandardCharsets.UTF_8).length>240||".offline-date".equals(name))name="attachment";
  return DownloadProvider.prepareOffline(context,account.id,asked,name,output->{
   int count=(int)((bytes-1)/TRANSFER_CHUNK_BYTES+1);
   for(int index=0;index<count;index++){
    byte[] data=transferChunk(account,version,index);
    if(data==null)throw new IOException("Attachment was cleared");
    output.write(data);
   }
  });
 }
 static boolean locked(Account account) {
  return "revoked".equals(account.status)||"unknownRejected".equals(account.status);
 }
}
