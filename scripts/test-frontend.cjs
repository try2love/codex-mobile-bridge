'use strict';
const fs=require('node:fs'),path=require('node:path'),{spawnSync}=require('node:child_process');
const root=path.resolve(__dirname,'..'),files=new Set();

function discover(directory){
  for(const entry of fs.readdirSync(directory,{withFileTypes:true})){
    const file=path.join(directory,entry.name);
    if(entry.isDirectory())discover(file);
    else if(entry.isFile()&&/\.test\.(?:js|cjs|mjs)$/.test(entry.name))files.add(file);
  }
}

discover(path.join(root,'tests'));
discover(path.join(root,'mobile/tests'));
const tests=[...files].sort();
if(!tests.length)throw Error('No frontend tests found.');
console.log(`Running ${tests.length} frontend test files.`);
const result=spawnSync(process.execPath,['--test',...tests],{cwd:root,stdio:'inherit'});
if(result.error)throw result.error;
process.exitCode=result.status??1;
