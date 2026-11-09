package io.github.try2love.codexbridge;

import android.content.*;
import android.database.Cursor;
import android.database.MatrixCursor;
import android.net.Uri;
import android.os.ParcelFileDescriptor;
import android.provider.OpenableColumns;
import java.io.*;
import java.util.UUID;

/** Only temporary, explicitly granted camera outputs are writable. */
public final class CameraProvider extends ContentProvider {
 static Uri prepare(Context context)throws IOException {
  File root=new File(context.getCacheDir(),"camera-uploads");
  if(!root.isDirectory()&&!root.mkdir())throw new IOException();
  File[] old=root.listFiles();
  if(old!=null)for(File file:old)if(file.lastModified()<System.currentTimeMillis()-86400000L)file.delete();
  File photo=new File(root,UUID.randomUUID()+".jpg");
  if(!photo.createNewFile())throw new IOException();
  return new Uri.Builder().scheme("content").authority(context.getPackageName()+".camera").appendPath(photo.getName()).build();
 }
 static void discard(Context context,Uri uri){
  if(uri==null)return;
  try{resolve(context,uri).delete();}catch(IOException ignored){}
 }
 private static File resolve(Context context,Uri uri)throws IOException {
  if(!"content".equals(uri.getScheme())||!(context.getPackageName()+".camera").equals(uri.getAuthority())||uri.getPathSegments().size()!=1||!uri.getLastPathSegment().matches("[a-f0-9-]{36}\\.jpg"))throw new FileNotFoundException();
  File root=new File(context.getCacheDir(),"camera-uploads").getCanonicalFile();
  File file=new File(root,uri.getLastPathSegment()).getCanonicalFile();
  if(!root.equals(file.getParentFile())||!file.isFile())throw new FileNotFoundException();
  return file;
 }
 @Override public boolean onCreate(){return true;}
 @Override public String getType(Uri uri){return "image/jpeg";}
 @Override public Cursor query(Uri uri,String[] projection,String selection,String[] args,String order){
  try{
   File photo=resolve(getContext(),uri);String[] columns=projection==null?new String[]{OpenableColumns.DISPLAY_NAME,OpenableColumns.SIZE}:projection;
   MatrixCursor cursor=new MatrixCursor(columns,1);Object[] row=new Object[columns.length];
   for(int i=0;i<columns.length;i++)row[i]=OpenableColumns.DISPLAY_NAME.equals(columns[i])?photo.getName():OpenableColumns.SIZE.equals(columns[i])?photo.length():null;
   cursor.addRow(row);return cursor;
  }catch(IOException e){return null;}
 }
 @Override public ParcelFileDescriptor openFile(Uri uri,String mode)throws FileNotFoundException {
  try{
   File photo=resolve(getContext(),uri);
   if("r".equals(mode))return ParcelFileDescriptor.open(photo,ParcelFileDescriptor.MODE_READ_ONLY);
   if("w".equals(mode)||"wt".equals(mode)||"rw".equals(mode)||"rwt".equals(mode))return ParcelFileDescriptor.open(photo,ParcelFileDescriptor.parseMode(mode));
  }catch(IOException ignored){}
  throw new FileNotFoundException();
 }
 @Override public Uri insert(Uri uri,ContentValues values){throw new UnsupportedOperationException();}
 @Override public int update(Uri uri,ContentValues values,String selection,String[] args){throw new UnsupportedOperationException();}
 @Override public int delete(Uri uri,String selection,String[] args){throw new UnsupportedOperationException();}
}
