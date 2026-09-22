'use strict';
// The state machine and persistence are independent of the recipe/thumbnail editors.
const UploadProcessData = (() => {
  const YOUTUBE_TITLE_MAX = 100, YOUTUBE_DESC_BYTES = 5000;
  function basicValid(fields) { return ['title','src_url','ref_urls'].every(k => typeof fields[k] === 'string' && fields[k].trim()) &&
    Object.entries({title:500,src_url:2000,ref_urls:5000,memo:5000}).every(([k,n]) => Array.from(fields[k] || '').length <= n); }
  function canReach(step, state) { return step === 0 || !!state.item && (step <= 1 || basicValid(state.fields)) &&
    (step <= 2 || state.recipeConfirmed) && (step <= 3 || !!state.thumb?.mode); }
  function key(connection, itemId) { return connection + ':' + itemId; }
  function utf8Bytes(text) { return new TextEncoder().encode(String(text || '').replace(/\r\n?/g,'\n')).length; }
  // Mirrors youtube_upload.service.validate_metadata: what the server will accept for the YouTube snippet.
  function youtubeMeta(title, desc) {
    const cleanTitle = String(title || '').split(/\s+/).filter(Boolean).join(' '), errors = [];
    const titleLen = Array.from(cleanTitle).length, descBytes = utf8Bytes(String(desc || '').trim());
    if (titleLen < 1 || titleLen > YOUTUBE_TITLE_MAX) errors.push(`유튜브 제목은 1~${YOUTUBE_TITLE_MAX}자여야 합니다. (현재 ${titleLen}자)`);
    if (descBytes > YOUTUBE_DESC_BYTES) errors.push(`유튜브 설명은 ${YOUTUBE_DESC_BYTES}바이트(한글 약 1,600자) 이하여야 합니다. (현재 ${descBytes}바이트)`);
    return {ok:!errors.length, errors, titleLen, descBytes, angleBrackets:/[<>]/.test(cleanTitle + String(desc || ''))};
  }
  function youtubeUrl(videoId) { return /^[A-Za-z0-9_-]{11}$/.test(videoId || '') ? 'https://youtu.be/' + videoId : ''; }
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
  return {basicValid,canReach,key,store,youtubeMeta,youtubeUrl,utf8Bytes,YOUTUBE_TITLE_MAX,YOUTUBE_DESC_BYTES};
})();
if (typeof module !== 'undefined') module.exports = UploadProcessData;
if (typeof document !== 'undefined') (async () => {
  const $ = id => document.getElementById(id), recipe = window.RecipeEditor, editor = window.ThumbnailEditor;
  const {store,canReach,basicValid,youtubeMeta} = UploadProcessData;
  let items=[], connection=null, state=null, step=0, switching=false, busy=false, blocked=false, restoring=false;
  let saveSequence=0;
  // YouTube upload runtime state lives on the server; the browser only keeps the chosen File and the adopted job.
  let youtubeStatus=null, youtubeJob=null, videoFile=null, uploading=false, uploadController=null, pollTimer=null, autoSubmit=false, previewVideoURL=null;
  function setLoading(kind=null) {
    const active=!!kind, submitting=kind==='submit';
    $('processLoadingTitle').textContent=active?(submitting?'현황판에 저장 중…':'영상 정보를 불러오는 중…'):'';
    $('processLoadingDetail').textContent=active?(submitting?'제출한 내용과 썸네일을 저장하고 있습니다. 잠시만 기다려 주세요.':'최신 현황판 정보와 저장된 초안을 확인하고 있습니다.'):'';
    $('processLoading').hidden=!active;
    const main=document.querySelector('main');main.inert=active;main.setAttribute('aria-busy',String(active));
    if(submitting)$('submitProcess').textContent='저장 중…';else renderYoutube();
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
  // Plain JSON helper for the YouTube endpoints: network failures are reported as such, not as sheet-commit doubt.
  async function api(url, options={}) {
    if(options.body && !(options.body instanceof FormData) && typeof options.body!=='string'){options={...options,headers:{'Content-Type':'application/json',...(options.headers||{})},body:JSON.stringify(options.body)};}
    let response;
    try { response=await fetch(url,options); }
    catch(e) { if(e.name==='AbortError')throw e; throw Object.assign(new Error('서버에 연결할 수 없습니다.'),{code:'network'}); }
    const body=await response.json().catch(()=>({error:'서버 응답을 읽지 못했습니다.'}));
    if(!response.ok || body.error) throw Object.assign(new Error(body.error || `요청 실패 (${response.status})`),{code:body.code || 'error',job:body.job});
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
    document.querySelectorAll('[data-goto]').forEach(b=>{ const n=Number(b.dataset.goto);b.disabled=busy||switching||uploading||!!state?.pending||!reachable(n);b.setAttribute('aria-current',n===step?'step':'false'); });
    $('nextStep').disabled=busy||switching||blocked||uploading||!!state?.pending||!reachable(step+1);$('previousStep').disabled=switching||uploading;
    $('submitProcess').disabled=busy||switching||blocked||uploading||!reachable(4)||!recipe.valid();
    $('confirmRecipe').disabled=!recipe.valid();
    $('recipeState').textContent=state?.recipeConfirmed?'설명글 확정됨':'설명글을 생성·수정한 뒤 확정하세요.';
    updateCounters();renderYoutube();
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
    $('reviewCopyStatus').textContent='';
    const dl=$('reviewFields');dl.replaceChildren();
    for(const [key,label] of Object.entries({title:'영상 제목',src_url:'원본 링크',ref_urls:'참고 쇼츠 링크',memo:'메모'})){
      const dt=document.createElement('dt'),dd=document.createElement('dd');dt.textContent=label;dd.textContent=fields()[key]||'—';dl.append(dt,dd);
      if(key==='title'){
        const button=document.createElement('button');button.type='button';button.className='secondary review-copy';button.textContent='복사';button.setAttribute('aria-label','영상 제목 복사');
        dt.classList.add('review-heading');dt.append(button);button.onclick=()=>copyReview(dd.textContent,label,button);
      }
    }
    $('reviewDescription').textContent=recipe.snapshot().results.youtube;
    if(previewURL)$('reviewThumbnail').src=previewURL;
  }
  async function copyReview(text,label,button) {
    const status=$('reviewCopyStatus');status.textContent='';button.disabled=true;
    try{
      try{await navigator.clipboard.writeText(text);}
      catch{
        const field=document.createElement('textarea'),previous=document.activeElement;
        field.value=text;field.readOnly=true;field.style.cssText='position:fixed;left:-9999px;top:0';document.body.append(field);
        try{field.select();if(!document.execCommand('copy'))throw new Error('Copy failed');}
        finally{field.remove();previous?.focus({preventScroll:true});}
      }
      status.textContent=label+'을 복사했습니다.';
    }catch{status.textContent='복사하지 못했습니다. 텍스트를 직접 선택해 복사해 주세요.';}
    finally{button.disabled=false;}
  }
  $('copyReviewDescription').onclick=()=>copyReview($('reviewDescription').textContent,'유튜브 설명글',$('copyReviewDescription'));
  function renderItems() {
    const query=$('itemSearch').value.trim().toLowerCase(), list=$('itemList');list.replaceChildren();
    const counts=new Map();items.forEach(it=>counts.set(it.item_id,(counts.get(it.item_id)||0)+1));
    const visible=items.filter(it=>it.status!=='uploaded' && [it.dish_title,it.video?.title].some(v=>(v||'').toLowerCase().includes(query)));
    if(!visible.length){list.textContent=query?'검색 결과가 없습니다.':'진행할 항목이 없습니다. 모든 영상의 업로드가 완료되었습니다.';return;}
    visible.forEach(it=>{
      const btn=document.createElement('button'), info=document.createElement('span'),title=document.createElement('strong'),desc=document.createElement('small'),badge=document.createElement('span');
      btn.type='button';btn.className='item-option';title.textContent=it.dish_title||it.video?.title||'(제목 없음)';desc.textContent=it.video?.title||'영상 제목은 다음 단계에서 입력합니다.';
      const valid=!!it.item_id && counts.get(it.item_id)===1;
      badge.className='badge';badge.textContent=valid?({candidate:'촬영 후보',making:'촬영 중',filmed:'촬영 완료',editing:'편집 중',ready:'업로드 대기'}[it.status]||it.status):'항목 ID 확인 필요';
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
    if(busy||switching||state?.pending||uploading) return;
    switching=true;setLoading('select');updateNav();renderItems();
    let generation=selectedGeneration, selected=false;
    try {
      if(state){await persist().catch(()=>{});if(persistFailed&&!confirm('초안 저장에 실패했습니다. 현재 입력을 떠나 다른 항목을 열까요?'))return;}
      await recipe.ready;
      let draft;try{draft=fresh?null:await store.get(connection,id);}catch(e){persistFailed=true;}
      generation=++selectedGeneration;
      recipe.cancel();
      const q=new URLSearchParams({connection,...(draft?.pending?{request_id:draft.pending.payload.request_id}:{})});
      let data,error;
      try { data=await json('/api/upload-process/items/'+encodeURIComponent(id)+'?'+q); } catch(e){error=e;if(!draft)throw e;}
      if(generation!==selectedGeneration) return;
      const item=data?.item || draft.item;
      // Decide before touching the visible state so a refused item leaves the previous screen intact.
      if(!draft&&item.status==='uploaded'){
        const job=await loadJobFor(id).catch(()=>null);
        throw new Error('이미 업로드 완료된 항목입니다. 다른 항목을 선택하세요.'+(job?.url?` 유튜브 링크: ${job.url}`:''));
      }
      restoring=true;
      state=draft || {version:1,connection,item,revision:data.revision,fields:{title:item.video.title,src_url:item.source.url,ref_urls:item.ref_urls||item.reference_shorts.map(r=>r.url).join('\n'),memo:item.memo},recipeConfirmed:false,thumb:null,pending:null,step:1};
      fillFields(state.fields);await recipe.restore(state.recipe);editor.reset();resetVideoFile();
      if(data?.submitted && state.pending){restoring=false;await finish(data);return;}
      blocked=!!error||item.status==='uploaded'||(!!draft&&data.revision!==draft.revision);
      if(error && ['row_mismatch','connection_changed','request_mismatch'].includes(error.code))state.pending=null;
      if(!blocked)state.item=item;
      $('conflictBox').classList.toggle('hidden',!blocked);
      message(error?.message || (blocked?'시트 내용이 변경되었습니다. 초안을 확인한 뒤 최신 내용으로 다시 시작해 주세요.':draft?'저장된 초안을 복원했습니다.':''),blocked);
      document.querySelectorAll('.selected-label').forEach(el=>el.textContent=state.item.dish_title||'선택한 영상');
      $('completed').classList.add('hidden');$('processWork').hidden=false;
      updateThumbnail();restoring=false;
      let target=Math.max(1,Math.min(4,state.step||1));while(target>1&&!reachable(target))target--;
      showStep(target);showRecovery();selected=true;
    }catch(e){restoring=false;message(e.message,true);}
    finally{switching=false;setLoading();updateNav();renderItems();}
    if(!selected) return;
    // After the overlay is gone: adopt a YouTube job the server already has for this item (busy → keep polling).
    const job=await loadJobFor(id).catch(()=>null);
    if(generation!==selectedGeneration||!state) return;
    youtubeJob=job;if(job?.busy) pollJob(false);renderYoutube();
  }
  function showRecovery() {
    const pending=!!state?.pending;$('recovery').classList.toggle('hidden',!pending);
    $('processWork').disabled=pending||busy;   // not while uploading: the cancel button lives inside this fieldset
    $('recoveryText').textContent='제출한 내용의 저장 결과를 확인하고 있습니다. 확인 전에는 내용을 바꾸거나 다른 항목을 제출할 수 없습니다.';
    $('retrySubmission').hidden=true;updateNav();
  }
  async function finish(data) {
    const id=state.item.item_id, c=state.connection, link=youtubeJob?.url && !youtubeJob?.test_mode ? youtubeJob.url : '';
    busy=false;blocked=false;state.pending=null;
    await saveQueue.catch(()=>{});
    try{await store.remove(c,id);unsaved=false;persistFailed=false;$('draftStatus').textContent='제출한 초안을 정리했습니다.';}catch(e){persistFailed=true;$('draftStatus').textContent='저장은 완료됐지만 이 브라우저의 초안 삭제에 실패했습니다.';}
    $('recovery').classList.add('hidden');$('conflictBox').classList.add('hidden');$('processWork').hidden=true;$('processWork').disabled=false;
    $('completed').classList.remove('hidden');
    const info=$('completedInfo');info.replaceChildren(document.createTextNode(data.item.video.title+' · ✅ 업로드 완료'));
    if(link){const a=document.createElement('a');a.href=link;a.target='_blank';a.rel='noopener';a.textContent=link;info.append(document.createTextNode(' · 유튜브: '),a);}
    message((data.warnings||[]).join('\n'));
    items=items.filter(it=>it.item_id!==id);state=null;youtubeJob=null;resetVideoFile();updateNav();
    try{localStorage.setItem('shorts-board-change',String(Date.now()));const channel=new BroadcastChannel('shorts-board');channel.postMessage('changed');channel.close();}catch(e){}
    loadJobs().catch(()=>{});
  }
  async function sendPending() {
    if(!state?.pending||busy) return;
    busy=true;setLoading('submit');showRecovery();$('checkSubmission').disabled=$('retrySubmission').disabled=true;
    try{
      const form=new FormData();form.append('payload',JSON.stringify(state.pending.payload));if(state.pending.blob)form.append('file',state.pending.blob,'thumbnail.jpg');
      const data=await json('/api/upload-process/submit',{method:'POST',body:form});await finish(data);
    }catch(e){
      const link=state.pending?.payload?.youtube_job_id && youtubeJob?.url ? youtubeJob.url : '';
      message(e.message+(link?`\n유튜브 업로드는 완료되었습니다 (${link}). 현황판 제출만 다시 시도하세요.`:''),true);
      const definite=['bad_fields','connection_changed','conflict','already_uploaded','row_mismatch','sheets_service_required','unknown_action','upgrade_required','image_failed','commit_failed','unauthorized','wrong_sheet','busy','youtube_job_invalid'].includes(e.code);
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
  // Sheet submission. `jobId` names a finished YouTube upload whose link the server records with the status flip.
  async function submitSheet(jobId=null) {
    if(busy||blocked||state?.pending||!reachable(4)||!recipe.valid())return;
    const payload={connection,item_id:state.item.item_id,revision:state.revision,request_id:crypto.randomUUID(),fields:{...fields(),desc:recipe.snapshot().results.youtube},thumbnail_mode:state.thumb.mode,...(jobId?{youtube_job_id:jobId}:{})};
    state.pending={payload,blob:state.thumb.blob || null};setLoading('submit');showRecovery();
    try{await persist();}catch(e){state.pending=null;setLoading();showRecovery();message('제출 내용을 임시 보관하지 못했습니다. 브라우저 저장 공간을 확인한 뒤 다시 제출해 주세요.',true);return;}
    await sendPending();
  }
  $('submitProcess').onclick=async()=>{
    if(busy||blocked||state?.pending||uploading||!reachable(4)||!recipe.valid())return;
    const withLink=!!(youtubeJob?.status==='done'&&youtubeJob.url&&!youtubeJob.test_mode);
    if(!confirm(withLink?'유튜브 링크와 함께 이 내용을 현황판에 저장하고 상태를 ✅ 업로드 완료로 변경할까요?':'영상 없이 이 내용과 썸네일을 현황판에 저장하고 상태를 ✅ 업로드 완료로 변경할까요?'))return;
    await submitSheet(withLink?youtubeJob.id:null);
  };
  // ---- YouTube ------------------------------------------------------------
  function fmtSize(n){return n>=1024**3?(n/1024**3).toFixed(2)+' GB':(n/1024**2).toFixed(1)+' MB';}
  function counterText(el,text,over){el.textContent=text;el.classList.toggle('over',over);}
  function updateCounters(){
    const meta=youtubeMeta($('videoTitle').value,recipe?.snapshot?.().results?.youtube||'');
    counterText($('titleYoutubeHint'),`유튜브 제목 ${meta.titleLen}/${UploadProcessData.YOUTUBE_TITLE_MAX}자`+(meta.titleLen>UploadProcessData.YOUTUBE_TITLE_MAX?' · 유튜브 업로드 시 초과':''),meta.titleLen>UploadProcessData.YOUTUBE_TITLE_MAX);
    counterText($('descYoutubeHint'),`유튜브 설명 ${meta.descBytes.toLocaleString()}/${UploadProcessData.YOUTUBE_DESC_BYTES.toLocaleString()}바이트`+(meta.descBytes>UploadProcessData.YOUTUBE_DESC_BYTES?' · 유튜브 업로드 시 초과':''),meta.descBytes>UploadProcessData.YOUTUBE_DESC_BYTES);
    return meta;
  }
  async function loadYoutubeStatus(){
    try{youtubeStatus=await api('/api/youtube/status');}
    catch(e){youtubeStatus={configured:false,connected:false,library_missing:false,oauth_available:true,error:e.message};}
    renderYoutube();
  }
  async function loadJobFor(id){
    if(!connection)return null;
    const data=await api('/api/youtube/jobs?'+new URLSearchParams({item_id:id,connection}));
    return data.job||null;
  }
  async function loadJobs(){
    const box=$('youtubeJobs');
    try{
      const data=await api('/api/youtube/jobs'+(connection?'?'+new URLSearchParams({connection}):''));
      box.replaceChildren();
      if(!data.jobs.length){const p=document.createElement('p');p.className='hint';p.textContent='기록이 없습니다.';box.append(p);return;}
      for(const j of data.jobs){
        const row=document.createElement('div');row.className='job';
        const left=document.createElement('span');left.textContent=(j.title||'(제목 없음)')+(j.test_mode?' · 테스트':'');
        const right=document.createElement('small');
        const when=new Date((j.created||0)*1000).toLocaleString('ko-KR',{month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit'});
        if(j.url){const a=document.createElement('a');a.href=j.url;a.target='_blank';a.rel='noopener';a.textContent=j.url;right.append(a,document.createTextNode(' · '+when));}
        else right.textContent=`${({queued:'대기',uploading:'업로드 중',finalizing:'마무리 중',thumbnail:'썸네일 설정 중',error:'실패',interrupted:'중단됨'})[j.status]||j.status} · ${when}`;
        row.append(left,right);box.append(row);
      }
    }catch(e){box.textContent=e.message;}
  }
  function renderYoutube(){
    const st=youtubeStatus, job=youtubeJob;
    if(!st){$('youtubeState').textContent='확인 중…';return;}
    const connected=st.connected&&!st.library_missing, ready=connected&&!!st.configured;
    $('youtubeRedirect').textContent=st.redirect_uri||'';
    $('youtubeState').textContent=st.error?'상태 확인 실패':st.library_missing?'라이브러리 없음 · run.sh 재실행':!st.configured?'미설정':!st.connected?'연결 안 됨':(st.channel_title||'연결됨')+(st.audit_passed?'':' · 테스트 모드');
    $('youtubeConnect').disabled=!st.configured||!st.oauth_available||st.library_missing;
    $('youtubeConnect').title=st.oauth_available?'':'Google 연결은 로컬(127.0.0.1)에서 실행한 앱에서만 할 수 있습니다.';
    $('youtubeDisconnect').hidden=!st.connected;
    $('youtubeAudit').checked=!!st.audit_passed;
    $('youtubeQuota').textContent=typeof st.uploads_today==='number'?`오늘 API 업로드 ${st.uploads_today}건.`:'';
    // step 4 panel
    const testMode=!st.audit_passed;
    $('youtubeConnState').textContent=st.library_missing?'Google 라이브러리가 없어 업로드할 수 없습니다.':!ready?'유튜브 채널이 연결되지 않았습니다. 상단 "유튜브 채널 연결"에서 설정하세요.':(st.channel_title?`채널: ${st.channel_title}`:'채널 연결됨')+(testMode?' · 테스트 모드':'');
    $('youtubeTestBanner').hidden=!(ready&&testMode);
    const done=job?.status==='done'&&!!job.url, running=uploading||!!job?.busy, failed=!!job&&!job.busy&&['error','interrupted'].includes(job.status);
    $('youtubePick').hidden=!ready||done||running;
    $('videoFile').disabled=running||busy;
    $('youtubeProgressBox').hidden=!running;
    if(job?.busy&&!uploading){$('youtubeProgress').value=job.percent||0;$('youtubeStatus').textContent=job.message||'';$('youtubeCancel').hidden=true;}
    $('youtubeDone').hidden=!done;
    if(done){$('youtubeDone').replaceChildren(document.createTextNode(job.test_mode?'테스트 업로드 완료 (비공개, 현황판 미제출): ':'유튜브 업로드 완료 (비공개): '));const a=document.createElement('a');a.href=job.url;a.target='_blank';a.rel='noopener';a.textContent=job.url;$('youtubeDone').append(a);
      if(job.warnings?.length)$('youtubeDone').append(document.createTextNode(' · '+job.warnings.join(' ')));
      const again=document.createElement('button');again.type='button';again.className='secondary review-copy';again.textContent='다른 영상으로 다시 업로드';again.onclick=resetJob;$('youtubeDone').append(document.createTextNode(' '),again);}
    $('youtubeRetry').hidden=!failed;
    if(failed){$('youtubeMetaHint').textContent=job.message||'유튜브 업로드에 실패했습니다.';$('youtubeRetry').textContent=job.error_code==='invalid_grant'?'설정에서 Google 다시 연결':job.error_code==='asset_missing'?'파일을 다시 선택해 업로드':'유튜브 업로드 다시 시도';}
    else if(!running)$('youtubeMetaHint').textContent='';
    // buttons
    const canUpload=ready&&!!videoFile&&!done&&!running&&!failed;
    $('uploadAndSubmit').hidden=!canUpload;
    $('uploadAndSubmit').textContent=testMode?'테스트 업로드 (현황판 제출 없음)':'유튜브에 비공개 업로드 후 현황판에 제출';
    $('uploadAndSubmit').disabled=busy||switching||blocked||uploading||!!state?.pending||!state||!reachable(4)||!recipe.valid();
    const withLink=done&&!job.test_mode;
    $('submitProcess').textContent=busy?'저장 중…':withLink?'현황판에 제출 (유튜브 링크 포함)':canUpload||running?'영상 없이 현황판에만 제출':'현황판에 제출';
    $('submitProcess').classList.toggle('secondary',canUpload||running);
    $('submitProcess').disabled=busy||switching||blocked||uploading||!state||!reachable(4)||!recipe.valid();
    $('submitHint').textContent=withLink?'승인하면 현황판에 저장하고 유튜브 체크·링크와 함께 상태를 ✅ 업로드 완료로 변경합니다.':ready&&!done?'영상을 선택하면 유튜브에 비공개로 올린 뒤 현황판에 저장합니다. 유튜브 스튜디오에서 직접 올린 경우 영상 없이 제출하세요.':'승인하면 현황판에 저장하고 상태를 ✅ 업로드 완료로 변경합니다.';
  }
  function resetVideoFile(){videoFile=null;$('videoFile').value='';$('videoFileInfo').textContent='';if(previewVideoURL){URL.revokeObjectURL(previewVideoURL);previewVideoURL=null;}$('videoPreview').hidden=true;$('videoPreview').removeAttribute('src');}
  $('videoFile').onchange=()=>{
    const file=$('videoFile').files[0];
    if(previewVideoURL){URL.revokeObjectURL(previewVideoURL);previewVideoURL=null;}
    videoFile=file||null;$('videoPreview').hidden=!file;
    if(!file){$('videoFileInfo').textContent='';renderYoutube();return;}
    $('videoFileInfo').textContent=`${file.name} · ${fmtSize(file.size)}`;
    previewVideoURL=URL.createObjectURL(file);$('videoPreview').src=previewVideoURL;
    $('videoPreview').onloadedmetadata=()=>{
      const v=$('videoPreview'),warn=[];
      if(v.duration>180)warn.push('3분을 넘어 쇼츠로 인식되지 않을 수 있습니다');
      if(v.videoWidth>=v.videoHeight)warn.push('세로 영상이 아니어서 쇼츠로 인식되지 않을 수 있습니다');
      $('videoFileInfo').textContent=`${file.name} · ${fmtSize(file.size)} · ${Math.round(v.duration)}초 · ${v.videoWidth}×${v.videoHeight}`+(warn.length?' · ⚠ '+warn.join(', '):'');
    };
    renderYoutube();
  };
  function progress(percent,text){$('youtubeProgress').value=percent;$('youtubeStatus').textContent=`${text} ${percent}%`;}
  async function uploadVideo(file){
    const cfg=await api('/api/hooks/config');
    if(file.size>cfg.max_video_bytes)throw new Error('영상 파일 용량이 제한을 초과합니다.');
    const registered=await api('/api/hooks/uploads',{method:'POST',body:{name:file.name,size:file.size}});
    uploadController=new AbortController();$('youtubeCancel').hidden=false;
    try{
      for(let start=0,index=0;start<file.size;start+=registered.chunk_size,index++){
        const buffer=await file.slice(start,start+registered.chunk_size).arrayBuffer();
        const hash=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',buffer)),b=>b.toString(16).padStart(2,'0')).join('');
        for(let attempt=0;;attempt++){
          try{const r=await fetch(`/api/hooks/uploads/${registered.id}/chunks/${index}`,{method:'PUT',headers:{'Content-Type':'application/octet-stream','X-Chunk-SHA256':hash},body:buffer,signal:uploadController.signal});if(!r.ok)throw new Error((await r.json().catch(()=>({}))).error||'영상 조각 전송에 실패했습니다.');break;}
          catch(e){if(attempt||uploadController.signal.aborted)throw e;}
        }
        progress(Math.round(Math.min(file.size,start+registered.chunk_size)/file.size*100),'서버로 전송 중');
      }
      $('youtubeStatus').textContent='영상 확인 중…';
      return (await api(`/api/hooks/uploads/${registered.id}/complete`,{method:'POST',body:{}})).asset_id;
    }catch(e){await api(`/api/hooks/uploads/${registered.id}`,{method:'DELETE'}).catch(()=>{});throw e;}
    finally{uploadController=null;$('youtubeCancel').hidden=true;}
  }
  $('youtubeCancel').onclick=()=>uploadController?.abort();
  function beginUploading(){uploading=true;autoSubmit=false;$('youtubeProgressBox').hidden=false;$('youtubeMetaHint').textContent='';progress(0,'준비 중');showRecovery();renderYoutube();}
  function endUploading(){uploading=false;showRecovery();renderYoutube();}
  $('uploadAndSubmit').onclick=async()=>{
    if(busy||blocked||uploading||state?.pending||!reachable(4)||!recipe.valid()||!videoFile||!youtubeStatus?.connected)return;
    const meta=updateCounters();
    if(!meta.ok){message(meta.errors.join('\n'),true);return;}
    const testMode=!youtubeStatus.audit_passed, file=videoFile;
    const text=testMode?`${file.name} (${fmtSize(file.size)}) 을 유튜브에 비공개 테스트 업로드합니다. 감사 승인 전이므로 현황판에는 제출하지 않으며, 이 영상은 공개할 수 없습니다. 몇 분 걸릴 수 있습니다.`
      :`${file.name} (${fmtSize(file.size)}) 을 유튜브에 비공개로 업로드합니다. 완료되면 현황판에 저장하고 상태를 ✅ 업로드 완료로 변경합니다. 몇 분 걸릴 수 있습니다.`;
    if(!confirm(text))return;
    message('');beginUploading();
    try{
      const assetId=await uploadVideo(file);
      const form=new FormData();
      form.append('payload',JSON.stringify({asset_id:assetId,item_id:state.item.item_id,connection,title:fields().title,description:recipe.snapshot().results.youtube,force:youtubeJob?.status==='done'}));
      if(state.thumb?.mode==='new'&&state.thumb.blob)form.append('thumbnail',state.thumb.blob,'thumbnail.jpg');
      $('youtubeStatus').textContent='유튜브 업로드 시작 중…';
      try{youtubeJob=await api('/api/youtube/jobs',{method:'POST',body:form});}
      catch(e){if(e.code==='job_exists'&&e.job){youtubeJob=e.job;}else throw e;}
      autoSubmit=!testMode;uploading=false;resetVideoFile();
      await pollJob(true);
    }catch(e){
      if(e.name==='AbortError')message('영상 업로드를 취소했습니다.');
      else message(e.message,true);
      endUploading();
    }
  };
  async function pollJob(fromThisSession){
    clearTimeout(pollTimer);
    if(!youtubeJob)return;
    let delay=1000;
    const tick=async()=>{
      try{
        youtubeJob=await api('/api/youtube/jobs/'+youtubeJob.id);delay=1000;
        $('youtubeStatus').textContent=youtubeJob.message||'';$('youtubeProgress').value=youtubeJob.percent||0;
      }catch(e){
        if(e.code==='not_found'){youtubeJob=null;message('유튜브 업로드 기록을 찾지 못했습니다. 다시 시도해 주세요.',true);endUploading();return;}
        delay=Math.min(10000,delay*2);$('youtubeStatus').textContent='서버 응답 대기 중…';
      }
      if(youtubeJob?.busy){pollTimer=setTimeout(tick,delay);renderYoutube();return;}
      endUploading();loadYoutubeStatus();loadJobs().catch(()=>{});
      if(youtubeJob?.status==='done'){
        if(fromThisSession&&autoSubmit&&!youtubeJob.test_mode&&state&&!state.pending){autoSubmit=false;await submitSheet(youtubeJob.id);}
        else if(youtubeJob.test_mode)message('테스트 업로드가 끝났습니다. YouTube Studio 에서 영상과 썸네일을 확인하세요. 현황판에는 제출하지 않았습니다.');
      }else if(youtubeJob)message(youtubeJob.message||'유튜브 업로드에 실패했습니다.',true);
    };
    uploading=true;renderYoutube();await tick();
  }
  $('youtubeRetry').onclick=async()=>{
    if(!youtubeJob||busy||uploading)return;
    if(youtubeJob.error_code==='invalid_grant'){$('youtubeBox').open=true;$('youtubeBox').scrollIntoView({behavior:'smooth'});return;}
    if(youtubeJob.error_code==='asset_missing'){youtubeJob=null;renderYoutube();return;}
    try{beginUploading();youtubeJob=await api(`/api/youtube/jobs/${youtubeJob.id}/retry`,{method:'POST',body:{}});autoSubmit=!youtubeJob.test_mode;await pollJob(true);}
    catch(e){message(e.message,true);endUploading();}
  };
  async function resetJob(){
    if(!confirm('이미 올라간 유튜브 영상은 그대로 두고, 다른 영상 파일로 다시 업로드할까요? 기존 영상은 YouTube Studio 에서 직접 삭제해야 합니다.'))return;
    youtubeJob=null;renderYoutube();
  }
  // settings
  $('copyRedirect').onclick=()=>copyReview($('youtubeRedirect').textContent,'리디렉션 URI',$('copyRedirect'));
  function settingsMessage(text,error=false){const el=$('youtubeSettingsMessage');el.textContent=text;el.style.color=error?'var(--warn)':'';}
  $('youtubeSaveClient').onclick=async()=>{
    const text=$('youtubeClientJson').value.trim();
    try{youtubeStatus=await api('/api/youtube/credentials',{method:'POST',body:text?{client_json:text}:{}});$('youtubeClientJson').value='';settingsMessage(text?'OAuth 클라이언트를 저장했습니다. 이제 Google 계정을 연결하세요.':'연결 정보를 삭제했습니다.');}
    catch(e){settingsMessage(e.message,true);}
    renderYoutube();
  };
  $('youtubeConnect').onclick=async()=>{
    try{await persist().catch(()=>{});const data=await api('/api/youtube/oauth/start',{method:'POST',body:{}});unsaved=false;location.assign(data.url);}
    catch(e){settingsMessage(e.message,true);}
  };
  $('youtubeDisconnect').onclick=async()=>{
    if(!confirm('Google 연결을 해제할까요? 저장된 갱신 토큰을 폐기합니다.'))return;
    try{youtubeStatus=await api('/api/youtube/disconnect',{method:'POST',body:{}});settingsMessage('연결을 해제했습니다.');}catch(e){settingsMessage(e.message,true);}
    renderYoutube();
  };
  $('youtubeAudit').onchange=async()=>{
    try{youtubeStatus=await api('/api/youtube/settings',{method:'POST',body:{audit_passed:$('youtubeAudit').checked}});settingsMessage($('youtubeAudit').checked?'감사 승인 상태로 설정했습니다. 업로드 후 현황판에 제출합니다.':'테스트 모드입니다. 업로드만 하고 현황판에는 제출하지 않습니다.');}
    catch(e){settingsMessage(e.message,true);}
    renderYoutube();
  };
  $('youtubeBox').addEventListener('toggle',()=>{if($('youtubeBox').open)loadJobs().catch(()=>{});});
  function handleQuery(){
    const params=new URLSearchParams(location.search);
    if(!params.has('youtube'))return;
    if(params.get('youtube')==='connected'){message('Google 계정을 연결했습니다.');$('youtubeBox').open=true;}
    else{const code=params.get('code')||'';message({denied:'Google 동의 화면에서 접근이 거절되었습니다.',state:'연결 요청이 만료되었거나 일치하지 않습니다. 다시 시도해 주세요.',no_refresh_token:'Google 이 갱신 토큰을 주지 않았습니다. 동의 화면에서 접근을 다시 허용해 주세요.'}[code]||'Google 연결에 실패했습니다. 클라이언트 설정을 확인한 뒤 다시 시도해 주세요.',true);$('youtubeBox').open=true;}
    history.replaceState(null,'',location.pathname);
  }
  $('checkSubmission').onclick=checkSubmission;$('retrySubmission').onclick=sendPending;
  $('refreshItems').onclick=()=>loadList();$('itemSearch').oninput=renderItems;
  document.querySelectorAll('[data-goto]').forEach(b=>b.onclick=()=>showStep(Number(b.dataset.goto)));
  $('previousStep').onclick=()=>showStep(step-1);$('nextStep').onclick=()=>showStep(step+1);
  $('confirmRecipe').onclick=()=>{if(recipe.valid()){state.recipeConfirmed=true;updateNav();persist();}};
  document.addEventListener('recipe:changed',()=>{updateCounters();if(!state||restoring||state.pending)return;state.recipeConfirmed=false;updateNav();persist();});
  document.querySelector('[data-step="1"]').addEventListener('input',()=>{updateCounters();if(state&&!restoring){updateNav();persist();}});
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
    if(!state||state.pending||uploading||!confirm('현재 초안을 버리고 최신 시트 내용으로 다시 시작할까요? 유튜브 업로드 결과(링크)는 유지됩니다.'))return;
    const id=state.item.item_id;await saveQueue.catch(()=>{});
    try{await store.remove(connection,id);}catch(e){message('초안을 지우지 못했습니다.',true);return;}
    state=null;blocked=false;await select(id,true);
  };
  $('nextItem').onclick=()=>{$('completed').classList.add('hidden');$('processWork').hidden=false;showStep(0);loadList();};
  window.addEventListener('beforeunload',e=>{if(unsaved||persistFailed||uploading){e.preventDefault();e.returnValue='';}});
  await recipe.ready;updateNav();handleQuery();await Promise.all([loadYoutubeStatus(),loadList(true)]);
})();
