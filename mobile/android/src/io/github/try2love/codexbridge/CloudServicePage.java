package io.github.try2love.codexbridge;

import android.app.Dialog;
import android.graphics.Typeface;
import android.text.InputType;
import android.util.TypedValue;
import android.view.*;
import android.widget.*;
import org.json.*;

/** Native cloud administration uses the same toolbar, grouped rows and palette as Settings. */
final class CloudServicePage extends Dialog {
    private final MainActivity owner;
    private final CloudComputers cloud;
    private final SettingsUi ui;
    private final LinearLayout content;
    private final TextView title;
    private final ScrollView scroll;
    private final View back;
    private JSONArray profiles;
    private JSONObject profile;
    private boolean form, listing=true;

    CloudServicePage(MainActivity owner,CloudComputers cloud){
        super(SettingsUi.themed(owner),owner.getResources().getIdentifier("SettingsTheme","style",owner.getPackageName()));
        this.owner=owner;this.cloud=cloud;ui=new SettingsUi(getContext());setOwnerActivity(owner);
        requestWindowFeature(Window.FEATURE_NO_TITLE);
        LinearLayout page=ui.column(),toolbar=new LinearLayout(getContext());
        page.setBackgroundColor(ui.page);toolbar.setBackgroundColor(ui.surface);toolbar.setGravity(Gravity.CENTER_VERTICAL);
        toolbar.setPadding(ui.dp(12),ui.dp(8),ui.dp(16),ui.dp(8));
        back=ui.icon("back",ui.text);back.setContentDescription(ui.L("返回"));ui.clickable(back,this::back);
        toolbar.addView(back,new LinearLayout.LayoutParams(ui.dp(44),ui.dp(44)));
        title=ui.text("云服务",20,ui.text);title.setTypeface(Typeface.create("sans-serif-medium",Typeface.NORMAL));toolbar.addView(title);
        page.addView(toolbar);
        scroll=new ScrollView(getContext());content=ui.column();content.setPadding(ui.dp(20),ui.dp(12),ui.dp(20),ui.dp(24));
        scroll.addView(content);page.addView(scroll,new LinearLayout.LayoutParams(-1,0,1));setContentView(page);
        getWindow().clearFlags(WindowManager.LayoutParams.FLAG_DIM_BEHIND);getWindow().setWindowAnimations(0);
        getWindow().setSoftInputMode(WindowManager.LayoutParams.SOFT_INPUT_ADJUST_RESIZE);
        new SystemBars(getWindow(),page,()->{}).update(ui.surface,ui.page);
        setOnShowListener(d->getWindow().setLayout(-1,-1));
    }
    private void reset(String heading){
        title.setText(ui.L(heading));content.removeAllViews();scroll.scrollTo(0,0);
    }
    void list(JSONArray value){
        profiles=value;profile=null;listing=true;form=false;reset("云服务");
        LinearLayout group=ui.section(content,"云服务配置");
        if(profiles!=null){
            for(int i=0;i<profiles.length();i++){
                JSONObject p=profiles.optJSONObject(i);if(p==null)continue;
                LinearLayout row=ui.row(cloud.profileTitle(p),p.optBoolean("signedOut")?ui.L("已退出"):ui.L("已登录"));
                row.addView(ui.icon("chevron",ui.muted),new LinearLayout.LayoutParams(ui.dp(24),ui.dp(24)));
                ui.clickable(row,()->manage(p));ui.append(group,row);
            }
        }else ui.append(group,ui.row("正在读取…",""));
        ui.action(group,"添加云服务","",()->edit(null));
    }
    void update(JSONArray value,String selected){
        profiles=value;
        if(selected!=null&&listing){
            for(int i=0;i<value.length();i++){
                JSONObject p=value.optJSONObject(i);
                if(selected.equals(p.optString("id"))){manage(p);return;}
            }
            dismiss();owner.message("云配置已删除，请刷新");return;
        }
        if(listing)list(value);
    }
    void manage(JSONObject p){
        profile=p;listing=false;form=false;reset("管理云服务");
        LinearLayout account=ui.section(content,cloud.profileTitle(p));
        ui.action(account,p.optBoolean("signedOut")?"重新登录":"编辑登录配置","",()->edit(p));
        if(!p.optBoolean("signedOut"))ui.action(account,"退出云账号","",()->cloud.confirm(p,false));
        LinearLayout removal=ui.section(content,"接入");
        LinearLayout row=ui.action(removal,"删除云服务配置","",()->cloud.confirm(p,true));
        ((TextView)((LinearLayout)row.getChildAt(0)).getChildAt(0)).setTextColor(ui.danger);
    }
    private EditText field(LinearLayout group,String name,int input){
        LinearLayout row=ui.column();row.setPadding(ui.dp(16),ui.dp(10),ui.dp(16),ui.dp(10));
        row.addView(ui.text(name,13,ui.muted));
        EditText entry=new EditText(getContext());entry.setSingleLine(true);entry.setInputType(input);
        entry.setTextColor(ui.text);entry.setTextSize(TypedValue.COMPLEX_UNIT_SP,16);
        entry.setContentDescription(ui.L(name));entry.setBackgroundColor(android.graphics.Color.TRANSPARENT);
        entry.setPadding(0,ui.dp(4),0,ui.dp(2));row.addView(entry,new LinearLayout.LayoutParams(-1,ui.dp(44)));
        ui.append(group,row);return entry;
    }
    void edit(JSONObject p){
        form=true;listing=false;profile=p;reset(p==null?"添加云服务":"编辑登录配置");
        LinearLayout types=ui.section(content,"服务类型"),credentials=ui.section(content,"登录信息");
        RadioGroup modes=new RadioGroup(getContext());modes.setOrientation(LinearLayout.HORIZONTAL);
        modes.setPadding(ui.dp(8),ui.dp(4),ui.dp(8),ui.dp(4));types.addView(modes);
        for(String label:new String[]{"官方云服务","自建云服务"}){
            RadioButton button=new RadioButton(getContext());button.setId(View.generateViewId());button.setText(ui.L(label));button.setTextColor(ui.text);button.setTextSize(15);
            button.setMinHeight(ui.dp(48));modes.addView(button,new RadioGroup.LayoutParams(0,-2,1));
        }
        EditText server=field(credentials,"服务器地址",InputType.TYPE_CLASS_TEXT|InputType.TYPE_TEXT_VARIATION_URI);
        EditText login=field(credentials,"账号",InputType.TYPE_CLASS_TEXT|InputType.TYPE_TEXT_VARIATION_EMAIL_ADDRESS);
        EditText password=field(credentials,"密码",InputType.TYPE_CLASS_TEXT|InputType.TYPE_TEXT_VARIATION_PASSWORD);
        View sourceRow=(View)server.getParent(),separator=credentials.getChildAt(1);
        int customId=modes.getChildAt(1).getId();boolean custom=p!=null&&"custom".equals(p.optString("serverType"));
        modes.check(modes.getChildAt(custom?1:0).getId());sourceRow.setVisibility(custom?View.VISIBLE:View.GONE);separator.setVisibility(sourceRow.getVisibility());
        if(p!=null){login.setText(p.optString("login"));server.setText(custom?p.optString("customOrigin"):"");}
        modes.setOnCheckedChangeListener((group,id)->{
            sourceRow.setVisibility(id==customId?View.VISIBLE:View.GONE);separator.setVisibility(sourceRow.getVisibility());server.setText("");password.setText("");
        });
        Button submit=owner.button(ui.L("登录"),()->{});submit.setTextColor(ui.surface);submit.setBackground(ui.face(ui.text,12));
        LinearLayout.LayoutParams lp=new LinearLayout.LayoutParams(-1,-2);lp.topMargin=ui.dp(20);content.addView(submit,lp);
        submit.setOnClickListener(v->{
            boolean hosted=modes.getCheckedRadioButtonId()!=customId;
            String address=server.getText().toString().trim(),username=login.getText().toString().trim();
            if(!hosted&&address.isEmpty()){server.setError(ui.L("请输入自建服务的 HTTPS 地址"));return;}
            if(username.isEmpty()){login.setError(ui.L("请输入账号"));return;}
            if(password.length()==0){password.setError(ui.L("请输入密码"));return;}
            JSONObject command=cloud.command("login","serverType",hosted?"official":"custom","login",username);
            try{if(!hosted)command.put("origin",address);if(p!=null)command.put("replaceProfile",p.getString("id"));}
            catch(JSONException invalid){throw new IllegalArgumentException(invalid);}
            Runnable signIn=()->{
                try{command.put("password",password.getText().toString());}catch(JSONException invalid){throw new IllegalArgumentException(invalid);}
                password.setText("");submit.setEnabled(false);submit.setText(ui.L("正在登录…"));
                cloud.login(command,()->{if(isShowing()){submit.setText(ui.L("登录"));submit.setEnabled(true);}});
            };
            if(p==null){signIn.run();return;}
            new android.app.AlertDialog.Builder(getContext()).setTitle(ui.L("更新登录配置？"))
                .setMessage(cloud.profileTitle(p)+"\n"+ui.L("验证成功后替换此配置；更换服务器或账号需重新配对。"))
                .setNegativeButton(ui.L("取消"),null).setPositiveButton(ui.L("继续"),(d,w)->signIn.run()).show();
        });
    }
    private void back(){
        if(form&&profile!=null)manage(profile);
        else if(!listing)list(profiles);
        else dismiss();
    }
    @Override public void onBackPressed(){back();}
}
