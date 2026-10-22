const assert = require('node:assert/strict');
const {CookBackfillQueue} = require('../static/cook-backfill.js');
(async () => {
  const posts = []; let fail = true;
  const queue = new CookBackfillQueue(async (method, url, body) => {
    if (method === 'GET') return {ok:true,connection:'one',items:[{itemId:'a',title:'A',revision:'rev'},{itemId:'b',title:'B',revision:'rev'}],errors:[],skipped:3,nextCursor:null};
    posts.push(url); assert.equal(body.connection,'one');
    if (url.endsWith('/a') && fail) throw Error('AI failed');
    return {ok:true,saved:true};
  });
  await queue.scan(); assert.equal(posts.length,0);
  await queue.run(); assert.deepEqual(queue.items.map(i=>i.status),['failed','saved']);
  fail=false; await queue.run(true); assert.deepEqual(queue.items.map(i=>i.status),['saved','saved']);
  assert.equal(posts.filter(url=>url.endsWith('/b')).length,1);
  // Stop after the active item, then resume only pending items.
  await queue.scan(); queue.changed=()=>{if(queue.items[0]?.status==='working')queue.stop=true;};
  await queue.run(); assert.deepEqual(queue.items.map(i=>i.status),['saved','pending']);
  queue.changed=()=>{};await queue.run();assert.deepEqual(queue.items.map(i=>i.status),['saved','saved']);
  // A failed second page must not leave a partial batch that looks complete.
  let pages=0;
  queue.request=async()=>{if(pages++)throw Error('offline');return {connection:'one',items:[{itemId:'x'}],nextCursor:53};};
  await assert.rejects(queue.scan(),/offline/);assert.equal(queue.items.length,0);assert.equal(queue.busy,false);
  queue.request=async(method,url)=>{
    assert.ok(url.includes('cursor=53'));
    return {connection:'one',items:[{itemId:'z'}],nextCursor:null};
  };
  await queue.scan();assert.deepEqual(queue.items.map(i=>i.itemId),['x','z']);
  assert.equal(queue.scanProgress,null);
  // Changed connection stops further writes, while remaining items stay pending.
  queue.items=[{itemId:'x',status:'pending'},{itemId:'y',status:'pending'}];
  queue.request=async()=>{const error=Error('changed');error.code='connection_changed';throw error;};
  await queue.run();assert.deepEqual(queue.items.map(i=>i.status),['failed','pending']);
  console.log('Backfill queue preview, retry-only-failures, stop/resume and connection guards passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
