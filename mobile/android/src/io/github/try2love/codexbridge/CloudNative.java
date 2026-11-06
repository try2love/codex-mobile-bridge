package io.github.try2love.codexbridge;

import android.content.Context;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import android.util.AtomicFile;
import java.io.File;
import java.io.FileOutputStream;
import java.nio.charset.StandardCharsets;
import java.security.KeyStore;
import java.util.Arrays;
import javax.crypto.*;
import javax.crypto.spec.GCMParameterSpec;
import org.json.JSONObject;

/** Loaded only after explicit cloud enrollment or opening an enrolled account. */
final class CloudNative {
    private static final String ALIAS="cmb-cloud-account-v1";
    private final AtomicFile file;
    private final SecretKey key;
    CloudNative(Context context) throws Exception {
        System.loadLibrary("cmb_cloud");System.loadLibrary("cmb_cloud_android");
        initializeTls(context.getApplicationContext());
        file=new AtomicFile(new File(context.getNoBackupFilesDir(),"cloud-account-v1"));
        KeyStore keys=KeyStore.getInstance("AndroidKeyStore");keys.load(null);
        SecretKey prior=(SecretKey)keys.getKey(ALIAS,null);
        if(prior==null){
            if(file.getBaseFile().exists())throw new IllegalStateException("云登录密钥不可用");
            KeyGenerator g=KeyGenerator.getInstance("AES","AndroidKeyStore");
            g.init(new KeyGenParameterSpec.Builder(ALIAS,KeyProperties.PURPOSE_ENCRYPT|KeyProperties.PURPOSE_DECRYPT)
                    .setBlockModes("GCM").setEncryptionPaddings("NoPadding").build());
            prior=g.generateKey();
        }
        key=prior;
    }
    private native void initializeTls(Context app);
    private native byte[] dispatch(byte[] request,byte[] saved);
    private boolean saveState(byte[] value){
        FileOutputStream out=null;
        try{
            Cipher c=Cipher.getInstance("AES/GCM/NoPadding");c.init(Cipher.ENCRYPT_MODE,key);
            byte[] iv=c.getIV();if(iv.length!=12)return false;
            out=file.startWrite();out.write(iv);out.write(c.doFinal(value));file.finishWrite(out);return true;
        }catch(Exception e){if(out!=null)file.failWrite(out);return false;}
        finally{Arrays.fill(value,(byte)0);}
    }
    JSONObject run(JSONObject cmd)throws Exception{
        byte[] state=new byte[0];
        if(file.getBaseFile().exists()){
            byte[] sealed=file.readFully();
            Cipher c=Cipher.getInstance("AES/GCM/NoPadding");c.init(Cipher.DECRYPT_MODE,key,new GCMParameterSpec(128,sealed,0,12));
            state=c.doFinal(sealed,12,sealed.length-12);
        }
        byte[] input=cmd.toString().getBytes(StandardCharsets.UTF_8);
        try{
            byte[] result=dispatch(input,state);
            if(result==null)throw new IllegalStateException("云服务不可用");
            JSONObject response=new JSONObject(new String(result,StandardCharsets.UTF_8));
            if(!response.optBoolean("ok"))throw new IllegalStateException(response.optString("error","云操作未完成"));
            return response.getJSONObject("result");
        }finally{Arrays.fill(input,(byte)0);Arrays.fill(state,(byte)0);}
    }
}
