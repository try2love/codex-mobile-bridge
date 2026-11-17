// Export the approved artwork at the sizes used by the App, phone and website.
'use strict';
const {app,nativeImage}=require('electron');
const fs=require('node:fs'),path=require('node:path');
app.whenReady().then(()=>{
  try{
    const root=path.join(__dirname,'..');
    const source=nativeImage.createFromPath(path.join(root,'assets/icon.png'));
    if(source.isEmpty())throw new Error('Cannot read assets/icon.png');
    const png=size=>source.resize({width:size,height:size,quality:'best'}).toPNG();
    for(const [file,size] of [['desktop/assets/icon.png',1024],['web/icon.png',512],['site/assets/icon.png',512]]){
      const output=path.join(root,file);
      fs.mkdirSync(path.dirname(output),{recursive:true});
      fs.writeFileSync(output,png(size));
    }
    // Multiple representations keep Windows shortcuts and tray icons sharp.
    const sizes=[16,20,24,32,40,48,64,128,256],images=sizes.map(png);
    const header=Buffer.alloc(6+16*sizes.length);
    header.writeUInt16LE(1,2);header.writeUInt16LE(sizes.length,4);
    let offset=header.length;
    images.forEach((image,index)=>{
      const entry=6+16*index,size=sizes[index];
      header[entry]=header[entry+1]=size===256?0:size;
      header.writeUInt16LE(1,entry+4);header.writeUInt16LE(32,entry+6);
      header.writeUInt32LE(image.length,entry+8);header.writeUInt32LE(offset,entry+12);
      offset+=image.length;
    });
    fs.writeFileSync(path.join(root,'desktop/assets/icon.ico'),Buffer.concat([header,...images]));
    app.exit(0);
  }catch(error){console.error(error);app.exit(1);}
});
