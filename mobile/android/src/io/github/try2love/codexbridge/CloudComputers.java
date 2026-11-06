package io.github.try2love.codexbridge;

import android.app.AlertDialog;
import android.text.InputType;
import android.widget.*;
import java.util.concurrent.*;
import java.util.function.Consumer;
import org.json.*;

/** Existing computer list owns these rows. No poll, receiver or native library at startup without enrollment. */
final class CloudComputers {
    private final MainActivity app;
    private static final ExecutorService io=Executors.newSingleThreadExecutor();
    private static CloudNative nativeClient;
    private LinearLayout rows;
    private JSONObject lastDirectory;
    private boolean directoryPending,opening;
    private JSONArray profiles;
    private CloudServicePage page;
    private static final String LAST="last-cloud-view";
    private int generation;
    CloudComputers(MainActivity app){this.app=app;}
    boolean enabled(){return app.prefs.getBoolean("cloud-account-enabled",false);}
    JSONObject command(String op,String... kv){
        JSONObject value=new JSONObject();try{value.put("op",op);for(int i=0;i<kv.length;i+=2)value.put(kv[i],kv[i+1]);}catch(JSONException e){throw new IllegalArgumentException(e);}return value;
    }
    private void call(JSONObject cmd,Consumer<JSONObject> done){call(cmd,done,app::message);}
    private void call(JSONObject cmd,Consumer<JSONObject> done,Consumer<String> failed){
        int current=generation;
        io.execute(()->{
            try{
                if(nativeClient==null)nativeClient=new CloudNative(app.getApplicationContext());
                JSONObject value=nativeClient.run(cmd);
                app.runOnUiThread(()->{if(generation==current&&!app.isDestroyed())done.accept(value);});
            }catch(Exception|LinkageError e){
                app.runOnUiThread(()->{if(generation==current&&!app.isDestroyed())failed.accept(app.L(e.getMessage()==null?"云服务不可用":e.getMessage()));});
            }
        });
    }
    void closeView(){io.execute(()->{try{if(nativeClient!=null)nativeClient.run(command("close"));}catch(Exception ignored){}});}
    void leave(){
        generation++;rows=null;directoryPending=false;
        if(opening){opening=false;closeView();}
        if(page!=null){page.dismiss();page=null;}
    }
    void show(LinearLayout parent){
        rows=app.column();app.add(rows,parent,0);
        if(enabled())refresh();
    }
    String profileTitle(JSONObject p){
        String kind="custom".equals(p.optString("serverType"))?p.optString("customOrigin").replaceFirst("^https://",""):app.L("官方云服务");
        return kind+" · "+p.optString("login");
    }
    private void showPage(String selected){
        if(page!=null)page.dismiss();
        CloudServicePage next=new CloudServicePage(app,this);page=next;
        next.list(!enabled()?new JSONArray():profiles);next.show();
        if(profiles!=null && selected!=null)next.update(profiles,selected);
        if(!enabled()){next.edit(null);return;}
        if(profiles!=null)return;
        call(command("profiles"),v->{
            profiles=v.optJSONArray("profiles");
            if(page==next&&next.isShowing()&&profiles!=null)next.update(profiles,selected);
        });
    }
    void account(){showPage(null);}
    void login(JSONObject cmd,Runnable finished){
        call(cmd,v->{
            String old=cmd.optString("replaceProfile");
            if(!old.isEmpty()&&!old.equals(v.optString("profile")))forgetRoute(old);
            lastDirectory=null;profiles=null;app.prefs.edit().putBoolean("cloud-account-enabled",true).apply();
            finished.run();app.home();
            if(!v.optBoolean("remoteConfirmed",true))app.message("本机配置已更新，远端退出暂未确认。");
        },error->{finished.run();app.message(error);});
    }
    void confirm(JSONObject p,boolean remove){
        JSONArray devices=p.optJSONArray("computers");StringBuilder affected=new StringBuilder(profileTitle(p)).append("\n");
        affected.append(app.L(remove?"从本机清除此配置、设备别名和配对密钥。":"退出此账号下所有电脑，保留配置和设备别名。"));
        if(devices!=null)for(int i=0;i<devices.length();i++)affected.append("\n").append(devices.optString(i));
        String title=app.L(remove?"删除云服务配置":"退出云账号");
        new AlertDialog.Builder(app).setTitle(title).setMessage(affected.toString()).setNegativeButton(app.L("取消"),null)
            .setPositiveButton(title,(d,w)->call(command(remove?"remove":"logout","profile",p.optString("id")),v->{
                lastDirectory=null;profiles=v.optJSONArray("profiles");forgetRoute(p.optString("id"));app.prefs.edit().putBoolean("cloud-account-enabled",v.optJSONArray("profiles").length()>0).apply();app.home();
                if(!v.optBoolean("remoteConfirmed"))app.message("本机配置已更新，远端退出暂未确认。");
            })).show();
    }
    private void rename(String profile,String gateway,String name){
        EditText entry=new EditText(app);entry.setText(name);entry.setSingleLine(true);entry.selectAll();
        new AlertDialog.Builder(app).setTitle(app.L("重命名")).setView(entry).setNegativeButton(app.L("取消"),null)
            .setPositiveButton(app.L("保存"),(d,w)->call(command("rename","profile",profile,"gateway",gateway,"name",entry.getText().toString()),v->{
                if(lastDirectory==null)return;
                JSONArray devices=lastDirectory.optJSONArray("devices");
                for(int i=0;i<devices.length();i++){
                    JSONObject row=devices.optJSONObject(i);
                    if(profile.equals(row.optString("profile"))&&gateway.equals(row.optJSONObject("gateway").optString("id"))){
                        try{row.put("name",v.getString("name"));}catch(JSONException e){throw new AssertionError(e);}
                        render(lastDirectory);break;
                    }
                }
            })).show();
    }
    void refresh(){
        if(!enabled()||rows==null)return;
        if(lastDirectory!=null)render(lastDirectory);
        if(directoryPending)return;
        directoryPending=true;
        call(command("devices","name","Android Bridge Test"),value->{directoryPending=false;render(value);},
             error->{directoryPending=false;if(rows!=null)app.add(app.text(error,14),rows,4);});
    }
    private void render(JSONObject v){
        if(rows==null)return;
        lastDirectory=v;if(v.has("profiles"))profiles=v.optJSONArray("profiles");
            rows.removeAllViews();JSONArray devices=v.optJSONArray("devices");if(devices==null)return;
            for(int i=0;i<devices.length();i++){
                JSONObject device=devices.optJSONObject(i),gateway=device.optJSONObject("gateway");
                String id=device.optString("binding_id"),profile=device.optString("profile"),name=device.optString("name"),status=device.optString("status");
                boolean online=gateway.optBoolean("online");
                LinearLayout card=app.card();
                boolean selectable=online&&(status.equals("active")||status.equals("pending"));
                TextView title=app.computerTitle(name);title.setEnabled(selectable);app.add(title,card,4);
                String kind="custom".equals(device.optString("sourceKind"))?device.optString("customOrigin").replaceFirst("^https://",""):app.L("官方云服务");
                String state=app.L(!online?"离线":status.equals("active")?"已允许连接":status.equals("pending")?"待电脑允许":"已停用");
                app.add(app.text(kind+" · "+device.optString("sourceLogin")+" · "+state,14),card,4);
                if(status.equals("pending")&&!device.optString("safety").isEmpty())app.add(app.text(device.optString("safety"),13),card,4);
                if(device.has("proofError"))app.add(app.text(app.L(device.optString("proofError")),13),card,4);
                app.computerActions(card,"管理云服务",()->rename(profile,gateway.optString("id"),name),()->showPage(profile));
                if(selectable){card.setOnClickListener(t->open(profile,id,name));card.setContentDescription(app.L("连接 ")+name);}
                app.add(card,rows,12);
            }
            JSONArray errors=v.optJSONArray("errors");
            if(errors!=null)for(int i=0;i<errors.length();i++)app.add(app.text(errors.optString(i),14),rows,4);
    }
    boolean restore(){
        String raw=app.prefs.getString(LAST,null);if(raw==null)return false;
        if("home".equals(raw)){app.home();return true;}
        try{
            JSONObject route=new JSONObject(raw);
            if(!enabled())throw new JSONException("signed out");
            open(route.getString("profile"),route.getString("binding"),route.getString("name"));
        }catch(JSONException unavailable){
            forgetRoute(null);app.home();app.message("上次使用的云配置不可用");
        }
        return true;
    }
    void forgetRoute(String profile){
        if(profile!=null){
            try{if(!profile.equals(new JSONObject(app.prefs.getString(LAST,"{}")).optString("profile")))return;}
            catch(JSONException invalid){/* Discard an incomplete previous selection. */}
        }
        rememberHome();
    }
    void rememberHome(){app.prefs.edit().putString(LAST,"home").apply();}
    void selectDirect(){app.prefs.edit().remove(LAST).apply();}
    boolean isOpening(){return opening;}
    private void open(String profile,String id,String name){
        if(opening)return;
        leave();opening=true;app.cloudProgress(name);
        call(command("open","profile",profile,"binding",id,"name","Android Bridge Test"),v->{
            opening=false;
            if(v.has("entry")){
                String resolved=v.isNull("name")?name:v.optString("name");
                JSONObject route=new JSONObject();
                try{route.put("profile",profile).put("binding",id).put("name",resolved);}catch(JSONException e){throw new AssertionError(e);}
                app.prefs.edit().putString(LAST,route.toString()).remove("active").apply();
                app.connect(v.optString("entry"),resolved);
            }else{
                app.home();
                app.message(app.L("在电脑核对安全码并允许连接。")+"\n"+v.optString("safety"));
            }
        },error->{opening=false;app.home();app.message(error);});
    }
}
