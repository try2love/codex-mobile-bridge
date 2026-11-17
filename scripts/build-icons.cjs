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
function write(file,bytes){fs.writeFileSync(path.join(root,file),bytes);}
async function render(window,nativeImage,file,size){
  const source='data:image/svg+xml;base64,'+fs.readFileSync(path.join(root,file)).toString('base64');
  const png=await window.webContents.executeJavaScript(`(async()=>{
    const image=new Image();image.src=${JSON.stringify(source)};await image.decode();
    const canvas=document.createElement('canvas');canvas.width=canvas.height=${size};
    canvas.getContext('2d').drawImage(image,0,0,${size},${size});
    return canvas.toDataURL('image/png').split(',')[1];
  })()`);
  return nativeImage.createFromBuffer(Buffer.from(png,'base64'));
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
