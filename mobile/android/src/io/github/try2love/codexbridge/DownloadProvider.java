package io.github.try2love.codexbridge;

import android.content.*;
import android.database.Cursor;
import android.database.MatrixCursor;
import android.net.Uri;
import android.os.ParcelFileDescriptor;
import android.provider.OpenableColumns;
import android.webkit.MimeTypeMap;
import java.io.*;
import java.nio.file.Files;
import java.util.*;

/** Read-only copies keep external viewers independent of the active download. */
public final class DownloadProvider extends ContentProvider {
 private static final long RETENTION=24L*60*60*1000;
 static Uri prepare(Context context,ArtifactDownload.Result result)throws IOException {
  File root=new File(context.getCacheDir(),"opened-downloads");
  if(!root.isDirectory()&&!root.mkdirs())throw new IOException();
  File[] folders=root.listFiles();
  if(folders!=null)for(File folder:folders){
   if(folder.lastModified()<System.currentTimeMillis()-RETENTION){
    File[] files=folder.listFiles();if(files!=null)for(File file:files)file.delete();folder.delete();
   }
  }
  String key=UUID.nameUUIDFromBytes(result.file.getAbsolutePath().getBytes(java.nio.charset.StandardCharsets.UTF_8)).toString();
  File folder=new File(root,key);if(!folder.isDirectory()&&!folder.mkdir())throw new IOException();
  File copy=new File(folder,result.name);
  try{if(!copy.isFile())Files.copy(result.file.toPath(),copy.toPath());folder.setLastModified(System.currentTimeMillis());}catch(IOException e){copy.delete();folder.delete();throw e;}
  return new Uri.Builder().scheme("content").authority(context.getPackageName()+".downloads").appendPath(folder.getName()).appendPath(copy.getName()).build();
 }
 private File file(Uri uri)throws FileNotFoundException {
  List<String> path=uri.getPathSegments();
  if(!"content".equals(uri.getScheme())||!(getContext().getPackageName()+".downloads").equals(uri.getAuthority())||path.size()!=2||!path.get(0).matches("[a-f0-9-]{36}"))throw new FileNotFoundException();
  try{
   File folder=new File(getContext().getCacheDir(),"opened-downloads/"+path.get(0)).getCanonicalFile();
   File file=new File(folder,path.get(1)).getCanonicalFile();
   if(!folder.equals(file.getParentFile())||!file.isFile())throw new FileNotFoundException();return file;
  }catch(IOException e){throw new FileNotFoundException();}
 }
 @Override public boolean onCreate(){return true;}
 @Override public String getType(Uri uri){
  try{String name=file(uri).getName();String extension=name.substring(name.lastIndexOf('.')+1).toLowerCase(Locale.ROOT);
   String type=MimeTypeMap.getSingleton().getMimeTypeFromExtension(extension);return type==null?"application/octet-stream":type;
  }catch(FileNotFoundException e){return "application/octet-stream";}
 }
 @Override public Cursor query(Uri uri,String[] projection,String selection,String[] args,String order){
  try{File file=file(uri);String[] columns=projection==null?new String[]{OpenableColumns.DISPLAY_NAME,OpenableColumns.SIZE}:projection;
   MatrixCursor cursor=new MatrixCursor(columns,1);Object[] row=new Object[columns.length];
   for(int i=0;i<columns.length;i++)row[i]=OpenableColumns.DISPLAY_NAME.equals(columns[i])?file.getName():OpenableColumns.SIZE.equals(columns[i])?file.length():null;
   cursor.addRow(row);return cursor;
  }catch(FileNotFoundException e){return null;}
 }
 @Override public ParcelFileDescriptor openFile(Uri uri,String mode)throws FileNotFoundException {
  if(!"r".equals(mode))throw new FileNotFoundException();return ParcelFileDescriptor.open(file(uri),ParcelFileDescriptor.MODE_READ_ONLY);
 }
 @Override public Uri insert(Uri uri,ContentValues values){throw new UnsupportedOperationException();}
 @Override public int update(Uri uri,ContentValues values,String selection,String[] args){throw new UnsupportedOperationException();}
 @Override public int delete(Uri uri,String selection,String[] args){throw new UnsupportedOperationException();}
}
