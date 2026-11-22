// Export both approved SVG appearances. Run with npm run icons:desktop.
'use strict';
const fs=require('node:fs'),path=require('node:path');
const root=path.join(__dirname,'..');
const pngTargets=[['assets/icon.png',1024],['desktop/assets/icon.png',1024],
  ['web/icon.png',512],['web/icon-192.png',192],['web/icon-ui.png',144],
  ['web/icon-favicon.png',32],['web/icon-touch.png',180],['site/assets/icon.png',512]];
const icoSizes=[16,20,24,32,40,48,64,128,256];

function ico(png){
  const images=icoSizes.map(png),header=Buffer.alloc(6+16*icoSizes.length);
  header.writeUInt16LE(1,2);header.writeUInt16LE(icoSizes.length,4);
  let offset=header.length;
  images.forEach((image,index)=>{
    const entry=6+16*index,size=icoSizes[index];
    header[entry]=header[entry+1]=size===256?0:size;
    header.writeUInt16LE(1,entry+4);header.writeUInt16LE(32,entry+6);
    header.writeUInt32LE(image.length,entry+8);header.writeUInt32LE(offset,entry+12);
    offset+=image.length;
  });
  return Buffer.concat([header,...images]);
}
function write(file,bytes){const target=path.join(root,file);fs.mkdirSync(path.dirname(target),{recursive:true});fs.writeFileSync(target,bytes);}
async function render(window,nativeImage,file,size,transform=source=>source){
  const source='data:image/svg+xml;base64,'+Buffer.from(transform(fs.readFileSync(path.join(root,file),'utf8'))).toString('base64');
  const png=await window.webContents.executeJavaScript(`(async()=>{
    const image=new Image();image.src=${JSON.stringify(source)};await image.decode();
    const canvas=document.createElement('canvas');canvas.width=canvas.height=${size};
    canvas.getContext('2d').drawImage(image,0,0,${size},${size});
    return canvas.toDataURL('image/png').split(',')[1];
  })()`);
  return nativeImage.createFromBuffer(Buffer.from(png,'base64'));
}
function mobileArtwork(svg,theme,foreground=false){
  // The OS applies the outer icon mask; keep the approved terminal/phone art.
  const tile=new RegExp(`<rect x="32" y="32" width="960" height="960" rx="218" fill="url\\(#cmb-${theme}-tile\\)"/>`);
  const outline=/<rect x="33" y="33" width="958" height="958" rx="217"[^>]*\/>/;
  if(!tile.test(svg)||!outline.test(svg))throw Error('Approved icon tile structure changed.');
  svg=svg.replace(tile,'').replace(outline,'');
  if(foreground)return svg.replace('translate(71.68 71.68) scale(0.86)','translate(143.36 143.36) scale(0.72)');
  return svg.replace('</defs>',`</defs><rect width="1024" height="1024" fill="url(#cmb-${theme}-tile)"/>`);
}
async function mobile(window,nativeImage){
  for(const theme of ['light','dark']){
    const file=`assets/icon-${theme}.svg`;
    const square=await render(window,nativeImage,file,1024,svg=>mobileArtwork(svg,theme));
    const png=size=>square.resize({width:size,height:size,quality:'best'}).toPNG();
    write(`mobile/android/res/drawable${theme==='dark'?'-night':''}-nodpi/ic_bridge.png`,png(512));
    write(`mobile/android/res/drawable${theme==='dark'?'-night':''}-nodpi/ic_launcher_art.png`,
      (await render(window,nativeImage,file,432,svg=>mobileArtwork(svg,theme,true))).toPNG());
    const icons='mobile/ios/BridgePreview/Assets.xcassets/AppIcon.appiconset';
    write(`${icons}/icon-1024${theme==='dark'?'-dark':''}.png`,png(1024));
    if(theme==='light'){
      for(const size of [20,29,40,58,60,76,80,87,120,152,167,180])write(`${icons}/icon-${size}.png`,png(size));
      for(const [name,size] of [['60x60@2x',120],['60x60@3x',180],['76x76',76],['76x76@2x',152],['83x83@2x',167]])write(`mobile/ios/BridgePreview/AppIcon${name}.png`,png(size));
    }
  }
}
async function build(){
  const {app,BrowserWindow,nativeImage}=require('electron');
  // An icon export never shares the gateway's profile or starts its workers.
  const userData=path.join(root,'.tmp/icon-export');
  fs.mkdirSync(userData,{recursive:true});app.setPath('userData',userData);
  app.disableHardwareAcceleration();
  await app.whenReady();
  const window=new BrowserWindow({show:false,webPreferences:{sandbox:true,contextIsolation:true,nodeIntegration:false,backgroundThrottling:false}});
  try{
    await window.loadURL('data:text/html,<html><body></body></html>');
    if(process.argv.includes('--mobile-only')){await mobile(window,nativeImage);console.log('Exported Android adaptive layers and iOS light/dark icons.');return;}
    for(const theme of ['light','dark']){
      console.log(`Rendering ${theme} artwork...`);
      const source=await render(window,nativeImage,`assets/icon-${theme}.svg`,1024);
      const png=size=>source.resize({width:size,height:size,quality:'best'}).toPNG();
      for(const [file,size] of pngTargets)write(theme==='dark'?file.replace('.png','-dark.png'):file,png(size));
      write(`desktop/assets/icon${theme==='dark'?'-dark':''}.ico`,ico(png));
    }
    for(const [file,size] of [['desktop/assets/trayTemplate.png',18],['desktop/assets/trayTemplate@2x.png',36]]){
      write(file,(await render(window,nativeImage,'assets/tray-template.svg',size)).toPNG());
    }
    const manifest=JSON.parse(fs.readFileSync(path.join(root,'web/manifest.webmanifest'),'utf8'));
    const darkManifest={...manifest,background_color:'#17181c',theme_color:'#17181c',
      icons:manifest.icons.map(icon=>({...icon,src:icon.src.replace('.png','-dark.png')}))};
    write('web/manifest-dark.webmanifest',JSON.stringify(darkManifest)+'\n');
    write('site/assets/brand-icons.js',fs.readFileSync(path.join(root,'web/shared/brand-icons.js')));
    console.log('Exported light/dark PNGs, Windows ICOs, macOS tray templates, and web install assets.');
  }finally{window.destroy();app.quit();}
}
// Electron loads its entry through its own bootstrap module, not Node's require.main.
if(process.versions.electron&&process.type==='browser')build().catch(error=>{console.error(error);require('electron').app.exit(1);});
module.exports={pngTargets,icoSizes,ico};
