const assert = require('node:assert/strict');
const {flatOf,diffFlat,requestGate} = require('../static/board-edit.js');
const before = flatOf({status:'uploaded',id:'a',row:3,ref_urls:'',ref_channels:'\nChannel\n',platforms:{youtube:{checked:true,url:'https://x'}},video:{title:'old'}});
const after = {...before,title:'new',status:'candidate'};
assert.deepEqual(diffFlat(before,after),{title:'new',status:'candidate'});
assert.equal(before.ref_channels,'\nChannel\n');
assert.equal(after.youtube_on,true); // Locking the platform does not erase its data.
assert.deepEqual(diffFlat(before,{...before}),{});
const gate=requestGate();
const old=gate.read(), latest=gate.read();
assert.equal(gate.accepts(old),false);
assert.equal(gate.accepts(latest),true);
gate.begin();assert.equal(gate.accepts(latest),false);
const during=gate.read();assert.equal(gate.accepts(during),false);
gate.end();assert.equal(gate.accepts(during),false);
assert.equal(gate.accepts(gate.read()),true);
console.log('Board form data and out-of-order read checks passed.');

// Exercise the actual template functions with a minimal DOM adapter, without a browser.
const fs = require('node:fs'), vm = require('node:vm');
const template = fs.readFileSync('templates/shorts.html','utf8');
const context = vm.createContext({assert, console, flatOf, STATUS_ORDER:['candidate','uploaded'],STATUSES:{candidate:'후보',uploaded:'완료'},
  PLAT_ORDER:['youtube'],PLATFORMS:{youtube:'유튜브'},platIcon:()=>''});
vm.runInContext(template.match(/  const esc = .*;/)[0] + '\n' +
  template.slice(template.indexOf('  function fieldEl('),template.indexOf('  function updateForm(')),context);
vm.runInContext(`
const html = detailHtml({id:'stable',status:'candidate',dish_title:'<script>bad</script>',ref_channels:'\\nChannel\\n',platforms:{youtube:{checked:true,url:'https://x'}}});
for (const name of ['status','dish','src_url','src_title','src_channel','src_desc','src_pinned','ref_urls','ref_channels','youtube_on','youtube_url','title','memo','desc','pinned'])
  assert.ok(html.includes('data-f="'+name+'"'),name);
assert.ok(!html.includes('<script>bad'));
assert.ok(html.includes('\\nChannel\\n</textarea>'));
let formUpdates=0;
const root={children:[],insertBefore(node,anchor){const i=this.children.indexOf(anchor);this.children.splice(i<0?this.children.length:i,0,node);}};
function node(name,detail=false){return {name,dataset:{id:detail?'stable':name},classList:{contains:()=>detail},remove(){assert.notEqual(this,kept);root.children.splice(root.children.indexOf(this),1);}};}
const kept=node('kept',true); kept.value='unsaved';kept.selectionStart=3;
root.children=[node('old-summary'),kept,node('other')];
let edit={id:'stable'};
const $=()=>root, detailNode=()=>kept, updateForm=()=>formUpdates++;
const desired=[node('new-summary'),node('discard-new-detail',true),node('new-other')];
const document={createElement:()=>({content:{querySelector:()=>({children:desired})}})};
`,context);
vm.runInContext(template.slice(template.indexOf('  function swapRows('),template.indexOf('  function beginWrite(')),context);
vm.runInContext(`
swapRows('rendered rows');
assert.deepEqual(root.children.map(n=>n.name),['new-summary','kept','new-other']);
assert.equal(kept.value,'unsaved');assert.equal(kept.selectionStart,3);assert.equal(formUpdates,1);
console.log('Board form markup and edit-node preservation checks passed.');
`,context);
