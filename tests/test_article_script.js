const vm=require('node:vm');
const context=require('./test_upload_script.js');
vm.runInContext(`
function articleReset(){reset();sheet.rows[2][COL.status-1]=UPLOADED;for(const k of ARTICLE_FIELDS)sheet.rows[2][COL[k]-1]='';}
function articleBody(){return {action:'article_fill_missing',sheetId:'sheet',itemId:'item',requestId:'article-request-123456789',revision:articleRevision(boardReadRow(sheet,3)),fields:{title:'볶음밥',desc:'밥을 볶는다',youtubeUrl:'https://www.youtube.com/watch?v=abcdefghijk'}};}
articleReset();let ab=articleBody();let ar=articleAction({...ab,action:'article_get'});
assert.ok(ar.ok);assert.equal(ar.article_version,1);assert.equal(ar.revision,ab.revision);assert.equal(batchCount,0);
// Row movement and unrelated edits preserve identity and do not conflict.
sheet.rows.splice(2,0,Array(LAST_COL).fill(''));sheet.rows[3][COL.memo-1]='keep new memo';
ar=articleAction(ab);assert.ok(ar.ok);assert.equal(ar.row,4);assert.equal(ar.cells.title,'볶음밥');assert.equal(ar.cells.memo,'keep new memo');assert.equal(ar.cells.status,UPLOADED);assert.equal(batchCount,1);
ar=articleAction(ab);assert.ok(ar.submitted);assert.equal(batchCount,1);
assert.equal(articleAction({...ab,fields:{...ab.fields,title:'other'}}).error,'request_mismatch');
articleReset();ab=articleBody();sheet.rows[2][COL.youtubeUrl-1]='https://youtu.be/lmnopqrstuv';assert.equal(articleAction(ab).error,'conflict');assert.equal(batchCount,0);
articleReset();ab=articleBody();sheet.rows[2][COL.status-1]=STATUSES[0].label;assert.equal(articleAction(ab).error,'article_not_uploaded');
articleReset();ab=articleBody();sheet.rows[2][COL.title-1]='already exists';ab.revision=articleRevision(boardReadRow(sheet,3));assert.equal(articleAction(ab).error,'article_not_missing');
articleReset();ab=articleBody();ab.fields.youtubeUrl='https://evil.test/video';assert.equal(articleAction(ab).error,'bad_fields');
articleReset();ab=articleBody();ab.fields.status=UPLOADED;assert.equal(articleAction(ab).error,'bad_fields');
articleReset();ab=articleBody();mode='timeoutAfter';ar=articleAction(ab);assert.ok(ar.submitted);assert.equal(batchCount,1);mode='';assert.ok(articleAction(ab).submitted);assert.equal(batchCount,1);
articleReset();ab=articleBody();mode='timeoutBefore';assert.equal(articleAction(ab).error,'commit_unknown');assert.equal(boardReadRow(sheet,3).title,'');mode='';assert.ok(articleAction(ab).submitted);assert.equal(batchCount,2);
articleReset();ab=articleBody();mode='invalid';assert.equal(articleAction(ab).error,'commit_failed');assert.equal(boardReadRow(sheet,3).title,'');assert.equal(receipts.length,0);
articleReset();ab=articleBody();sheet.rows[3][COL.itemId-1]='item';assert.equal(articleAction(ab).error,'row_mismatch');
articleReset();ab=articleBody();locked=true;assert.equal(articleAction(ab).error,'busy');
console.log('Article Apps Script: missing-only fields, published URL revision, moved IDs, atomic receipts and retries passed.');
`,context);
