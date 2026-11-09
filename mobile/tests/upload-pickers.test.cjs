'use strict';
// Source contracts only; Android/iOS builds and device permission flows are separate.
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const read=file=>fs.readFileSync(path.join(__dirname,'../..',file),'utf8');

test('both APK variants expose only explicitly granted temporary camera outputs',()=>{
  for(const variant of ['android','android-push'])assert.match(read('mobile/'+variant+'/AndroidManifest.xml'),/<provider[^>]*CameraProvider[^>]*android:exported="false"[^>]*android:grantUriPermissions="true"/);
  const source=read('mobile/android/src/io/github/try2love/codexbridge/CameraProvider.java');
  assert.match(source,/camera-uploads/);assert.match(source,/getCanonicalFile\(\)/);assert.match(source,/root\.equals\(file\.getParentFile\(\)\)/);
  assert.match(source,/UUID\.randomUUID\(\)/);assert.match(source,/pathSegments|PathSegments/);
});

test('Android distinguishes native photo picker, capture and document intents',()=>{
  const source=read('mobile/android/src/io/github/try2love/codexbridge/MainActivity.java');
  assert.match(source,/photos&&params\.isCaptureEnabled\(\)/);
  assert.match(source,/MediaStore\.ACTION_PICK_IMAGES/);assert.match(source,/MediaStore\.EXTRA_PICK_IMAGES_MAX/);
  assert.match(source,/MediaStore\.ACTION_IMAGE_CAPTURE/);assert.match(source,/MediaStore\.EXTRA_OUTPUT,cameraUpload/);
  assert.match(source,/photos\?Intent\.ACTION_GET_CONTENT:Intent\.ACTION_OPEN_DOCUMENT/);
  assert.match(source,/code==27/);assert.match(source,/fileGeneration!=generation/);
  assert.match(source,/myUid\(\)&&!uri\.equals\(captured\)/);
  assert.match(source,/revokeUriPermission\(captured/);assert.match(source,/CameraProvider\.discard\(this,captured\)/);
});

test('iOS uses image-only native photo and camera pickers with scoped completion',()=>{
  const source=read('mobile/ios/BridgePreview/App.swift');
  assert.match(source,/source === 'photos' && accept/);
  assert.match(source,/\["photos", "files", "photos-images", "camera"\]\.contains\(value\)/);
  assert.match(source,/config\.filter = \.images/);assert.match(source,/UIImagePickerController\(\); picker\.sourceType = \.camera/);
  assert.match(source,/AVCaptureDevice\.requestAccess\(for: \.video\)/);
  assert.match(source,/imagePickerControllerDidCancel/);assert.match(source,/ticket == self\.uploadTicket/);
  assert.match(source,/DispatchQueue\.global\(qos: \.userInitiated\)/);
  assert.match(read('mobile/ios/BridgePreview/Info.plist'),/拍照添加到聊天/);
});
