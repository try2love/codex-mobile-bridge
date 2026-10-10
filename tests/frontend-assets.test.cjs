'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const root=path.resolve(__dirname,'..'),assets=require('../web/assets.json'),pkg=require('../package.json');
const references=html=>[...html.matchAll(/(?:src|href)="([^"]+)"/g)].map(match=>match[1]).filter(value=>!value.startsWith('#')&&!/^[a-z]+:/i.test(value));

test('every public web entry resolves through the static asset allowlist',()=>{
  for(const [url,file] of Object.entries(assets)){
    assert.ok(url.startsWith('/')&&!url.includes('?'),url);
    for(const part of [].concat(file)){
      assert.ok(!path.isAbsolute(part)&&!part.split('/').includes('..'),part);
      assert.ok(fs.statSync(path.join(root,'web',part)).isFile(),url);
    }
  }
  const html=fs.readFileSync(path.join(root,'web/index.html'),'utf8');
  for(const ref of references(html)){
    const url=new URL(ref,'http://gateway.invalid/').pathname;
    const file=assets[url]||(url.startsWith('/vendor/')?url.slice(1):null);
    assert.ok(file,'Unmapped public resource: '+url);
    for(const part of [].concat(file))assert.ok(fs.statSync(path.join(root,'web',part)).isFile(),url);
  }
  assert.equal(assets['/'],assets['/index.html']);
  assert.ok(assets['/app.js']);assert.ok(assets['/style.css']);
});

test('desktop local resources exist and are included in the application package',()=>{
  const html=fs.readFileSync(path.join(root,'desktop/index.html'),'utf8');
  for(const ref of references(html)){
    const absolute=path.resolve(root,'desktop',ref.split('?')[0]),relative=path.relative(root,absolute).split(path.sep).join('/');
    assert.ok(fs.statSync(absolute).isFile(),ref);
    assert.ok(pkg.build.files.some(pattern=>pattern.endsWith('/**')?relative.startsWith(pattern.slice(0,-2)):relative===pattern),'Not packaged: '+relative);
  }
  const entry=fs.readFileSync(path.join(root,'desktop/main.cjs'),'utf8');
  for(const [,file] of entry.matchAll(/require\(['"](\.[^'"]+)['"]\)/g))assert.ok(fs.statSync(path.resolve(root,'desktop',file)).isFile(),file);
  assert.ok(fs.statSync(path.join(root,'desktop/features/updates/update-public-key.pem')).isFile());
});
