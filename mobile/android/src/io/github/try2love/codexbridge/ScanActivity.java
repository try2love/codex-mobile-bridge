package io.github.try2love.codexbridge;
import android.app.*;
import android.os.*;
import android.content.*;
import android.hardware.Camera;
import android.view.*;
import android.widget.*;
import com.google.zxing.*;
import com.google.zxing.common.HybridBinarizer;
import java.util.*;

@SuppressWarnings("deprecation")
public final class ScanActivity extends Activity implements SurfaceHolder.Callback,Camera.PreviewCallback {
 private Camera camera;private boolean done;private long last;
 public void onCreate(Bundle b){super.onCreate(b);LinearLayout root=new LinearLayout(this);root.setOrientation(1);root.setFitsSystemWindows(true);TextView title=new TextView(this);title.setText("扫描电脑网关的登录二维码");title.setTextSize(19);title.setPadding(24,36,24,24);root.addView(title);SurfaceView view=new SurfaceView(this);view.getHolder().addCallback(this);root.addView(view,new LinearLayout.LayoutParams(-1,0,1));Button cancel=new Button(this);cancel.setText("取消");cancel.setOnClickListener(v->finish());root.addView(cancel);setContentView(root);}
 public void surfaceCreated(SurfaceHolder h){try{camera=Camera.open();Camera.Parameters p=camera.getParameters();if(p.getSupportedFocusModes().contains(Camera.Parameters.FOCUS_MODE_CONTINUOUS_PICTURE))p.setFocusMode(Camera.Parameters.FOCUS_MODE_CONTINUOUS_PICTURE);camera.setParameters(p);camera.setDisplayOrientation(90);camera.setPreviewDisplay(h);camera.setPreviewCallback(this);camera.startPreview();}catch(Exception e){new AlertDialog.Builder(this).setMessage("无法打开相机，请返回后粘贴网关地址。").setPositiveButton("返回",(d,w)->finish()).show();}}
 public void surfaceChanged(SurfaceHolder h,int f,int w,int ht){}
 public void surfaceDestroyed(SurfaceHolder h){release();}
 public void onPreviewFrame(byte[] data,Camera c){if(done||System.currentTimeMillis()-last<500)return;last=System.currentTimeMillis();try{Camera.Size size=c.getParameters().getPreviewSize();PlanarYUVLuminanceSource source=new PlanarYUVLuminanceSource(data,size.width,size.height,0,0,size.width,size.height,false);Map<DecodeHintType,Object> hints=new EnumMap<>(DecodeHintType.class);hints.put(DecodeHintType.POSSIBLE_FORMATS,Collections.singletonList(BarcodeFormat.QR_CODE));Result result=new MultiFormatReader().decode(new BinaryBitmap(new HybridBinarizer(source)),hints);done=true;setResult(RESULT_OK,new Intent().putExtra("code",result.getText()));finish();}catch(Exception ignored){}}
 private void release(){if(camera!=null){camera.setPreviewCallback(null);camera.stopPreview();camera.release();camera=null;}}
 @Override protected void onPause(){release();super.onPause();}
}
