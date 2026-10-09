'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const {defaults,normalize,colors}=require('../web/features/settings/presentation.js');

test('invalid or older saved appearance values keep the page readable',()=>{
  for(const value of [null,42,'bad',{}, {theme:'unknown',accent:'url(external)',fontSize:100,codeSize:-1,density:'tiny'}])assert.deepEqual(normalize(value),defaults);
  assert.deepEqual(normalize({theme:'dark',accent:'#123ABC',fontSize:20,codeSize:17,density:'compact'}),{...defaults,theme:'dark',accent:'#123ABC',fontSize:20,codeSize:17,density:'compact'});
  assert.equal(normalize({showReasoning:false,showProcess:false}).showReasoning,false);
  assert.equal(normalize({showReasoning:false,showProcess:false}).showProcess,false);
  assert.deepEqual(normalize({showReasoning:'false',showProcess:null}),defaults);
  assert.equal(normalize({showFileThumbnails:false}).showFileThumbnails,false);
  assert.equal(normalize({showComputer:false}).showComputer,false);
  assert.equal(normalize({showComputer:'false'}).showComputer,true);
  assert.equal(normalize({showFileThumbnails:'false'}).showFileThumbnails,true);
});

function luminance(hex){
  const channels=hex.slice(1).match(/../g).map(x=>parseInt(x,16)/255).map(x=>x<=.04045?x/12.92:((x+.055)/1.055)**2.4);
  return channels[0]*.2126+channels[1]*.7152+channels[2]*.0722;
}
function contrast(a,b){const x=luminance(a),y=luminance(b);return (Math.max(x,y)+.05)/(Math.min(x,y)+.05);}

test('custom accent colors retain readable text in both themes',()=>{
  for(const accent of ['#ffffff','#000000','#ffff00','#00ff00','#4d6bfe','#24836b','#9565d6','#d77b37']){
    for(const dark of [false,true]){
      const palette=colors(accent,dark);
      assert.ok(contrast(palette.text,dark?'#17181c':'#ffffff')>=4.5,accent+' link contrast');
      assert.ok(contrast(accent,palette.ink)>=4.5,accent+' button contrast');
    }
  }
});

test('appearance reset returns to defaults without retaining a prior override',()=>{
  const previous=normalize({theme:'dark',accent:'#abcdef',fontSize:20,codeSize:17,density:'relaxed'});
  assert.deepEqual(normalize({...previous,...defaults}),defaults);
});
