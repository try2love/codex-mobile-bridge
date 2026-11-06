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
    private boolean busy;
    private int generation;
    CloudComputers(MainActivity app){this.app=app;}
    boolean enabled(){return app.prefs.getBoolean("cloud-account-enabled",false);}
    private JSONObject command(String op,String... kv){
        JSONObject value=new JSONObject();try{value.put("op",op);for(int i=0;i<kv.length;i+=2)value.put(kv[i],kv[i+1]);}catch(JSONException e){throw new IllegalArgumentException(e);}return value;
    }
    private void call(JSONObject cmd,Consumer<JSONObject> done){
        if(busy)return;busy=true;int current=generation;
        io.execute(()->{
            try{
                if(nativeClient==null)nativeClient=new CloudNative(app.getApplicationContext());
                JSONObject value=nativeClient.run(cmd);
                app.runOnUiThread(()->{
                    busy=false;if(generation==current&&!app.isDestroyed())done.accept(value);
                    else if(cmd.optString("op").equals("open"))closeView();
                });
            }catch(Exception|LinkageError e){
                app.runOnUiThread(()->{busy=false;if(generation==current&&!app.isDestroyed())app.message(e.getMessage()==null?"云服务不可用":e.getMessage());});
            }
        });
    }
    void closeView(){if(nativeClient!=null)io.execute(()->{try{nativeClient.run(command("close"));}catch(Exception ignored){}});}
    void leave(){generation++; rows=null;}
    void show(LinearLayout parent){
        rows=app.column();app.add(rows,parent,0);
        if(enabled())refresh();
    }
    void account(){
        if(!enabled()){loginForm(null);return;}
        call(command("profiles"),value->{
            JSONArray profiles=value.optJSONArray("profiles");int count=profiles==null?0:profiles.length();
            String[] options=new String[count+1];options[0]=app.L("添加云服务");
            for(int i=0;i<count;i++){JSONObject p=profiles.optJSONObject(i);options[i+1]=p.optString("label")+(p.optBoolean("signedOut")?"（已退出）":"");}
            new AlertDialog.Builder(app).setTitle(app.L("云服务")).setItems(options,(d,i)->{
                if(i==0)loginForm(null);else manage(profiles.optJSONObject(i-1));
            }).setNegativeButton(app.L("取消"),null).show();
        });
    }
    private void manage(JSONObject p){
        boolean off=p.optBoolean("signedOut");
        String[] actions=off?new String[]{app.L("登录"),app.L("删除云服务配置")}:new String[]{app.L("刷新云设备"),app.L("退出云账号"),app.L("删除云服务配置")};
        new AlertDialog.Builder(app).setTitle(p.optString("label")).setItems(actions,(d,i)->{
            if(i==0){if(off)loginForm(p);else refresh();}else confirm(p,i==actions.length-1);
        }).setNegativeButton(app.L("取消"),null).show();
    }
    private void confirm(JSONObject p,boolean remove){
        JSONArray devices=p.optJSONArray("computers");StringBuilder affected=new StringBuilder(p.optString("label")).append("\n");
        affected.append(remove?"从本机清除此配置、设备别名和配对密钥。":"退出此账号下所有电脑，保留配置和设备别名。");
        if(devices!=null)for(int i=0;i<devices.length();i++)affected.append("\n").append(devices.optString(i));
        String title=app.L(remove?"删除云服务配置":"退出云账号");
        new AlertDialog.Builder(app).setTitle(title).setMessage(affected.toString()).setNegativeButton(app.L("取消"),null)
            .setPositiveButton(title,(d,w)->call(command(remove?"remove":"logout","profile",p.optString("id")),v->{
                lastDirectory=null;app.prefs.edit().putBoolean("cloud-account-enabled",v.optJSONArray("profiles").length()>0).apply();app.home();
                if(!v.optBoolean("remoteConfirmed"))app.message("本机配置已更新，远端退出暂未确认。");
            })).show();
    }
    private void cardLogout(String profile){
        call(command("profiles"),value->{JSONArray all=value.optJSONArray("profiles");for(int i=0;i<all.length();i++){
            JSONObject p=all.optJSONObject(i);if(profile.equals(p.optString("id"))){confirm(p,false);return;}
        }});
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
    private void loginForm(JSONObject p){
        LinearLayout form=app.column();
        Spinner type=new Spinner(app);
        ArrayAdapter<String> choices=new ArrayAdapter<>(app,android.R.layout.simple_spinner_item,new String[]{app.L("官方云服务"),app.L("自建云服务")});
        choices.setDropDownViewResource(android.R.layout.simple_spinner_dropdown_item);type.setAdapter(choices);form.addView(type);
        EditText server=field(form,app.L("服务器地址")),account=field(form,app.L("账号")),password=field(form,app.L("密码"));
        server.setInputType(InputType.TYPE_CLASS_TEXT|InputType.TYPE_TEXT_VARIATION_URI);server.setVisibility(android.view.View.GONE);
        password.setInputType(InputType.TYPE_CLASS_TEXT|InputType.TYPE_TEXT_VARIATION_PASSWORD);
        int initial=p!=null&&"custom".equals(p.optString("serverType"))?1:0;
        type.setSelection(initial);server.setVisibility(initial==1?android.view.View.VISIBLE:android.view.View.GONE);
        if(p!=null){account.setText(p.optString("login"));server.setText(initial==1?p.optString("customOrigin"):"");}
        type.setOnItemSelectedListener(new AdapterView.OnItemSelectedListener(){
            private int prior=initial;
            public void onNothingSelected(AdapterView<?> parent){}
            public void onItemSelected(AdapterView<?> parent,android.view.View row,int index,long id){
                server.setVisibility(index==1?android.view.View.VISIBLE:android.view.View.GONE);
                if(prior!=index){password.setText("");server.setText("");}prior=index;
            }
        });
        AlertDialog login=new AlertDialog.Builder(app).setTitle(app.L("登录云账号")).setView(form).setNegativeButton(app.L("取消"),null)
            .setPositiveButton(app.L("登录"),null).create();
        login.setOnShowListener(ignored->login.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener(sender->{
            boolean custom=type.getSelectedItemPosition()==1;String address=server.getText().toString().trim();
            if(custom&&address.isEmpty()){server.setError(app.L("请输入自建服务的 HTTPS 地址"));return;}
            if(account.length()==0||password.length()==0)return;
            JSONObject cmd=custom?command("login","serverType","custom","origin",address):command("login","serverType","official");
            try{cmd.put("login",account.getText().toString().trim()).put("password",password.getText().toString());}catch(JSONException e){throw new AssertionError(e);}
            password.setText("");login.dismiss();call(cmd,v->{lastDirectory=null;app.prefs.edit().putBoolean("cloud-account-enabled",true).apply();app.home();});
        }));login.show();
    }
    private EditText field(LinearLayout form,String hint){EditText input=new EditText(app);input.setSingleLine(true);input.setHint(hint);form.addView(input);return input;}
    void refresh(){
        if(!enabled()||rows==null)return;
        if(lastDirectory!=null)render(lastDirectory);
        if(!busy)call(command("devices","name","Android Bridge Test"),this::render);
    }
    private void render(JSONObject v){
        if(rows==null)return;
        lastDirectory=v;
            rows.removeAllViews();JSONArray devices=v.optJSONArray("devices");if(devices==null)return;
            for(int i=0;i<devices.length();i++){
                JSONObject device=devices.optJSONObject(i),gateway=device.optJSONObject("gateway");
                String id=device.optString("binding_id"),profile=device.optString("profile"),name=device.optString("name"),status=device.optString("status");
                boolean online=gateway.optBoolean("online");
                LinearLayout card=app.card();Button open=app.button(name,()->open(profile,id,name));
                open.setEnabled(online&&(status.equals("active")||status.equals("pending")));app.add(open,card,4);
                app.add(app.text(device.optString("source")+" · "+(!online?"离线":status.equals("active")?"已允许连接":status.equals("pending")?"待电脑允许":"已停用"),14),card,0);if(status.equals("pending")&&!device.optString("safety").isEmpty())app.add(app.text(device.optString("safety"),13),card,0);
                if(device.has("proofError"))app.add(app.text(device.optString("proofError"),13),card,0);
                LinearLayout actions=new LinearLayout(app);
                actions.addView(app.button(app.L("重命名"),()->rename(profile,gateway.optString("id"),name)),new LinearLayout.LayoutParams(0,-2,1));
                actions.addView(app.button(app.L("退出云账号"),()->cardLogout(profile)),new LinearLayout.LayoutParams(0,-2,1));
                app.add(actions,card,0);app.add(card,rows,12);
            }
            JSONArray errors=v.optJSONArray("errors");
            if(errors!=null)for(int i=0;i<errors.length();i++)app.add(app.text(errors.optString(i),14),rows,4);
    }
    private void open(String profile,String id,String name){
        call(command("open","profile",profile,"binding",id,"name","Android Bridge Test"),v->{
            if(v.has("entry"))app.connect(v.optString("entry"),name);
            else new AlertDialog.Builder(app).setMessage("在电脑的云设备列表核对安全码并开启允许连接。\n"+v.optString("safety")).setPositiveButton(app.L("好"),null).show();
        });
    }
}
