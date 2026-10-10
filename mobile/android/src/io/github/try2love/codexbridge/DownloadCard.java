package io.github.try2love.codexbridge;

import android.content.res.ColorStateList;
import android.graphics.Typeface;
import android.text.TextUtils;
import android.view.*;
import android.widget.*;
import java.util.Locale;

/** Activity-level download controls: navigation never owns or removes this view. */
final class DownloadCard extends LinearLayout {
 private final MainActivity activity;private final FrameLayout parent;
 private final TextView title,details;private final ProgressBar progress;private final LinearLayout actions;
 private final Button primary,open,cancel,toggle;
 private boolean collapsed,dragged;private float downX,downY,startX,startY;private boolean moving;
 DownloadCard(MainActivity activity,FrameLayout parent,Runnable action,Runnable openFile,Runnable dismiss){
  super(activity);this.activity=activity;this.parent=parent;
  setOrientation(VERTICAL);setPadding(dp(12),dp(8),dp(12),dp(10));
  android.graphics.drawable.GradientDrawable face=activity.background(0xfff6f9fe,16);face.setStroke(dp(1),0xffd7e1ef);setBackground(face);setElevation(dp(8));
  LinearLayout heading=new LinearLayout(activity);heading.setGravity(Gravity.CENTER_VERTICAL);
  title=activity.text("",14);title.setTypeface(null,Typeface.BOLD);title.setSingleLine();title.setEllipsize(TextUtils.TruncateAt.MIDDLE);title.setPadding(0,dp(6),dp(4),dp(6));heading.addView(title,new LinearLayout.LayoutParams(0,-2,1));
  toggle=control("−",()->setCollapsed(!collapsed));heading.addView(toggle,new LinearLayout.LayoutParams(dp(40),dp(40)));addView(heading);
  title.setOnClickListener(v->setCollapsed(!collapsed));
  title.setOnTouchListener((v,event)->{switch(event.getActionMasked()){
   case MotionEvent.ACTION_DOWN:downX=event.getRawX();downY=event.getRawY();startX=getX();startY=getY();moving=false;return true;
   case MotionEvent.ACTION_MOVE:float dx=event.getRawX()-downX,dy=event.getRawY()-downY;if(moving||Math.hypot(dx,dy)>ViewConfiguration.get(activity).getScaledTouchSlop()){moving=true;dragged=true;setX(startX+dx);setY(startY+dy);place();}return true;
   case MotionEvent.ACTION_UP:if(!moving)v.performClick();return true;
   case MotionEvent.ACTION_CANCEL:return true;default:return false;
  }});
  progress=new ProgressBar(activity,null,android.R.attr.progressBarStyleHorizontal);progress.setMax(1000);progress.setProgressTintList(ColorStateList.valueOf(0xff477ccc));progress.setProgressBackgroundTintList(ColorStateList.valueOf(0xffdce6f4));addView(progress,new LinearLayout.LayoutParams(-1,dp(4)));
  details=activity.text("",12);details.setTextColor(0xff63738a);details.setPadding(0,dp(8),0,dp(4));addView(details,new LinearLayout.LayoutParams(-1,-2));
  actions=new LinearLayout(activity);open=control(L("打开"),openFile);open.setVisibility(GONE);LinearLayout.LayoutParams opening=new LinearLayout.LayoutParams(0,dp(40),1);opening.setMarginEnd(dp(8));actions.addView(open,opening);
  primary=control("",action);primary.setTextColor(0xff3268b5);cancel=control("",dismiss);LinearLayout.LayoutParams first=new LinearLayout.LayoutParams(0,dp(40),1);first.setMarginEnd(dp(8));actions.addView(primary,first);actions.addView(cancel,new LinearLayout.LayoutParams(0,dp(40),1));addView(actions);
  parent.addView(this,new FrameLayout.LayoutParams(dp(320),-2,Gravity.TOP|Gravity.LEFT));setVisibility(GONE);
  parent.addOnLayoutChangeListener((v,l,t,r,b,ol,ot,or,ob)->place());addOnLayoutChangeListener((v,l,t,r,b,ol,ot,or,ob)->place());
 }
 private int dp(int value){return activity.dp(value);}
 private Button control(String label,Runnable action){Button button=activity.button(label,action);button.setTextSize(13);button.setMinHeight(0);button.setMinimumHeight(0);button.setMinWidth(0);button.setMinimumWidth(0);button.setPadding(dp(4),0,dp(4),0);return button;}
 private String L(String text){return activity.L(text);}
 void showTask(){collapsed=false;dragged=false;setVisibility(VISIBLE);post(this::place);}
 private void setCollapsed(boolean value){collapsed=value;actions.setVisibility(value?GONE:VISIBLE);details.setVisibility(value?GONE:VISIBLE);toggle.setText(value?"＋":"−");toggle.setContentDescription(L(value?"展开下载":"收起下载"));place();}
 void place(){
  if(getVisibility()!=VISIBLE||parent.getWidth()==0)return;
  if(!collapsed&&getHeight()>0&&parent.getHeight()-parent.getPaddingTop()-parent.getPaddingBottom()<getHeight()+dp(112)){setCollapsed(true);return;}
  int available=parent.getWidth()-parent.getPaddingLeft()-parent.getPaddingRight()-dp(24);
  FrameLayout.LayoutParams layout=(FrameLayout.LayoutParams)getLayoutParams();int width=Math.max(dp(120),Math.min(dp(collapsed?200:320),available));
  if(layout.width!=width){layout.width=width;setLayoutParams(layout);}
  float left=parent.getPaddingLeft()+dp(12),top=parent.getPaddingTop()+dp(12);
  float right=Math.max(left,parent.getWidth()-parent.getPaddingRight()-width-dp(12));
  float bottom=Math.max(top,parent.getHeight()-parent.getPaddingBottom()-getHeight()-dp(88));
  setX(dragged?Math.max(left,Math.min(right,getX())):right);setY(dragged?Math.max(top,Math.min(bottom,getY())):bottom);
 }
 void render(ArtifactDownload.Task task,boolean saving,boolean opening){
  if(task==null){setVisibility(GONE);return;}
  if(getVisibility()!=VISIBLE)showTask();
  ArtifactDownload.State state=task.state;boolean active=state==ArtifactDownload.State.RUNNING||state==ArtifactDownload.State.READY||state==ArtifactDownload.State.PAUSING;
  String size=bytes(task.downloaded)+(task.total>=0?" / "+bytes(task.total):"");
  String phase=state==ArtifactDownload.State.COMPLETE?L("下载完成，可打开或保存"):state==ArtifactDownload.State.PAUSED?L("已暂停"):state==ArtifactDownload.State.FAILED?L("下载中断"):state==ArtifactDownload.State.PAUSING?L("正在暂停…"):L("正在下载…");
  if(opening)phase=L("正在打开…");
  boolean busy=saving||opening;
  if(saving)phase=L("正在保存…");
  String percent=task.total>0?String.format(Locale.ROOT,"%d%%",Math.min(100,task.downloaded*100/task.total)):state==ArtifactDownload.State.COMPLETE?"100%":"";
  title.setText((collapsed&&!percent.isEmpty()?percent+" · ":"")+task.name);
  title.setContentDescription(task.name+" · "+phase+" · "+size);
  progress.setIndeterminate(active&&task.total<0);progress.setProgress(task.total>0?(int)Math.min(1000,task.downloaded*1000/task.total):state==ArtifactDownload.State.COMPLETE?1000:0);
  details.setText(phase+" · "+size+(state==ArtifactDownload.State.RUNNING?" · "+bytes((long)task.bytesPerSecond)+"/s":"")+(state==ArtifactDownload.State.FAILED&&!task.error.isEmpty()?"\n"+L(task.error):""));
  primary.setText(L(state==ArtifactDownload.State.COMPLETE?"保存":active?"暂停":task.resumable()?"继续":"重新下载"));primary.setEnabled(!busy&&state!=ArtifactDownload.State.PAUSING&&state!=ArtifactDownload.State.READY);
  cancel.setText(L(state==ArtifactDownload.State.COMPLETE?"关闭":"取消"));cancel.setEnabled(!busy);
  open.setText(L("打开"));open.setVisibility(state==ArtifactDownload.State.COMPLETE?VISIBLE:GONE);open.setEnabled(!busy);
  setCollapsed(collapsed);
 }
 private static String bytes(long value){if(value<1024)return value+" B";if(value<1024*1024)return String.format(Locale.ROOT,"%.1f KB",value/1024d);return String.format(Locale.ROOT,"%.1f MB",value/(1024d*1024));}
}
