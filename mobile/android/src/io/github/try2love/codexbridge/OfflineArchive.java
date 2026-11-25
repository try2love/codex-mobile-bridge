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
  super(context, new File(context.getNoBackupFilesDir(), "offline-archive.db").getPath(), null, 5);
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
  createRecords(db);
  createComposer(db);
 }
 @Override public void onUpgrade(SQLiteDatabase db, int oldVersion, int newVersion) {
  if(newVersion!=5||oldVersion<1||oldVersion>4)throw new IllegalStateException("Unsupported offline archive version");
  if(oldVersion==1){createOfflinePreferences(db);createTransfers(db);}
  if(oldVersion<3)createTransferAliases(db);
  if(oldVersion<4)createRecords(db);
  createComposer(db);
 }
 private static void createComposer(SQLiteDatabase db) {
  db.execSQL("CREATE TABLE composer(account INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE, session TEXT NOT NULL, revision INTEGER NOT NULL, content TEXT NOT NULL, pending INTEGER NOT NULL, active INTEGER NOT NULL, title TEXT NOT NULL, changed INTEGER NOT NULL, PRIMARY KEY(account,session))");
 }
 static boolean composerAction(String action) {
  return "composerRead".equals(action)||"composerWrite".equals(action)||"composerPending".equals(action);
 }
 private static JSONObject readComposer(SQLiteDatabase db,String account,String session)throws JSONException {
  try(Cursor row=db.rawQuery("SELECT revision,content FROM composer WHERE account=? AND session=?",new String[]{account,session})) {
   if(row.moveToFirst())return new JSONObject().put("revision",row.getLong(0)).put("value",new JSONObject(row.getString(1)));
  }
  return new JSONObject().put("revision",0).put("value",JSONObject.NULL);
 }
 JSONObject composer(Account previous,String action,JSONObject input)throws JSONException {
  Account account=account(previous.id);
  if(account==null||locked(account)||account.epoch!=previous.epoch||
    !account.gateway.equals(previous.gateway)||!account.identity.equals(previous.identity))
   throw new IllegalArgumentException("Invalid composer account");
  String accountId=Long.toString(account.id);
  if("composerPending".equals(action)){
   JSONArray sessions=new JSONArray();
   try(Cursor row=getReadableDatabase().rawQuery("SELECT session FROM composer WHERE account=? AND pending=1 AND session<>'' ORDER BY session",new String[]{accountId})){
    while(row.moveToNext())sessions.put(row.getString(0));
   }
   return new JSONObject().put("sessions",sessions);
  }
  Object rawSession=input.opt("sessionId");
  if(!(rawSession instanceof String))throw new IllegalArgumentException("Invalid composer session");
  String session=(String)rawSession;
  if(session.indexOf(0)>=0||session.getBytes(StandardCharsets.UTF_8).length>4096)throw new IllegalArgumentException("Invalid composer session");
  if("composerRead".equals(action))return readComposer(getReadableDatabase(),accountId,session);
  Object expected=input.opt("revision");
  JSONObject value=input.optJSONObject("value");
  if(!"composerWrite".equals(action)||!"valid".equals(account.status)||!(expected instanceof Number)||value==null)
   throw new IllegalArgumentException("Invalid composer write");
  double number=((Number)expected).doubleValue();
  long revision=((Number)expected).longValue();
  String encoded=value.toString();
  if(number!=revision||revision<0||revision>=9_007_199_254_740_991L||encoded.getBytes(StandardCharsets.UTF_8).length>1_048_576)
   throw new IllegalArgumentException("Invalid composer write");
  SQLiteDatabase db=getWritableDatabase();
  db.beginTransaction();
  try {
   JSONObject current=readComposer(db,accountId,session);
   if(current.getLong("revision")!=revision)return current.put("applied",false);
   boolean pending=hasComposerItems(value,"outbox");
   JSONObject draft=value.optJSONObject("draft");
   boolean hasDraft=draft!=null&&((draft.opt("text") instanceof String&&!draft.optString("text").isEmpty())
     ||hasComposerItems(draft,"quotes")||hasComposerItems(draft,"attachments")||hasComposerItems(draft,"references")
     ||hasComposerItems(draft,"attachmentDrafts")||hasComposerItems(draft,"skills")
     ||"plan".equals(draft.optString("workMode"))||"goal".equals(draft.optString("workMode")));
   String title=value.opt("title") instanceof String?value.optString("title"):"";
   if(title.length()>512)title=title.substring(0,title.offsetByCodePoints(0,Math.min(512,title.codePointCount(0,title.length()))));
   db.execSQL("INSERT OR REPLACE INTO composer(account,session,revision,content,pending,active,title,changed) VALUES(?,?,?,?,?,?,?,?)",
     new Object[]{account.id,session,revision+1,encoded,pending?1:0,!session.isEmpty()&&(pending||hasDraft)?1:0,title,System.currentTimeMillis()});
   db.setTransactionSuccessful();
   return new JSONObject().put("revision",revision+1).put("value",value).put("applied",true);
  } finally { db.endTransaction(); }
 }
 private static boolean hasComposerItems(JSONObject value,String key) {
  JSONArray items=value.optJSONArray(key);
  return items!=null&&items.length()>0;
 }
 private static void createRecords(SQLiteDatabase db) {
  db.execSQL("CREATE INDEX IF NOT EXISTS turns_recent ON turns(account,session,suppressed,asked DESC,id DESC);");
  db.execSQL("CREATE TABLE IF NOT EXISTS archive_records(account INTEGER NOT NULL,session TEXT NOT NULL,key TEXT NOT NULL,turn TEXT NOT NULL,asked INTEGER NOT NULL,position REAL NOT NULL,visible INTEGER NOT NULL,version TEXT NOT NULL,synced INTEGER NOT NULL,content TEXT NOT NULL,bytes INTEGER NOT NULL,PRIMARY KEY(account,session,key),FOREIGN KEY(account,session,turn) REFERENCES turns(account,session,id) ON DELETE CASCADE);");
  db.execSQL("CREATE INDEX IF NOT EXISTS archive_recent ON archive_records(account,session,visible,asked DESC,turn DESC,position DESC,key DESC);");
  db.execSQL("CREATE INDEX IF NOT EXISTS archive_process ON archive_records(account,session,turn,position,key);");
  db.execSQL("CREATE INDEX IF NOT EXISTS turns_legacy ON turns(account,asked DESC,id DESC) WHERE suppressed=0 AND content IS NOT NULL AND content<>'';");
  db.execSQL("CREATE INDEX IF NOT EXISTS turns_legacy_session ON turns(account,session) WHERE suppressed=0 AND content IS NOT NULL AND content<>'';");
  db.execSQL("CREATE TABLE IF NOT EXISTS archive_deleted(account INTEGER NOT NULL,session TEXT NOT NULL,key TEXT NOT NULL,synced INTEGER NOT NULL,PRIMARY KEY(account,session,key),FOREIGN KEY(account,session) REFERENCES sessions(account,id) ON DELETE CASCADE);");
  db.execSQL("CREATE TABLE IF NOT EXISTS archive_parts(account INTEGER NOT NULL,session TEXT NOT NULL,key TEXT NOT NULL,offset INTEGER NOT NULL,version TEXT NOT NULL,content TEXT NOT NULL,PRIMARY KEY(account,session,key,offset),FOREIGN KEY(account,session,key) REFERENCES archive_records(account,session,key) ON DELETE CASCADE);");
 }
 private static double order(JSONObject message) {
  return message.has("order")?message.optDouble("order"):message.optJSONObject("row")==null?0:message.optJSONObject("row").optDouble("order",0);
 }
 private void storeRecord(SQLiteDatabase db,Account account,String session,String turn,long asked,JSONObject original,long synced)throws JSONException {
  String key=original.getString("key"),version=original.getString("version");
  if(version.isEmpty())throw new JSONException("Missing record version");
  try(Cursor deleted=db.rawQuery("SELECT synced FROM archive_deleted WHERE account=? AND session=? AND key=?",new String[]{""+account.id,session,key})){
   if(deleted.moveToFirst()){
    if(synced<=deleted.getLong(0))return;
    db.execSQL("DELETE FROM archive_deleted WHERE account=? AND session=? AND key=?",new Object[]{account.id,session,key});
   }
  }
  String priorVersion=null;long priorSynced=Long.MIN_VALUE;
  try(Cursor previous=db.rawQuery("SELECT version,synced FROM archive_records WHERE account=? AND session=? AND key=?",new String[]{""+account.id,session,key})){
   if(previous.moveToFirst()){priorVersion=previous.getString(0);priorSynced=previous.getLong(1);}
  }
  JSONObject message=new JSONObject(original.toString()),row=message.optJSONObject("row");
  if(row==null)row=new JSONObject();
  row.put("key",key).put("version",version).put("turnId",turn).put("role",message.optString("role",row.optString("role","assistant")));
  double position=order(message);
  JSONObject parts=message.optJSONObject("bodyParts");message.remove("bodyParts");
  if(parts==null)parts=new JSONObject();
  JSONObject part=message.optJSONObject("bodyPart");message.remove("bodyPart");
  if(part!=null)parts.put(Long.toString(part.getLong("offset")),part);
  String text=message.optString("text",row.optString("text"));
  int scalarCount=text.codePointCount(0,text.length());
  if(scalarCount>6000){
   if(parts.length()==0&&!row.optBoolean("truncated")){
    int start=0,offset=0,remaining=scalarCount;
    while(start<text.length()){
     int count=Math.min(16000,remaining),end=text.offsetByCodePoints(start,count);
     parts.put(""+offset,new JSONObject().put("offset",offset).put("text",text.substring(start,end)).put("next",end==text.length()?JSONObject.NULL:offset+count));
     start=end;offset+=count;remaining-=count;
    }
   }
   row.put("truncated",true);
  }
  int end=text.offsetByCodePoints(0,Math.min(6000,text.codePointCount(0,text.length())));
  String preview=text.substring(0,end);
  row.put("text",preview);message.put("text",preview).put("row",row);message.remove("_syncedAt");
  if(priorVersion==null||synced>=priorSynced){
   if(!version.equals(priorVersion))db.execSQL("DELETE FROM archive_parts WHERE account=? AND session=? AND key=?",new Object[]{account.id,session,key});
   String role=row.optString("role");
   boolean process=row.optString("kind").equals("processPage")||row.optString("phase").equals("commentary")||row.optString("interaction").equals("response")||
      !java.util.Arrays.asList("assistant","user","error","fileChanges").contains(role);
   int visible=row.optString("kind").equals("processPage")||!process?1:0;
   if(visible==0){
    String anchor="start";boolean needs=true;
    try(Cursor prior=db.rawQuery("SELECT key,visible,content FROM archive_records WHERE account=? AND session=? AND turn=? AND position<? ORDER BY position DESC,key DESC LIMIT 1",new String[]{""+account.id,session,turn,""+position})){
     if(prior.moveToFirst()){anchor=prior.getString(0);needs=prior.getInt(1)==1&&!new JSONObject(prior.getString(2)).getJSONObject("row").optString("kind").equals("processPage");}
    }
    if(needs){
     String header="offline-process:"+turn+":"+anchor;
     JSONObject headerRow=new JSONObject(row.toString()).put("key",header).put("kind","processPage").put("role","activity").put("text","").put("order",position-.25);
     storeRecord(db,account,session,turn,asked,new JSONObject().put("key",header).put("version",version).put("role","activity").put("order",position-.25).put("text","").put("row",headerRow),synced);
    }
   }
   String content=message.toString();int bytes=content.getBytes(StandardCharsets.UTF_8).length;
   db.execSQL("INSERT OR IGNORE INTO archive_records(account,session,key,turn,asked,position,visible,version,synced,content,bytes) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
      new Object[]{account.id,session,key,turn,asked,position,visible,version,synced,content,bytes});
   db.execSQL("UPDATE archive_records SET turn=?,asked=?,position=?,visible=?,version=?,synced=?,content=?,bytes=? WHERE account=? AND session=? AND key=?",
      new Object[]{turn,asked,position,visible,version,synced,content,bytes,account.id,session,key});
  }else if(!version.equals(priorVersion))return;
  for(java.util.Iterator<String> offsets=parts.keys();offsets.hasNext();){
   String offset=offsets.next();long positionOffset=Long.parseLong(offset);
   if(positionOffset<0)throw new JSONException("Invalid body offset");
   String operation=priorVersion!=null&&synced<priorSynced?"INSERT OR IGNORE":"INSERT OR REPLACE";
   JSONObject bodyPart=parts.getJSONObject(offset);String body=bodyPart.getString("text");
   int start=0,scalarOffset=0,remaining=body.codePointCount(0,body.length());
   do{
    int count=Math.min(16000,remaining),bodyEnd=body.offsetByCodePoints(start,count);
    JSONObject piece=new JSONObject(bodyPart.toString()).put("offset",positionOffset+scalarOffset).put("text",body.substring(start,bodyEnd))
      .put("next",bodyEnd==body.length()?bodyPart.opt("next"):positionOffset+scalarOffset+count);
    db.execSQL(operation+" INTO archive_parts(account,session,key,offset,version,content) VALUES(?,?,?,?,?,?)",new Object[]{account.id,session,key,positionOffset+scalarOffset,version,piece.toString()});
    start=bodyEnd;scalarOffset+=count;remaining-=count;
   }while(start<body.length());
  }
 }
 private JSONObject storedRecord(Account account,String session,String key,boolean parts)throws JSONException {
  SQLiteDatabase db=getReadableDatabase();JSONObject message;
  try(Cursor row=db.rawQuery("SELECT r.content FROM archive_records r JOIN sessions s ON s.account=r.account AND s.id=r.session JOIN turns t ON t.account=r.account AND t.session=r.session AND t.id=r.turn WHERE r.account=? AND r.session=? AND r.key=? AND s.epoch=? AND t.suppressed=0",new String[]{""+account.id,session,key,""+account.epoch})){
   if(!row.moveToFirst())return null;message=new JSONObject(row.getString(0));
  }
  if(parts){
   JSONObject values=new JSONObject();
   try(Cursor rows=db.rawQuery("SELECT offset,content FROM archive_parts WHERE account=? AND session=? AND key=? ORDER BY offset",new String[]{""+account.id,session,key})){
    while(rows.moveToNext())values.put(rows.getString(0),new JSONObject(rows.getString(1)));
   }
   if(values.length()>0)message.put("bodyParts",values);
  }
  return message;
 }
 boolean migrateBatch(Account account,String focus)throws Exception {
  if(account==null||locked(account))return false;
  SQLiteDatabase db=getWritableDatabase();String session,id;long asked;int length;
  try(Cursor row=db.rawQuery("SELECT session,id,asked,length(content) FROM turns WHERE account=? AND suppressed=0 AND content IS NOT NULL AND content<>''"+(focus==null?"":" AND session=?")+" ORDER BY asked DESC,id DESC LIMIT 1",focus==null?new String[]{""+account.id}:new String[]{""+account.id,focus})){
   if(!row.moveToFirst())return false;session=row.getString(0);id=row.getString(1);asked=row.getLong(2);length=row.getInt(3);
  }
  // Only legacy conversion reads a whole turn; foreground queries never enter this path.
  JSONObject legacy=readTurnContent(db,""+account.id,session,id,length),messages=legacy.optJSONObject("messages");
  db.beginTransaction();
  try{
   if(messages!=null){
    java.util.List<JSONObject> ordered=new java.util.ArrayList<>();
    for(java.util.Iterator<String> keys=messages.keys();keys.hasNext();)ordered.add(messages.getJSONObject(keys.next()));
    ordered.sort((a,b)->Double.compare(order(a),order(b)));
    for(JSONObject message:ordered)storeRecord(db,account,session,id,asked,message,message.optLong("_syncedAt",0));
   }else for(String field:new String[]{"question","answer"}){
    String text=legacy.optString(field);
    if(!text.isEmpty()){
     String key=id+":"+field,role=field.equals("question")?"user":"assistant";
     storeRecord(db,account,session,id,asked,new JSONObject().put("key",key).put("version",ConnectionDiscovery.hash(text)).put("role",role).put("text",text).put("order",field.equals("question")?0:1).put("row",new JSONObject().put("key",key).put("role",role).put("text",text)),0);
    }
   }
   db.execSQL("UPDATE turns SET content=NULL WHERE account=? AND session=? AND id=?",new Object[]{account.id,session,id});
   db.setTransactionSuccessful();
  }finally{db.endTransaction();}
  return true;
 }
 JSONObject recordPage(Account account,String session,JSONObject event)throws JSONException {
  if(account==null||locked(account))return null;
  SQLiteDatabase db=getReadableDatabase();String title;long synced;boolean complete;
  try(Cursor row=db.rawQuery("SELECT title,synced,complete FROM sessions WHERE account=? AND id=? AND epoch=?",new String[]{""+account.id,session,""+account.epoch})){
   if(row.moveToFirst()){title=row.getString(0);synced=row.getLong(1);complete=row.getInt(2)!=0;}
   else try(Cursor local=db.rawQuery("SELECT title,changed FROM composer WHERE account=? AND session=? AND active=1",new String[]{""+account.id,session})){
    if(!local.moveToFirst())return null;
    title=local.getString(0);synced=local.getLong(1);complete=false;
   }
  }
  String mode=event.optString("mode","recent"),filter=" AND r.visible=1",sort="r.asked DESC,r.turn DESC,r.position DESC,r.key DESC";
  if(mode.equals("body"))return body(account,new JSONObject().put("sessionId",session).put("body",event));
  List<String> values=new ArrayList<>(java.util.Arrays.asList(""+account.id,session,""+account.epoch));
  if(mode.equals("process")){
   String turn;double position;
   try(Cursor row=db.rawQuery("SELECT turn,position FROM archive_records WHERE account=? AND session=? AND key=? AND visible=1",new String[]{""+account.id,session,event.getString("key")})){
    if(!row.moveToFirst())return null;turn=row.getString(0);position=row.getDouble(1);
   }
   double end=Double.MAX_VALUE;
   try(Cursor row=db.rawQuery("SELECT position FROM archive_records WHERE account=? AND session=? AND turn=? AND visible=1 AND position>? ORDER BY position,key LIMIT 1",new String[]{""+account.id,session,turn,""+position})){if(row.moveToFirst())end=row.getDouble(0);}
   filter=" AND r.turn=? AND r.visible=0 AND r.position>? AND r.position<?";sort="r.position,r.key";values.add(turn);values.add(""+position);values.add(""+end);
  }
  String cursor=event.optString(mode.equals("process")?"after":"before","");
  if(!cursor.isEmpty()&&!cursor.equals("null")){
   JSONArray p=new JSONArray(new String(Base64.decode(cursor,Base64.DEFAULT),StandardCharsets.UTF_8));
   if(p.length()!=7||!p.getString(0).equals(session)||!p.getString(1).equals(""+account.clearRevision)||!p.getString(2).equals(""+account.epoch))throw new JSONException("Invalid record cursor");
   if(mode.equals("process")){filter+=" AND (r.position,r.key)>(?,?)";values.add(p.getString(5));values.add(p.getString(6));}
   else{filter+=" AND (r.asked,r.turn,r.position,r.key)<(?,?,?,?)";for(int i=3;i<7;i++)values.add(p.getString(i));}
  }
  List<JSONObject> selected=new ArrayList<>();int bytes=0;boolean more=false;
  try(Cursor rows=db.rawQuery("SELECT r.key,r.turn,r.asked,r.position,r.bytes FROM archive_records r JOIN sessions s ON s.account=r.account AND s.id=r.session JOIN turns t ON t.account=r.account AND t.session=r.session AND t.id=r.turn WHERE r.account=? AND r.session=? AND s.epoch=? AND t.suppressed=0"+filter+" ORDER BY "+sort+" LIMIT 41",values.toArray(new String[0]))){
   while(rows.moveToNext()){
    if(selected.size()>=40||!selected.isEmpty()&&bytes+rows.getInt(4)>192*1024){more=true;break;}
    bytes+=rows.getInt(4);selected.add(new JSONObject().put("key",rows.getString(0)).put("turn",rows.getString(1)).put("asked",rows.getLong(2)).put("position",rows.getDouble(3)));
   }
  }
  Object next=JSONObject.NULL;
  if(more&&!selected.isEmpty()){
   JSONObject tail=selected.get(selected.size()-1);
   next=Base64.encodeToString(new JSONArray().put(session).put(""+account.clearRevision).put(""+account.epoch).put(String.valueOf(tail.get("asked"))).put(tail.getString("turn")).put(String.valueOf(tail.get("position"))).put(tail.getString("key")).toString().getBytes(StandardCharsets.UTF_8),Base64.NO_WRAP);
  }
  if(!mode.equals("process"))java.util.Collections.reverse(selected);
  JSONArray result=new JSONArray();JSONObject turns=new JSONObject();
  int index=0;
  for(JSONObject item:selected){
   JSONObject message=storedRecord(account,session,item.getString("key"),false);
   if(message==null)continue;
   JSONObject row=message.getJSONObject("row").put("key",message.getString("key")).put("version",message.getString("version")).put("text",message.optString("text")).put("order",index++);
   String turn=row.getString("turnId");JSONObject meta=turns.optJSONObject(turn);
   if(meta==null){meta=new JSONObject().put("id",turn).put("processes",new JSONArray());turns.put(turn,meta);}
   String[][] fields={{"turnStatus","status"},{"turnCompletionKnown","completionKnown"},{"turnStartedAt","startedAt"},{"turnCompletedAt","completedAt"},{"turnError","error"}};
   for(String[] field:fields)if(row.has(field[0]))meta.put(field[1],row.get(field[0]));
   if(row.optString("kind").equals("processPage")&&!mode.equals("process"))meta.getJSONArray("processes").put(new JSONObject().put("key",row.getString("key")).put("version",row.getString("version")).put("order",row.getInt("order")).put("processCursor",row.getString("key")));
   else result.put(row);
  }
  JSONArray metas=new JSONArray();for(java.util.Iterator<String> keys=turns.keys();keys.hasNext();)metas.put(turns.getJSONObject(keys.next()));
  boolean legacy;
  try(Cursor row=db.rawQuery("SELECT 1 FROM turns WHERE account=? AND session=? AND suppressed=0 AND content IS NOT NULL AND content<>'' LIMIT 1",new String[]{""+account.id,session})){legacy=row.moveToFirst();}
  return new JSONObject().put("recordPage",1).put("id",session).put("title",title).put("syncedAt",synced).put("gaps",!complete||hasGaps(account,session))
    .put("rows",result).put("index",result).put("turns",metas).put("before",mode.equals("process")?JSONObject.NULL:next).put("next",mode.equals("process")?next:JSONObject.NULL).put("hasMore",more).put("migrating",legacy).put("readRecords",selected.size());
 }
 private JSONObject turnContent(Account account,String session,String turn)throws JSONException {
  JSONObject messages=new JSONObject();
  try(Cursor rows=getReadableDatabase().rawQuery("SELECT key FROM archive_records WHERE account=? AND session=? AND turn=? ORDER BY position,key",new String[]{""+account.id,session,turn})){
   while(rows.moveToNext()){String key=rows.getString(0);JSONObject value=storedRecord(account,session,key,true);if(value!=null)messages.put(key,value);}
  }
  return new JSONObject().put("messages",messages);
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
    db.execSQL("DELETE FROM composer WHERE account=?",new Object[]{current.id});
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
  String stored="EXISTS(SELECT 1 FROM turns t WHERE t.account=a.id AND t.suppressed=0) OR EXISTS(SELECT 1 FROM composer c WHERE c.account=a.id AND c.active=1)";
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
    try(Cursor suppression=db.rawQuery("SELECT suppressed FROM turns WHERE account=? AND session=? AND id=?",new String[]{""+account.id,session,id})){
     if(suppression.moveToFirst()&&suppression.getInt(0)!=0&&!canRestore)continue;
    }
    db.execSQL("INSERT OR IGNORE INTO turns(account,session,id,asked,content,suppressed) VALUES(?,?,?,?,NULL,0)",new Object[]{account.id,session,id,asked});
    db.execSQL("UPDATE turns SET asked=CASE WHEN asked=0 THEN ? ELSE asked END,suppressed=0 WHERE account=? AND session=? AND id=?",new Object[]{asked,account.id,session,id});
    try(Cursor current=db.rawQuery("SELECT asked FROM turns WHERE account=? AND session=? AND id=?",new String[]{""+account.id,session,id})){if(current.moveToFirst())asked=current.getLong(0);}
    db.execSQL("UPDATE archive_records SET asked=? WHERE account=? AND session=? AND turn=? AND asked=0",new Object[]{asked,account.id,session,id});
    JSONArray messages=incoming.optJSONArray("messages");
    if(messages!=null){
     List<JSONObject> ordered=new ArrayList<>();for(int j=0;j<messages.length();j++)ordered.add(messages.getJSONObject(j));
     ordered.sort((a,b)->Double.compare(order(a),order(b)));
     for(JSONObject message:ordered)storeRecord(db,account,session,id,asked,message,synced);
    }
   }
   JSONArray deleted=event.optJSONArray("deletedKeys");
   if(deleted!=null)for(int i=0;i<deleted.length();i++){
    String key=deleted.getString(i);db.execSQL("DELETE FROM archive_records WHERE account=? AND session=? AND key=?",new Object[]{account.id,session,key});
    db.execSQL("INSERT OR IGNORE INTO archive_deleted VALUES(?,?,?,?)",new Object[]{account.id,session,key,synced});
    db.execSQL("UPDATE archive_deleted SET synced=MAX(synced,?) WHERE account=? AND session=? AND key=?",new Object[]{synced,account.id,session,key});
   }
   db.setTransactionSuccessful();
  } finally { db.endTransaction(); }
 }
 List<Session> sessions(Account account) {
  List<Session> values=new ArrayList<>();
  if(account==null||locked(account))return values;
  try(Cursor row=getReadableDatabase().rawQuery("SELECT s.id,s.title,s.synced,s.complete FROM sessions s WHERE s.account=? AND s.epoch=? "+
     "AND EXISTS(SELECT 1 FROM turns t WHERE t.account=s.account AND t.session=s.id AND t.suppressed=0) "+
     "UNION ALL SELECT c.session,c.title,c.changed,0 FROM composer c WHERE c.account=? AND c.active=1 "+
     "AND NOT EXISTS(SELECT 1 FROM sessions s JOIN turns t ON s.account=t.account AND s.id=t.session "+
     "WHERE s.account=c.account AND s.id=c.session AND s.epoch=? AND t.suppressed=0) ORDER BY 3 DESC,1",
      new String[]{""+account.id,""+account.epoch,""+account.id,""+account.epoch})) {
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
    JSONObject content=turnContent(account,session,id);
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
  String key=wanted.getString("key"),version=wanted.getString("version"),session=command.getString("sessionId");
  JSONObject message=storedRecord(account,session,key,false);
  if(message==null||!version.equals(message.optString("version")))return null;
  try(Cursor row=getReadableDatabase().rawQuery("SELECT content FROM archive_parts WHERE account=? AND session=? AND key=? AND offset=? AND version=?",new String[]{""+account.id,session,key,""+wanted.getLong("offset"),version})){
   return row.moveToFirst()?new JSONObject(row.getString(0)).put("key",key).put("version",version):null;
  }
 }
 JSONArray seed(Account account,JSONObject command)throws JSONException {
  JSONArray result=new JSONArray();if(account==null||!"valid".equals(account.status))return result;
  String session=command.getString("sessionId");JSONArray keys=command.optJSONArray("keys");
  if(keys==null||keys.length()==0){
   keys=new JSONArray();
   try(Cursor rows=getReadableDatabase().rawQuery("SELECT key FROM archive_records WHERE account=? AND session=? ORDER BY asked DESC,turn DESC,position DESC,key DESC LIMIT 40",new String[]{""+account.id,session})){while(rows.moveToNext())keys.put(rows.getString(0));}
  }
  JSONObject groups=new JSONObject();int bytes=0;
  Set<String> ids=stringSet(command.optJSONArray("turnIds"));
  for(int i=0;i<Math.min(100,keys.length());i++){
   String key=keys.getString(i);JSONObject message=storedRecord(account,session,key,false);if(message==null)continue;
   String turn;long asked;
   try(Cursor row=getReadableDatabase().rawQuery("SELECT turn,asked FROM archive_records WHERE account=? AND session=? AND key=?",new String[]{""+account.id,session,key})){if(!row.moveToFirst())continue;turn=row.getString(0);asked=row.getLong(1);}
   if(!ids.isEmpty()&&!ids.contains(turn))continue;
   JSONArray parts=new JSONArray();
   try(Cursor rows=getReadableDatabase().rawQuery("SELECT content FROM archive_parts WHERE account=? AND session=? AND key=? ORDER BY offset LIMIT 2",new String[]{""+account.id,session,key})){while(rows.moveToNext())parts.put(new JSONObject(rows.getString(0)));}
   if(parts.length()>0)message.put("bodyParts",parts);
   int size=message.toString().getBytes(StandardCharsets.UTF_8).length;if(bytes+size>512_000)break;bytes+=size;
   JSONObject group=groups.optJSONObject(turn);if(group==null){group=new JSONObject().put("id",turn).put("questionTime",asked).put("messages",new JSONArray());groups.put(turn,group);}
   group.getJSONArray("messages").put(message);
  }
  for(java.util.Iterator<String> names=groups.keys();names.hasNext();)result.put(groups.getJSONObject(names.next()));
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
  try(Cursor row=getReadableDatabase().rawQuery("SELECT 1 FROM turns WHERE account=? AND suppressed=0 UNION ALL SELECT 1 FROM composer WHERE account=? AND active=1 LIMIT 1",new String[]{""+account.id,""+account.id})){return row.moveToFirst();}
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
  db.execSQL("DELETE FROM archive_records WHERE account=? AND (session,turn) IN (SELECT session,id FROM turns WHERE account=? AND asked>=? AND asked<?)",new Object[]{id,id,start,end});
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
   JSONArray records=event.optJSONArray("records");
   if(records==null)records=new JSONArray().put(new JSONObject().put("key",event.getString("key")).put("recordVersion",event.getString("recordVersion")).put("textLength",event.optLong("textLength",-1)));
   if(records.length()<1||records.length()>64)throw new JSONException("Invalid transfer record batch");
   present=true;
   for(int i=0;i<records.length();i++){
    JSONObject record=records.getJSONObject(i),message=storedRecord(account,item.session,record.getString("key"),true);
    if(message==null||!record.getString("recordVersion").equals(message.optString("version"))||!completeMessage(message,record.optLong("textLength",-1))){present=false;break;}
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
