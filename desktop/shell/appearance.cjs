"use strict";
const fs=require('node:fs'),path=require('node:path'),{randomUUID}=require('node:crypto');
const themes=new Set(['system','light','dark']);
function readAppearance(dataDir){
  try{const value=JSON.parse(fs.readFileSync(path.join(dataDir,'desktop-appearance.json'),'utf8'));return themes.has(value.theme)?value.theme:'system';}
  catch{return 'system';}
}
function saveAppearance(dataDir,theme){
  if(!themes.has(theme))throw Error('主题设置无效。');
  fs.mkdirSync(dataDir,{recursive:true});
  const file=path.join(dataDir,'desktop-appearance.json'),temporary=file+'.'+randomUUID()+'.tmp';
  try{fs.writeFileSync(temporary,JSON.stringify({theme}),{mode:0o600,flag:'wx'});fs.renameSync(temporary,file);}
  finally{fs.rmSync(temporary,{force:true});}
  return theme;
}
module.exports={readAppearance,saveAppearance};
