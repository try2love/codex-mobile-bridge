package io.github.try2love.codexbridge;

import android.app.*;
import android.animation.ValueAnimator;
import android.content.*;
import android.graphics.Bitmap;
import android.graphics.drawable.BitmapDrawable;
import android.net.Uri;
import android.os.*;
import android.view.*;
import android.widget.*;
import android.transition.*;
import java.util.*;

/** A full-screen settings window preserves the caller's WebView and active downloads. */
final class SettingsPage extends Dialog implements SharedPreferences.OnSharedPreferenceChangeListener {
 private final MainActivity owner;
 private final SharedPreferences prefs;
 private final SettingsUi ui;
 private final Handler handler=new Handler(Looper.getMainLooper());
 private final LinearLayout notificationHost;
 private final LinearLayout body,systemHost;
 private final SettingsUi.ToggleRow taskToggle,backgroundToggle;
 private final View backgroundDivider;
 private final FrameLayout screen;
 private final ScrollView scroll;
 private final int initialScroll;
 private final boolean entering;
 private boolean closing;
 private Bitmap previousFrame;
 private BitmapDrawable previousDrawable;
 private ValueAnimator pageAnimator,themeAnimator;
 private final Runnable refreshStatus=new Runnable(){public void run(){if(isShowing()){refresh();handler.postDelayed(this,3000);}}};
 private String signature="";
 private SettingsMotion.Sheet themePicker;
 SettingsPage(MainActivity owner,int position){this(owner,position,true,null);}
 SettingsPage(MainActivity owner,int position,boolean entering){this(owner,position,entering,null);}
 private SettingsPage(MainActivity owner,int position,boolean entering,Bitmap previousFrame){
  super(SettingsUi.themed(owner),owner.getResources().getIdentifier("SettingsTheme","style",owner.getPackageName()));this.owner=owner;initialScroll=position;this.entering=entering;this.previousFrame=previousFrame;prefs=owner.getSharedPreferences("bridge",0);ui=new SettingsUi(getContext());setOwnerActivity(owner);requestWindowFeature(Window.FEATURE_NO_TITLE);
  LinearLayout content=ui.column();content.setBackgroundColor(ui.page);
  LinearLayout toolbar=new LinearLayout(getContext());toolbar.setGravity(Gravity.CENTER_VERTICAL);toolbar.setPadding(ui.dp(12),ui.dp(8),ui.dp(12),ui.dp(8));toolbar.setBackgroundColor(ui.surface);
  View back=ui.icon("back",ui.text);back.setContentDescription(ui.L("返回"));ui.clickable(back,this::close);toolbar.addView(back,new LinearLayout.LayoutParams(ui.dp(44),ui.dp(44)));
  TextView title=ui.text("设置",20,ui.text);title.setTypeface(android.graphics.Typeface.create("sans-serif-medium",0));toolbar.addView(title);content.addView(toolbar);
  scroll=new ScrollView(getContext());scroll.setFillViewport(true);scroll.setClipToPadding(false);body=ui.column();body.setPadding(ui.dp(20),ui.dp(12),ui.dp(20),ui.dp(24));scroll.addView(body);content.addView(scroll,new LinearLayout.LayoutParams(-1,0,1));
  notificationHost=ui.column();body.addView(notificationHost);
  LinearLayout notifications=ui.section(notificationHost,"通知");
  taskToggle=ui.toggle(notifications,"任务通知",prefs.getBoolean("alerts",false),owner::toggleNotifications);
  backgroundToggle=ui.toggle(notifications,"后台通知",prefs.getBoolean("backgroundNotifications",false),owner::toggleBackgroundNotifications);backgroundDivider=notifications.getChildAt(1);
  ui.hint(backgroundToggle.row,"后台通知功能说明",()->new AlertDialog.Builder(getContext()).setTitle(ui.L("后台通知功能说明")).setMessage(ui.L("后台通知仍在完善中，开启后可能增加耗电或发热。\n遇到问题或有改进建议，欢迎提交 Issue 或 PR。感谢支持！")).setPositiveButton(ui.L("关闭"),null).show());
  systemHost=ui.column();systemHost.setPadding(0,ui.dp(16),0,0);notificationHost.addView(systemHost);buildNotifications();
  LinearLayout app=ui.section(body,"应用");themeRow(app);languageRow(app);
  LinearLayout update=ui.action(app,"检查更新","",()->{});TextView value=ui.value(update,owner.appVersion());
  android.view.ViewGroup.LayoutParams valueLayout=value.getLayoutParams();update.removeView(value);update.addView(value,update.getChildCount()-1,valueLayout);
  update.setOnClickListener(v->owner.checkUpdate(this,update,value));
  screen=new FrameLayout(getContext());screen.addView(content,new FrameLayout.LayoutParams(-1,-1));setContentView(screen);
  getWindow().setBackgroundDrawableResource(android.R.color.transparent);getWindow().clearFlags(WindowManager.LayoutParams.FLAG_DIM_BEHIND);getWindow().setWindowAnimations(0);getWindow().getDecorView().setPadding(0,0,0,0);
  new SystemBars(getWindow(),screen,()->{}).update(ui.surface,ui.page);
  setOnShowListener(d->{getWindow().setLayout(-1,-1);if(entering&&SettingsMotion.enabled()){screen.setAlpha(0);screen.setTranslationX(ui.dp(24));}
   screen.getViewTreeObserver().addOnPreDrawListener(new ViewTreeObserver.OnPreDrawListener(){public boolean onPreDraw(){screen.getViewTreeObserver().removeOnPreDrawListener(this);scroll.scrollTo(0,initialScroll);appear();return true;}});
  });
 }
 int scrollPosition(){return scroll.getScrollY();}
 boolean isClosing(){return closing;}
 SettingsPage replacement(){Bitmap frame=ui.page==new SettingsUi(SettingsUi.themed(owner)).page?null:SettingsMotion.capture(screen);return new SettingsPage(owner,scrollPosition(),false,frame);}
 private void appear(){
  if(closing||!isShowing())return;
  if(previousFrame!=null){
   previousDrawable=new BitmapDrawable(getContext().getResources(),previousFrame);previousDrawable.setBounds(0,0,screen.getWidth(),screen.getHeight());screen.getOverlay().add(previousDrawable);
   themeAnimator=SettingsMotion.animate(SettingsMotion.THEME,f->previousDrawable.setAlpha(Math.round(255*(1-f))),()->{themeAnimator=null;releaseFrame();});
  }else if(entering)pageAnimator=SettingsMotion.animate(SettingsMotion.ENTER,f->{screen.setAlpha(f);screen.setTranslationX(ui.dp(24)*(1-f));},()->pageAnimator=null);
 }
 private void releaseFrame(){
  if(themeAnimator!=null){themeAnimator.cancel();themeAnimator=null;}if(previousDrawable!=null){screen.getOverlay().remove(previousDrawable);previousDrawable=null;}if(previousFrame!=null){previousFrame.recycle();previousFrame=null;}
 }
 private void close(){
  if(closing||!isShowing())return;closing=true;if(pageAnimator!=null)pageAnimator.cancel();releaseFrame();TransitionManager.endTransitions(body);
  float alpha=screen.getAlpha(),position=screen.getTranslationX();pageAnimator=SettingsMotion.animate(SettingsMotion.EXIT,f->{screen.setAlpha(alpha*(1-f));screen.setTranslationX(position+(ui.dp(24)-position)*f);},()->{pageAnimator=null;dismiss();});
 }
 @Override public void onBackPressed(){close();}
 @Override public void cancel(){close();}
 @Override public boolean dispatchTouchEvent(MotionEvent event){return closing||super.dispatchTouchEvent(event);}
 @Override protected void onStart(){super.onStart();prefs.registerOnSharedPreferenceChangeListener(this);handler.post(refreshStatus);}
 @Override protected void onStop(){prefs.unregisterOnSharedPreferenceChangeListener(this);handler.removeCallbacksAndMessages(null);if(pageAnimator!=null){pageAnimator.cancel();pageAnimator=null;}releaseFrame();TransitionManager.endTransitions(body);if(themePicker!=null)themePicker.dismiss();super.onStop();}
 @Override public void onSharedPreferenceChanged(SharedPreferences preferences,String key){
  if("settingsTheme".equals(key)||"language".equals(key)){owner.reopenSettings(this);return;}
  if("alerts".equals(key)||"backgroundNotifications".equals(key))refresh(true);
 }
 void refresh(){refresh(false);}
 private void refresh(boolean animate){
  if(closing)return;
  String next=statusSignature();if(next.equals(signature))return;
  if(animate&&SettingsMotion.enabled()&&body.isLaidOut()){TransitionManager.endTransitions(body);TransitionSet transition=new TransitionSet().setOrdering(TransitionSet.ORDERING_TOGETHER);transition.addTransition(new ChangeBounds()).addTransition(new Fade());transition.setDuration(SettingsMotion.LAYOUT);transition.setInterpolator(SettingsMotion.EASE);TransitionManager.beginDelayedTransition(body,transition);}
  int position=scroll.getScrollY();buildNotifications();scroll.post(()->scroll.scrollTo(0,position));
 }
 private String statusSignature(){
  NotificationManager manager=(NotificationManager)owner.getSystemService(Context.NOTIFICATION_SERVICE);
  PowerManager power=(PowerManager)owner.getSystemService(Context.POWER_SERVICE);
  return prefs.getBoolean("alerts",false)+":"+prefs.getBoolean("backgroundNotifications",false)+":"+manager.areNotificationsEnabled()+":"+blocked("foreground-tasks")+":"+blocked("background-monitor")+":"+power.isIgnoringBatteryOptimizations(owner.getPackageName())+":"+connectionStates();
 }
 private void buildNotifications(){
  signature=statusSignature();systemHost.removeAllViews();
  boolean alerts=prefs.getBoolean("alerts",false),background=alerts&&prefs.getBoolean("backgroundNotifications",false);
  taskToggle.checked(alerts);backgroundToggle.checked(background);backgroundToggle.row.setVisibility(alerts?View.VISIBLE:View.GONE);backgroundDivider.setVisibility(alerts?View.VISIBLE:View.GONE);systemHost.setVisibility(alerts?View.VISIBLE:View.GONE);
  if(!alerts)return;
  LinearLayout system=ui.section(systemHost,"系统权限");
  boolean allowed=((NotificationManager)owner.getSystemService(Context.NOTIFICATION_SERVICE)).areNotificationsEnabled();
  boolean tasksBlocked=blocked("foreground-tasks"),backgroundBlocked=background&&blocked("background-monitor");
  String permission=allowed?(tasksBlocked||backgroundBlocked?"需处理":"已允许"):"前往授权";
  LinearLayout permissions=ui.action(system,"通知权限",permission,()->openSetting(new Intent(android.provider.Settings.ACTION_APP_NOTIFICATION_SETTINGS).putExtra(android.provider.Settings.EXTRA_APP_PACKAGE,owner.getPackageName())));
  if(!allowed||tasksBlocked||backgroundBlocked)((TextView)permissions.getChildAt(1)).setTextColor(ui.danger);
  if(tasksBlocked||backgroundBlocked){
   String warning=tasksBlocked?(backgroundBlocked?"任务提醒与后台运行频道已关闭":"任务提醒频道已关闭"):"后台运行频道已关闭";
   LinearLayout labels=(LinearLayout)permissions.getChildAt(0);TextView detail=ui.text(warning,12,ui.danger);LinearLayout.LayoutParams p=new LinearLayout.LayoutParams(-1,-2);p.topMargin=ui.dp(4);labels.addView(detail,p);
  }
  if(background){
   ui.action(system,"后台运行设置","应用详情",()->openSetting(detailsIntent()));
   PowerManager power=(PowerManager)owner.getSystemService(Context.POWER_SERVICE);
   ui.action(system,"电池优化",power.isIgnoringBatteryOptimizations(owner.getPackageName())?"已豁免":"已启用",()->openSetting(new Intent(android.provider.Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS)));
  }
  ui.action(system,"通知来源","",this::showNotificationSources);
  ui.action(system,"发送测试通知","10 秒后提醒",owner::testNotification);
  Map<String,String> connections=connectionStates();
  if(connections.values().stream().anyMatch(value->!"已连接".equals(value))){
   LinearLayout row=ui.action(system,"电脑连接","需处理",this::showConnections);((TextView)row.getChildAt(1)).setTextColor(ui.danger);
  }
 }
 private void showNotificationSources(){
  LinearLayout content=ui.column();content.setPadding(ui.dp(8),ui.dp(8),ui.dp(8),ui.dp(8));
  TextView hint=ui.text("仅控制这台手机的任务提醒，不影响聊天和通知收件箱。",13,ui.muted);hint.setPadding(ui.dp(16),ui.dp(8),ui.dp(16),ui.dp(12));content.addView(hint);
  if(owner.saved().isEmpty())content.addView(ui.text("尚未保存电脑连接",15,ui.muted));
  LinearLayout list=ui.column();content.addView(list);
  for(String address:new TreeSet<>(owner.saved())){
   SettingsUi.ToggleRow[] choice=new SettingsUi.ToggleRow[1];
   choice[0]=ui.toggle(list,owner.computerName(address),NotificationSources.enabled(prefs,address),()->{
    boolean enabled=!NotificationSources.enabled(prefs,address);
    NotificationSources.setEnabled(prefs,address,enabled);choice[0].checked(enabled);
    choice[0].row.setContentDescription(owner.computerName(address)+", "+address+", "+ui.L(enabled?"已开启":"已关闭"));
    MonitorService.refresh();
   });
   LinearLayout labels=(LinearLayout)choice[0].row.getChildAt(0);
   TextView url=ui.text(address,12,ui.muted);url.setPadding(0,ui.dp(4),ui.dp(8),0);labels.addView(url);
   choice[0].row.setContentDescription(owner.computerName(address)+", "+address+", "+ui.L(NotificationSources.enabled(prefs,address)?"已开启":"已关闭"));
  }
  ScrollView scroll=new ScrollView(getContext());scroll.addView(content);
  new AlertDialog.Builder(getContext()).setTitle(ui.L("通知来源")).setView(scroll).setPositiveButton(ui.L("完成"),null).show();
 }
 private boolean blocked(String id){NotificationChannel channel=((NotificationManager)owner.getSystemService(Context.NOTIFICATION_SERVICE)).getNotificationChannel(id);return channel!=null&&channel.getImportance()==NotificationManager.IMPORTANCE_NONE;}
 private Map<String,String> connectionStates(){
  Map<String,NotificationReceiver.Status> statuses=MonitorService.connectionStates();Map<String,String> result=new TreeMap<>();
  for(String address:owner.saved()){
   NotificationReceiver.Status status=statuses.get(address);
   result.put(address,status==NotificationReceiver.Status.RECEIVING?"已连接":status==NotificationReceiver.Status.RETRYING?"正在重试":"请重新登录");
  }
  return result;
 }
 private void showConnections(){
  StringBuilder message=new StringBuilder();
  for(Map.Entry<String,String> row:connectionStates().entrySet()){
   if(message.length()>0)message.append("\n\n");message.append(owner.computerName(row.getKey())).append('\n').append(row.getKey()).append('\n').append(ui.L(row.getValue()));
  }
  new AlertDialog.Builder(getContext()).setTitle(ui.L("电脑连接")).setMessage(message).setNegativeButton(ui.L("关闭"),null).setPositiveButton(ui.L("刷新状态"),(d,w)->{MonitorService.refresh();showConnections();}).show();
 }
 private Intent detailsIntent(){return new Intent(android.provider.Settings.ACTION_APPLICATION_DETAILS_SETTINGS,Uri.parse("package:"+owner.getPackageName()));}
 private void openSetting(Intent intent){try{owner.startActivity(intent);}catch(RuntimeException unavailable){try{owner.startActivity(detailsIntent());}catch(RuntimeException denied){Toast.makeText(getContext(),ui.L("请从系统设置打开本应用的详情页。"),Toast.LENGTH_LONG).show();}}}
 private void languageRow(LinearLayout list){
  String[] ids={"system","zh","en"}, names={ui.L("跟随系统"),"简体中文","English"};
  int active=Math.max(0,java.util.Arrays.asList(ids).indexOf(prefs.getString("language","system")));
  ui.action(list,"语言",names[active],()->new AlertDialog.Builder(getContext()).setTitle(ui.L("语言"))
   .setSingleChoiceItems(names,active,(d,w)->{d.dismiss();prefs.edit().putString("language",ids[w]).apply();})
   .setNegativeButton(ui.L("取消"),null).show());
 }
 private void themeRow(LinearLayout app){
  LinearLayout row=ui.action(app,"主题",themeName(),this::chooseTheme);
  ui.hint(row,"主题功能说明",()->Toast.makeText(getContext(),ui.L("待开发项，暂时只支持设置页主题切换"),Toast.LENGTH_LONG).show());
 }
 private String themeName(){String value=SettingsUi.theme(owner);return "light".equals(value)?"浅色":"dark".equals(value)?"深色":"跟随系统";}
 private void chooseTheme(){
  if(themePicker!=null&&themePicker.isShowing())return;
  LinearLayout sheet=ui.column();sheet.setPadding(ui.dp(16),ui.dp(20),ui.dp(16),ui.dp(28));
  android.graphics.drawable.GradientDrawable face=ui.face(ui.surface,20);float radius=ui.dp(20);face.setCornerRadii(new float[]{radius,radius,radius,radius,0,0,0,0});sheet.setBackground(face);
  TextView title=ui.text("主题",20,ui.text);title.setPadding(ui.dp(16),0,ui.dp(16),ui.dp(12));sheet.addView(title);
  String[] values={"system","light","dark"},labels={"跟随系统","浅色","深色"};
  RadioGroup options=new RadioGroup(getContext());sheet.addView(options,new LinearLayout.LayoutParams(-1,-2));
  for(int i=0;i<values.length;i++){
   final String value=values[i];RadioButton option=new RadioButton(getContext());option.setText(ui.L(labels[i]));option.setTextSize(15);option.setTextColor(ui.text);option.setChecked(value.equals(SettingsUi.theme(owner)));option.setPadding(ui.dp(16),0,ui.dp(16),0);option.setMinimumHeight(ui.dp(52));
   option.setButtonTintList(android.content.res.ColorStateList.valueOf(option.isChecked()?ui.accent:ui.track));
   option.setBackground(ui.face(option.isChecked()?ui.pressed:ui.surface,8));
   option.setOnClickListener(v->themePicker.close(()->prefs.edit().putString("settingsTheme",value).apply()));options.addView(option,new RadioGroup.LayoutParams(-1,-2));
  }
  TextView cancel=ui.text("取消",15,ui.muted);cancel.setGravity(Gravity.CENTER);cancel.setMinimumHeight(ui.dp(48));ui.clickable(cancel,()->themePicker.close(null));sheet.addView(cancel,new LinearLayout.LayoutParams(-1,-2));
  themePicker=new SettingsMotion.Sheet(getContext(),sheet,ui.dp(24));themePicker.show();
 }
}
