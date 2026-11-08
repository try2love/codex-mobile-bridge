'use strict';
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
module.exports=function loadCodexEntryPredicates(context){
  const source=fs.readFileSync(path.join(__dirname,'../../web/features/clients/client-navigation.js'),'utf8');
  const predicates=source.match(/^function isCodex(?:Entry|Client)\([^\n]+$/gm);
  if(predicates?.length!==2)throw Error('Expected both Codex entry predicates');
  vm.runInContext(predicates.join('\n'),context);
};
