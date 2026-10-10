package io.github.try2love.codexbridge;

import android.app.Activity;
import android.graphics.*;
import android.graphics.drawable.Drawable;
import android.os.Build;
import android.view.*;

final class SystemBars {
 private final Window window;
 private final BarBackground background=new BarBackground();
 SystemBars(Activity activity,View screen,Runnable insetsChanged){
  this.window=activity.getWindow();
  window.clearFlags(WindowManager.LayoutParams.FLAG_TRANSLUCENT_STATUS|WindowManager.LayoutParams.FLAG_TRANSLUCENT_NAVIGATION);
  window.addFlags(WindowManager.LayoutParams.FLAG_DRAWS_SYSTEM_BAR_BACKGROUNDS);
  window.setStatusBarColor(Color.TRANSPARENT);window.setNavigationBarColor(Color.TRANSPARENT);
  if(Build.VERSION.SDK_INT>=28)window.setNavigationBarDividerColor(Color.TRANSPARENT);
  if(Build.VERSION.SDK_INT>=29){window.setStatusBarContrastEnforced(false);window.setNavigationBarContrastEnforced(false);}
  if(Build.VERSION.SDK_INT>=30)window.setDecorFitsSystemWindows(false);
  else window.getDecorView().setSystemUiVisibility(View.SYSTEM_UI_FLAG_LAYOUT_STABLE|View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN|View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION);
  screen.setOnApplyWindowInsetsListener((v,insets)->{
   if(Build.VERSION.SDK_INT>=30){
    Insets i=insets.getInsets(WindowInsets.Type.systemBars()|WindowInsets.Type.displayCutout()|WindowInsets.Type.ime());
    v.setPadding(i.left,i.top,i.right,i.bottom);
    insetsChanged.run();
    return WindowInsets.CONSUMED;
   }
   int left=insets.getSystemWindowInsetLeft(),top=insets.getSystemWindowInsetTop(),right=insets.getSystemWindowInsetRight(),bottom=insets.getSystemWindowInsetBottom();
   if(Build.VERSION.SDK_INT>=28&&insets.getDisplayCutout()!=null){DisplayCutout cutout=insets.getDisplayCutout();left=Math.max(left,cutout.getSafeInsetLeft());top=Math.max(top,cutout.getSafeInsetTop());right=Math.max(right,cutout.getSafeInsetRight());bottom=Math.max(bottom,cutout.getSafeInsetBottom());}
   v.setPadding(left,top,right,bottom);
   insetsChanged.run();
   return insets.consumeSystemWindowInsets();
  });
  // Insets are consumed here, so the native background fills the space outside the WebView.
  screen.setBackground(background);
  update(0xfff5f5f3,0xfff5f5f3);
  screen.requestApplyInsets();
 }
 void update(int top,int bottom){
  background.top=top;background.bottom=bottom;background.invalidateSelf();
  boolean lightTop=light(top),lightBottom=light(bottom);
  if(Build.VERSION.SDK_INT>=30){WindowInsetsController controller=window.getInsetsController();if(controller!=null)controller.setSystemBarsAppearance((lightTop?WindowInsetsController.APPEARANCE_LIGHT_STATUS_BARS:0)|(lightBottom?WindowInsetsController.APPEARANCE_LIGHT_NAVIGATION_BARS:0),WindowInsetsController.APPEARANCE_LIGHT_STATUS_BARS|WindowInsetsController.APPEARANCE_LIGHT_NAVIGATION_BARS);}
  else {View decor=window.getDecorView();int flags=decor.getSystemUiVisibility()&~(View.SYSTEM_UI_FLAG_LIGHT_STATUS_BAR|View.SYSTEM_UI_FLAG_LIGHT_NAVIGATION_BAR);decor.setSystemUiVisibility(flags|(lightTop?View.SYSTEM_UI_FLAG_LIGHT_STATUS_BAR:0)|(lightBottom?View.SYSTEM_UI_FLAG_LIGHT_NAVIGATION_BAR:0));}
 }
 private static final class BarBackground extends Drawable {
  private final Paint paint=new Paint();
  private int top,bottom,alpha=255;
  @Override public void draw(Canvas canvas){Rect b=getBounds();int middle=b.top+b.height()/2;paint.setColor(top);paint.setAlpha(alpha);canvas.drawRect(b.left,b.top,b.right,middle,paint);paint.setColor(bottom);paint.setAlpha(alpha);canvas.drawRect(b.left,middle,b.right,b.bottom,paint);}
  @Override public void setAlpha(int alpha){this.alpha=alpha;invalidateSelf();}
  @Override public void setColorFilter(ColorFilter filter){paint.setColorFilter(filter);invalidateSelf();}
  @Override public int getOpacity(){return alpha==255?PixelFormat.OPAQUE:PixelFormat.TRANSLUCENT;}
 }
 private static boolean light(int color){return Color.red(color)*.299+Color.green(color)*.587+Color.blue(color)*.114>=128;}
}
