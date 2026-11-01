package io.github.abyxs.codexmobile;

import android.content.Context;
import android.content.SharedPreferences;
import android.net.Uri;
import android.os.Handler;
import android.os.Looper;
import org.json.JSONArray;
import org.json.JSONObject;
import java.io.File;
import java.io.InputStream;
import java.io.RandomAccessFile;
import java.net.HttpURLConnection;
import java.net.URL;
import java.util.HashMap;
import java.util.Map;
import java.util.TreeSet;
import java.util.UUID;

/** Foreground downloads with persistent partial files and explicit continuation. */
final class AppDownloads {
    private final SharedPreferences prefs;
    private final File directory;
    private final Handler main = new Handler(Looper.getMainLooper());
    private final Runnable changed;
    private final Context appContext;
    private final JSONArray records;
    private final Map<String, Job> jobs = new HashMap<>();
    private boolean foreground = true;
    private static final class Job { volatile boolean stopped; volatile HttpURLConnection connection; }

    AppDownloads(Context context, Runnable changed) {
        appContext=context.getApplicationContext();
        prefs = context.getSharedPreferences("app_downloads", Context.MODE_PRIVATE);
        directory = new File(context.getFilesDir(), "Downloads");
        this.changed = changed;
        JSONArray saved;
        try { saved = new JSONArray(prefs.getString("records", "[]")); } catch (Exception e) { saved = new JSONArray(); }
        records = saved;
        for (int i=0; i<records.length(); i++) {
            JSONObject row=records.optJSONObject(i);
            if (row!=null && ("running".equals(row.optString("state")) || "pausing".equals(row.optString("state")))) {
                put(row,"state","paused"); put(row,"message","下载已暂停，点击继续下载");
            }
        }
        persist();
    }
    private static void put(JSONObject row, String key, Object value) {
        try { row.put(key,value); } catch (org.json.JSONException e) { throw new IllegalStateException(e); }
    }
    static String origin(Uri uri) { return "https://"+uri.getHost().toLowerCase(java.util.Locale.ROOT)+":"+(uri.getPort()<0?443:uri.getPort()); }
    private static String key(String source) {
        Uri uri=Uri.parse(source); Uri.Builder out=uri.buildUpon().clearQuery().fragment(null);
        for(String name:new TreeSet<>(uri.getQueryParameterNames())) {
            java.util.List<String> values=new java.util.ArrayList<>(uri.getQueryParameters(name)); java.util.Collections.sort(values);
            for(String value:values) if(!("host".equals(name)&&"local".equals(value))) out.appendQueryParameter(name,value);
        }
        return out.build().toString();
    }
    private void persist() { prefs.edit().putString("records",records.toString()).apply(); main.post(changed); }
    private JSONObject row(String id, Uri origin) {
        for(int i=0;i<records.length();i++) { JSONObject r=records.optJSONObject(i); if(r!=null&&id.equals(r.optString("id"))&&origin(origin).equals(r.optString("origin"))) return r; }
        return null;
    }
    private File file(JSONObject row) { return new File(directory,row.optString("id")+".download"); }
    synchronized JSONArray rows(Uri origin) {
        JSONArray result=new JSONArray();
        for(int i=0;i<records.length();i++) { JSONObject row=records.optJSONObject(i); if(row!=null&&origin(origin).equals(row.optString("origin"))) result.put(row); }
        try { return new JSONArray(result.toString()); } catch(Exception e) { return new JSONArray(); }
    }
    synchronized String start(String source, String name, String cookie, String userAgent) {
        Uri uri=Uri.parse(source);
        if(!"https".equalsIgnoreCase(uri.getScheme())||uri.getHost()==null||uri.getUserInfo()!=null) throw new IllegalArgumentException();
        for(int i=records.length()-1;i>=0;i--) {
            JSONObject existing=records.optJSONObject(i);
            if(existing!=null&&origin(uri).equals(existing.optString("origin"))&&key(source).equals(key(existing.optString("sourceURL")))) {
                String id=existing.optString("id");
                resume(id,uri,cookie,userAgent,false); return id;
            }
        }
        JSONObject row=new JSONObject();String id="app-"+UUID.randomUUID();
        put(row,"id",id);put(row,"origin",origin(uri));put(row,"sourceURL",source);put(row,"name",name);
        put(row,"createdAt",System.currentTimeMillis());put(row,"state","paused");put(row,"percent",-1);
        records.put(row);resume(id,uri,cookie,userAgent,false);return id;
    }
    synchronized File readyFile(String id, Uri origin) {
        JSONObject row=row(id,origin);
        return row!=null&&"ready".equals(row.optString("state"))&&file(row).isFile()?file(row):null;
    }
    synchronized String name(String id, Uri origin) { JSONObject row=row(id,origin);return row==null?"下载文件":row.optString("name","下载文件"); }
    synchronized String source(String id, Uri origin) { JSONObject row=row(id,origin);return row==null?"":row.optString("sourceURL"); }
    synchronized void setForeground(boolean value) {
        foreground=value;
        if(!value) for(Map.Entry<String,Job> entry:new HashMap<>(jobs).entrySet()) {
            for(int i=0;i<records.length();i++) { JSONObject row=records.optJSONObject(i);if(row!=null&&entry.getKey().equals(row.optString("id"))) pause(entry.getKey(),Uri.parse(row.optString("sourceURL"))); }
        }
    }
    synchronized void pause(String id, Uri origin) {
        JSONObject row=row(id,origin);Job job=jobs.get(id);
        if(row==null||job==null||!"running".equals(row.optString("state")))return;
        job.stopped=true;put(row,"state","pausing");put(row,"message","正在保存下载进度…");persist();
        new Thread(()->{HttpURLConnection connection=job.connection;if(connection!=null)connection.disconnect();}).start();
    }
    synchronized void delete(String id, Uri origin) {
        if(jobs.containsKey(id))return;
        JSONObject row=row(id,origin);if(row==null)return;
        if(!"ready".equals(row.optString("state")))file(row).delete();
        for(int i=0;i<records.length();i++)if(records.optJSONObject(i)==row){records.remove(i);break;}
        persist();
    }
    synchronized void resume(String id, Uri origin, String cookie, String userAgent, boolean restart) {
        JSONObject row=row(id,origin);
        if(row==null||jobs.containsKey(id)||readyFile(id,origin)!=null){persist();return;}
        if(!foreground){put(row,"state","paused");put(row,"message","已暂停，回到 App 后点击继续下载");persist();return;}
        if(row.optBoolean("restartRequired")&&!restart){persist();return;}
        File file=file(row);
        if(restart){file.delete();row.remove("expected");row.remove("etag");row.remove("restartRequired");put(row,"percent",-1);}
        long offset=file.isFile()?file.length():0;
        if(offset>0&&offset==row.optLong("expected",-1)){put(row,"state","ready");put(row,"percent",100);put(row,"message","已下载到 App，点击保存文件");persist();return;}
        if(offset>0&&row.optString("etag").isEmpty()){put(row,"state","paused");put(row,"restartRequired",true);put(row,"message","无法核对续传文件，请点击重新下载");persist();return;}
        Job job=new Job();jobs.put(id,job);put(row,"state","running");put(row,"message",offset>0?"正在继续下载":"正在下载到 App");persist();
        String source=row.optString("sourceURL"),etag=row.optString("etag");
        new Thread(()->transfer(id,row,job,file,source,offset,etag,cookie,userAgent),"AppDownload").start();
    }
    private void transfer(String id,JSONObject row,Job job,File target,String source,long offset,String etag,String cookie,String userAgent) {
        HttpURLConnection connection=null;
        try {
            connection=(HttpURLConnection)new URL(source).openConnection();job.connection=connection;
            connection.setInstanceFollowRedirects(false);connection.setConnectTimeout(20000);connection.setReadTimeout(20000);
            connection.setRequestProperty("Accept-Encoding","identity");connection.setRequestProperty("User-Agent",userAgent);
            if(cookie!=null)connection.setRequestProperty("Cookie",cookie);
            if(offset>0){connection.setRequestProperty("Range","bytes="+offset+"-");connection.setRequestProperty("If-Range",etag);}
            if(job.stopped)return;
            int status=connection.getResponseCode();long total=connection.getContentLengthLong();
            if(offset>0){
                java.util.regex.Matcher range=java.util.regex.Pattern.compile("bytes (\\d+)-(\\d+)/(\\d+)").matcher(String.valueOf(connection.getHeaderField("Content-Range")));
                if(status!=206||!range.matches()||Long.parseLong(range.group(1))!=offset||Long.parseLong(range.group(2))+1!=Long.parseLong(range.group(3))||!etag.equals(connection.getHeaderField("ETag"))){
                    synchronized(this){put(row,"restartRequired",true);}throw new java.io.IOException("文件已变化或入口不支持续传，请点击重新下载");
                }
                total=Long.parseLong(range.group(3));
            }else if(status!=200)throw new java.io.IOException("下载失败，请检查登录状态和连接后点击继续下载");
            if(!directory.isDirectory()&&!directory.mkdirs())throw new java.io.IOException("无法创建 App 下载目录");
            synchronized(this){put(row,"expected",total);put(row,"etag",connection.getHeaderField("ETag"));persist();}
            try(InputStream input=connection.getInputStream();RandomAccessFile output=new RandomAccessFile(target,"rw")){
                if(offset==0)output.setLength(0);output.seek(offset);
                byte[] buffer=new byte[128*1024];long received=offset,lastNotice=0;int count;
                while(!job.stopped&&(count=input.read(buffer))!=-1){
                    synchronized(this){
                        if(job.stopped)break;
                        if(total>=0&&received+count>total){put(row,"restartRequired",true);throw new java.io.IOException("下载长度异常，请点击重新下载");}
                        output.write(buffer,0,count);received+=count;
                        put(row,"percent",total>0?Math.min(99,(int)(received*100/total)):-1);
                        put(row,"message","正在下载 · "+android.text.format.Formatter.formatFileSize(appContext,received));
                        if(System.currentTimeMillis()-lastNotice>300){main.post(changed);lastNotice=System.currentTimeMillis();}
                    }
                }
                output.getFD().sync();
                synchronized(this){
                    if(!job.stopped){
                        if(total>=0&&received!=total)throw new java.io.IOException("下载中断，点击继续下载");
                        put(row,"state","ready");put(row,"percent",100);put(row,"message","已下载到 App，点击保存文件选择保存位置");
                    }
                }
            }
        }catch(Exception error){synchronized(this){if(!job.stopped){put(row,"state","paused");put(row,"message",error.getMessage()==null?"下载已暂停，点击继续下载":error.getMessage());}}}
        finally{
            if(connection!=null)connection.disconnect();
            synchronized(this){if(jobs.get(id)==job){jobs.remove(id);if(job.stopped){put(row,"state","paused");put(row,"message","已暂停，点击继续下载");}persist();}}
        }
    }
}
