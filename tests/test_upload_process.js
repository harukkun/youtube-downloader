const assert=require('node:assert/strict');
const {basicValid,canReach,key,youtubeMeta,youtubeUrl}=require('../static/upload-process.js');
const fields={title:'title',src_url:'https://source',ref_urls:'https://ref',memo:''};
assert.equal(basicValid(fields),true);
assert.equal(basicValid({...fields,title:''}),false);
assert.equal(basicValid({...fields,title:'😀'.repeat(501)}),false);
assert.equal(canReach(0,{}),true);
assert.equal(canReach(1,{}),false);
assert.equal(canReach(2,{item:{},fields}),true);
assert.equal(canReach(3,{item:{},fields,recipeConfirmed:false}),false);
assert.equal(canReach(3,{item:{},fields,recipeConfirmed:true}),true);
assert.equal(canReach(4,{item:{},fields,recipeConfirmed:true,thumb:null}),false);
assert.equal(canReach(4,{item:{},fields,recipeConfirmed:true,thumb:{mode:'existing'}}),true);
assert.notEqual(key('sheetA','id'),key('sheetB','id'));
assert.notEqual(key('sheetA','id'),key('sheetA','other'));
// YouTube snippet limits mirror youtube_upload.service.validate_metadata.
assert.equal(youtubeMeta('제목','설명').ok,true);
assert.equal(youtubeMeta('가'.repeat(100),'').ok,true);
assert.equal(youtubeMeta('가'.repeat(101),'').ok,false);
assert.equal(youtubeMeta('  여러   공백  ','').titleLen,5);
assert.equal(youtubeMeta('','').ok,false);
assert.equal(youtubeMeta('t','한'.repeat(1666)).ok,true);   // 4998 bytes
assert.equal(youtubeMeta('t','한'.repeat(1667)).ok,false);  // 5001 bytes
assert.equal(youtubeMeta('t','a\r\nb').descBytes,3);
assert.equal(youtubeMeta('<b>','').angleBrackets,true);
assert.equal(youtubeUrl('abcdefghijk'),'https://youtu.be/abcdefghijk');
assert.equal(youtubeUrl('bad'),'');
console.log('Upload progression, required inputs, draft identity and YouTube limits passed.');
