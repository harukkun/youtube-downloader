'use strict';
// The state machine and persistence are independent of the recipe/thumbnail editors.
const UploadProcessData = (() => {
  function basicValid(fields) { return ['title','src_url','ref_urls'].every(k => typeof fields[k] === 'string' && fields[k].trim()) &&
    Object.entries({title:500,src_url:2000,ref_urls:5000,memo:5000}).every(([k,n]) => Array.from(fields[k] || '').length <= n); }
  function canReach(step, state) { return step === 0 || !!state.item && (step <= 1 || basicValid(state.fields)) &&
    (step <= 2 || state.recipeConfirmed) && (step <= 3 || !!state.thumb?.mode); }
  function key(connection, itemId) { return connection + ':' + itemId; }
  function open() {
    return new Promise((resolve,reject) => {
      const req = indexedDB.open('youtube-upload-process',1);
      req.onupgradeneeded = () => { req.result.createObjectStore('drafts'); req.result.createObjectStore('meta'); };
      req.onsuccess = () => resolve(req.result); req.onerror = () => reject(req.error);
      req.onblocked = () => reject(new Error('다른 탭이 임시 저장소를 사용 중입니다.'));
    });
  }
  async function operation(mode, callback) {
    const db = await open();
    return new Promise((resolve,reject) => {
      const tx = db.transaction(['drafts','meta'],mode); let result;
      tx.oncomplete = () => { db.close(); resolve(result?.result); };
      tx.onerror = tx.onabort = () => { db.close(); reject(tx.error || new Error('초안 저장 실패')); };
      try { result = callback(tx.objectStore('drafts'),tx.objectStore('meta')); }
      catch(e) { tx.abort(); db.close(); reject(e); }
    });
  }
  const store = {
    get:(c,id) => operation('readonly',d=>d.get(key(c,id))),
    last:c => operation('readonly',(_,m)=>m.get(c)),
    save:state => operation('readwrite',(d,m)=>{d.put(state,key(state.connection,state.item.item_id));m.put(state.item.item_id,state.connection);}),
    remove:(c,id) => operation('readwrite',(d,m)=>{d.delete(key(c,id)); const req=m.get(c);req.onsuccess=()=>{if(req.result===id)m.delete(c);};})
  };
  return {basicValid,canReach,key,store};
})();
if (typeof module !== 'undefined') module.exports = UploadProcessData;
if (typeof document !== 'undefined') (async () => {
  const $ = id => document.getElementById(id), recipe = window.RecipeEditor, editor = window.ThumbnailEditor;
  const {store,canReach,basicValid} = UploadProcessData;
  let items=[], connection=null, state=null, step=0, switching=false, busy=false, blocked=false, restoring=false;
  let saveSequence=0;
  function setLoading(kind=null) {
    const active=!!kind, submitting=kind==='submit';
    $('processLoadingTitle').textContent=active?(submitting?'현황판에 저장 중…':'영상 정보를 불러오는 중…'):'';
    $('processLoadingDetail').textContent=active?(submitting?'제출한 내용과 썸네일을 저장하고 있습니다. 잠시만 기다려 주세요.':'최신 현황판 정보와 저장된 초안을 확인하고 있습니다.'):'';
    $('processLoading').hidden=!active;
    const main=document.querySelector('main');main.inert=active;main.setAttribute('aria-busy',String(active));
    $('submitProcess').textContent=submitting?'저장 중…':'현황판에 제출';
  }
  let saveQueue=Promise.resolve(), unsaved=false, persistFailed=false, selectedGeneration=0, previewURL=null;
  function message(text, error=false) { $('processMessage').textContent=text; $('processMessage').className='msg '+(error?'err':'ok')+(text?'':' hidden'); }
  function fields() { return {title:$('videoTitle').value,src_url:$('sourceUrl').value,ref_urls:$('referenceUrls').value,memo:$('videoMemo').value}; }
  function fillFields(f) { for(const [id,key] of Object.entries({videoTitle:'title',sourceUrl:'src_url',referenceUrls:'ref_urls',videoMemo:'memo'})) $(id).value=f[key] || ''; }
  async function json(url, options) {
    let response;
    try { response=await fetch(url,options); }
    catch(e) { throw Object.assign(new Error('서버 연결이 끊겼습니다. 저장 결과를 확인해 주세요.'),{code:'commit_unknown'}); }
    const body=await response.json().catch(()=>({error:'서버 응답을 읽지 못했습니다.',code:'commit_unknown'}));
    if(!response.ok || body.error) throw Object.assign(new Error(body.error || '요청에 실패했습니다.'),{code:body.code || 'commit_unknown'});
    return body;
  }
  function snapshot() { return {...state,fields:fields(),recipe:recipe.snapshot(),step,version:1}; }
  function persist() {
    if(!state || restoring) return saveQueue;
    const saved=snapshot(), sequence=++saveSequence; state=saved;unsaved=true;
    $('draftStatus').textContent='초안 저장 중…';
    saveQueue=saveQueue.catch(()=>{}).then(()=>store.save(saved)).then(()=>{
      if(sequence!==saveSequence)return;
      unsaved=false;persistFailed=false;$('draftStatus').textContent='이 브라우저에 초안을 저장했습니다.';
    }).catch(e=>{persistFailed=true;$('draftStatus').textContent='초안을 보관하지 못했습니다. 페이지를 닫으면 작업이 사라질 수 있습니다.';throw e;});
    saveQueue.catch(()=>{}); return saveQueue;
  }
  function reachable(n) { return canReach(n,{...state,fields:fields()}); }
  function updateNav() {
    document.querySelectorAll('[data-goto]').forEach(b=>{ const n=Number(b.dataset.goto);b.disabled=busy||switching||!!state?.pending||!reachable(n);b.setAttribute('aria-current',n===step?'step':'false'); });
    $('nextStep').disabled=busy||switching||blocked||!!state?.pending||!reachable(step+1);
    $('submitProcess').disabled=busy||switching||blocked||!reachable(4)||!recipe.valid();
    $('confirmRecipe').disabled=!recipe.valid();
    $('recipeState').textContent=state?.recipeConfirmed?'설명글 확정됨':'설명글을 생성·수정한 뒤 확정하세요.';
  }
  function showStep(n) {
    if(!reachable(n)) return;
    step=n;document.querySelectorAll('[data-step]').forEach(el=>el.hidden=Number(el.dataset.step)!==n);
    $('stepActions').hidden=n===0;$('nextStep').hidden=n===4;
    if(n===4) review();updateNav();persist();
  }
  function updateThumbnail() {
    if(previewURL) URL.revokeObjectURL(previewURL);
    previewURL=state?.thumb?.blob ? URL.createObjectURL(state.thumb.blob) : state?.thumb?.url || null;
    $('confirmedThumb').hidden=!previewURL;if(previewURL)$('confirmedThumb').src=previewURL;
    $('thumbState').textContent=previewURL ? '썸네일 확정됨 · 최종 승인 시 저장됩니다.' : '새 썸네일을 확정하거나 기존 썸네일을 선택하세요.';
    const existing=state?.item?.video?.thumbnail;
    $('existingThumb').hidden=!existing;$('useExisting').disabled=!existing;
    if(existing)$('existingThumb').src=existing;
    $('existingThumbInfo').textContent=existing?'현황판에 등록된 썸네일':'기존 썸네일이 없습니다.';
  }
  function review() {
    const dl=$('reviewFields');dl.replaceChildren();
    for(const [key,label] of Object.entries({title:'영상 제목',src_url:'원본 링크',ref_urls:'참고 쇼츠 링크',memo:'메모'})){
      const dt=document.createElement('dt'),dd=document.createElement('dd');dt.textContent=label;dd.textContent=fields()[key]||'—';dl.append(dt,dd);
    }
    $('reviewDescription').textContent=recipe.snapshot().results.youtube;
    if(previewURL)$('reviewThumbnail').src=previewURL;
  }
  function renderItems() {
    const query=$('itemSearch').value.trim().toLowerCase(), list=$('itemList');list.replaceChildren();
    const counts=new Map();items.forEach(it=>counts.set(it.item_id,(counts.get(it.item_id)||0)+1));
    const visible=items.filter(it=>it.status!=='uploaded' && [it.dish_title,it.video?.title].some(v=>(v||'').toLowerCase().includes(query)));
    if(!visible.length){list.textContent=query?'검색 결과가 없습니다.':'진행할 항목이 없습니다. 모든 영상의 업로드가 완료되었습니다.';return;}
    visible.forEach(it=>{
      const btn=document.createElement('button'), info=document.createElement('span'),title=document.createElement('strong'),desc=document.createElement('small'),badge=document.createElement('span');
      btn.type='button';btn.className='item-option';title.textContent=it.dish_title||it.video?.title||'(제목 없음)';desc.textContent=it.video?.title||'영상 제목은 다음 단계에서 입력합니다.';
      const valid=!!it.item_id && counts.get(it.item_id)===1;
      badge.className='badge';badge.textContent=valid?({candidate:'촬영 후보',making:'촬영 중',editing:'편집 중',ready:'업로드 대기'}[it.status]||it.status):'항목 ID 확인 필요';
      btn.disabled=!valid||busy||switching||!!state?.pending;info.append(title,desc);btn.append(info,badge);btn.onclick=()=>select(it.item_id);list.append(btn);
    });
  }
  async function loadList(restoreLast=false) {
    try {
      const data=await json('/api/shorts?refresh=1');
      if(!data.sheet?.configured||!data.sheet?.upload_configured) throw new Error('쇼츠 현황판에서 구글 시트와 시트 쓰기 연결을 먼저 설정해 주세요.');
      if(!data.sheet.connection) throw new Error('앱을 최신 버전으로 다시 시작해 주세요.');
      if(connection && connection!==data.sheet.connection && state){blocked=true;throw new Error('연결된 시트가 변경되었습니다. 원래 시트 연결에서 초안을 다시 여세요.');}
      connection=data.sheet.connection;items=data.items || [];renderItems();
      if(data.error) throw new Error(data.error);
      if(restoreLast){
        let last;try{last=await store.last(connection);}catch(e){persistFailed=true;$('draftStatus').textContent='초안 저장소를 사용할 수 없습니다. 페이지 이탈 시 작업이 사라집니다.';}
        if(last) await select(last);
      }
    }catch(e){message(e.message,true);if(!items.length)$('itemList').textContent='현황판 연결을 확인한 뒤 목록 새로고침을 눌러 주세요.';}
  }
  async function select(id, fresh=false) {
    if(busy||switching||state?.pending) return;
    switching=true;setLoading('select');updateNav();renderItems();
    try {
      if(state){await persist().catch(()=>{});if(persistFailed&&!confirm('초안 저장에 실패했습니다. 현재 입력을 떠나 다른 항목을 열까요?'))return;}
      await recipe.ready;
      let draft;try{draft=fresh?null:await store.get(connection,id);}catch(e){persistFailed=true;}
      const generation=++selectedGeneration;
      recipe.cancel();
      const q=new URLSearchParams({connection,...(draft?.pending?{request_id:draft.pending.payload.request_id}:{})});
      let data,error;
      try { data=await json('/api/upload-process/items/'+encodeURIComponent(id)+'?'+q); } catch(e){error=e;if(!draft)throw e;}
      if(generation!==selectedGeneration) return;
      restoring=true;
      const item=data?.item || draft.item;
      state=draft || {version:1,connection,item,revision:data.revision,fields:{title:item.video.title,src_url:item.source.url,ref_urls:item.ref_urls||item.reference_shorts.map(r=>r.url).join('\n'),memo:item.memo},recipeConfirmed:false,thumb:null,pending:null,step:1};
      fillFields(state.fields);await recipe.restore(state.recipe);editor.reset();
      if(data?.submitted && state.pending){restoring=false;await finish(data);return;}
      blocked=!!error||item.status==='uploaded'||(!!draft&&data.revision!==draft.revision);
      if(error && ['row_mismatch','connection_changed','request_mismatch'].includes(error.code))state.pending=null;
      if(!draft&&item.status==='uploaded')throw new Error('이미 업로드 완료된 항목입니다. 다른 항목을 선택하세요.');
      if(!blocked)state.item=item;
      $('conflictBox').classList.toggle('hidden',!blocked);
      message(error?.message || (blocked?'시트 내용이 변경되었습니다. 초안을 확인한 뒤 최신 내용으로 다시 시작해 주세요.':draft?'저장된 초안을 복원했습니다.':''),blocked);
      document.querySelectorAll('.selected-label').forEach(el=>el.textContent=state.item.dish_title||'선택한 영상');
      $('completed').classList.add('hidden');$('processWork').hidden=false;
      updateThumbnail();restoring=false;
      let target=Math.max(1,Math.min(4,state.step||1));while(target>1&&!reachable(target))target--;
      showStep(target);showRecovery();
    }catch(e){restoring=false;message(e.message,true);}
    finally{switching=false;setLoading();updateNav();renderItems();}
  }
  function showRecovery() {
    const pending=!!state?.pending;$('recovery').classList.toggle('hidden',!pending);
    $('processWork').disabled=pending||busy;
    $('recoveryText').textContent='제출한 내용의 저장 결과를 확인하고 있습니다. 확인 전에는 내용을 바꾸거나 다른 항목을 제출할 수 없습니다.';
    $('retrySubmission').hidden=true;updateNav();
  }
  async function finish(data) {
    const id=state.item.item_id, c=state.connection;
    busy=false;blocked=false;state.pending=null;
    await saveQueue.catch(()=>{});
    try{await store.remove(c,id);unsaved=false;persistFailed=false;$('draftStatus').textContent='제출한 초안을 정리했습니다.';}catch(e){persistFailed=true;$('draftStatus').textContent='저장은 완료됐지만 이 브라우저의 초안 삭제에 실패했습니다.';}
    $('recovery').classList.add('hidden');$('conflictBox').classList.add('hidden');$('processWork').hidden=true;$('processWork').disabled=false;
    $('completed').classList.remove('hidden');$('completedInfo').textContent=data.item.video.title+' · ✅ 업로드 완료';
    message((data.warnings||[]).join('\n'));
    items=items.filter(it=>it.item_id!==id);state=null;updateNav();
    try{localStorage.setItem('shorts-board-change',String(Date.now()));const channel=new BroadcastChannel('shorts-board');channel.postMessage('changed');channel.close();}catch(e){}
  }
  async function sendPending() {
    if(!state?.pending||busy) return;
    busy=true;setLoading('submit');showRecovery();$('checkSubmission').disabled=$('retrySubmission').disabled=true;
    try{
      const form=new FormData();form.append('payload',JSON.stringify(state.pending.payload));if(state.pending.blob)form.append('file',state.pending.blob,'thumbnail.jpg');
      const data=await json('/api/upload-process/submit',{method:'POST',body:form});await finish(data);
    }catch(e){
      message(e.message,true);
      const definite=['bad_fields','connection_changed','conflict','already_uploaded','row_mismatch','sheets_service_required','unknown_action','upgrade_required','image_failed','commit_failed','unauthorized','wrong_sheet','busy'].includes(e.code);
      if(definite){state.pending=null;blocked=['conflict','already_uploaded','row_mismatch','connection_changed'].includes(e.code);$('conflictBox').classList.toggle('hidden',!blocked);await persist().catch(()=>{});}
    }finally{busy=false;setLoading();$('checkSubmission').disabled=$('retrySubmission').disabled=false;showRecovery();}
  }
  async function checkSubmission() {
    if(!state?.pending||busy)return;
    busy=true;$('checkSubmission').disabled=true;$('retrySubmission').hidden=true;
    try{
      const p=state.pending.payload,q=new URLSearchParams({request_id:p.request_id,connection:p.connection});
      const data=await json('/api/upload-process/items/'+encodeURIComponent(p.item_id)+'?'+q);
      if(data.submitted){await finish(data);return;}
      if(data.revision!==state.revision||data.item.status==='uploaded'){
        state.pending=null;blocked=true;$('conflictBox').classList.remove('hidden');message('완료 기록 없이 시트 내용이 변경되었습니다. 최신 내용을 확인해 주세요.',true);await persist().catch(()=>{});showRecovery();
      }else{
        $('recoveryText').textContent='완료 기록이 없습니다. 같은 제출 번호와 내용으로 다시 시도할 수 있습니다.';$('retrySubmission').hidden=false;
      }
    }catch(e){message(e.message,true);
      if(['row_mismatch','connection_changed','request_mismatch'].includes(e.code)){state.pending=null;blocked=true;$('conflictBox').classList.remove('hidden');await persist().catch(()=>{});showRecovery();}
    }
    finally{busy=false;$('checkSubmission').disabled=false;updateNav();}
  }
  $('submitProcess').onclick=async()=>{
    if(busy||blocked||state?.pending||!reachable(4)||!recipe.valid())return;
    if(!confirm('이 내용과 썸네일을 현황판에 저장하고 상태를 ✅ 업로드 완료로 변경할까요?'))return;
    const payload={connection,item_id:state.item.item_id,revision:state.revision,request_id:crypto.randomUUID(),fields:{...fields(),desc:recipe.snapshot().results.youtube},thumbnail_mode:state.thumb.mode};
    state.pending={payload,blob:state.thumb.blob || null};setLoading('submit');showRecovery();
    try{await persist();}catch(e){state.pending=null;setLoading();showRecovery();message('제출 내용을 임시 보관하지 못했습니다. 브라우저 저장 공간을 확인한 뒤 다시 제출해 주세요.',true);return;}
    await sendPending();
  };
  $('checkSubmission').onclick=checkSubmission;$('retrySubmission').onclick=sendPending;
  $('refreshItems').onclick=()=>loadList();$('itemSearch').oninput=renderItems;
  document.querySelectorAll('[data-goto]').forEach(b=>b.onclick=()=>showStep(Number(b.dataset.goto)));
  $('previousStep').onclick=()=>showStep(step-1);$('nextStep').onclick=()=>showStep(step+1);
  $('confirmRecipe').onclick=()=>{if(recipe.valid()){state.recipeConfirmed=true;updateNav();persist();}};
  document.addEventListener('recipe:changed',()=>{if(!state||restoring||state.pending)return;state.recipeConfirmed=false;updateNav();persist();});
  document.querySelector('[data-step="1"]').addEventListener('input',()=>{if(state&&!restoring){updateNav();persist();}});
  document.addEventListener('thumbnail:changed',()=>{if(!state||restoring||state.pending)return;state.thumb=null;updateThumbnail();updateNav();persist();});
  $('confirmThumbnail').onclick=async()=>{
    if(busy)return;
    const generation=selectedGeneration;$('confirmThumbnail').disabled=true;busy=true;updateNav();
    ++saveSequence;unsaved=true;$('draftStatus').textContent='썸네일 확정 중…';
    try{const blob=await editor.exportImage('jpg',.9);if(generation!==selectedGeneration)return;state.thumb={mode:'new',blob};updateThumbnail();updateNav();await persist();message('썸네일을 확정했습니다.');}
    catch(e){message(e.message,true);persist();}finally{busy=false;$('confirmThumbnail').disabled=false;updateNav();}
  };
  $('useExisting').onclick=()=>{if(busy)return;state.thumb={mode:'existing',url:state.item.video.thumbnail};updateThumbnail();updateNav();persist();};
  $('restartDraft').onclick=async()=>{
    if(!state||state.pending||!confirm('현재 초안을 버리고 최신 시트 내용으로 다시 시작할까요?'))return;
    const id=state.item.item_id;await saveQueue.catch(()=>{});
    try{await store.remove(connection,id);}catch(e){message('초안을 지우지 못했습니다.',true);return;}
    state=null;blocked=false;await select(id,true);
  };
  $('nextItem').onclick=()=>{$('completed').classList.add('hidden');$('processWork').hidden=false;showStep(0);loadList();};
  window.addEventListener('beforeunload',e=>{if(unsaved||persistFailed){e.preventDefault();e.returnValue='';}});
  await recipe.ready;updateNav();await loadList(true);
})();
