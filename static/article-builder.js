'use strict';
(() => {
  const root=document.getElementById('ab-panel'); if(!root)return;
  const $=id=>document.getElementById('ab-'+id), apiRoot='/api/articles';
  let initialized=false, settings=null, defaults=null, items=[], selected=null, job=null, stage=0, activeStep=null;
  let busy=false, conflict=false, dirty=false, sequence=0, saveTimer=null, saving=Promise.resolve(), polling=null;
  let writes=Promise.resolve();
  let crop=null, drag=null, looping=false, cropDirty=false, selectedTime=null;
  const video=$('video'), canvas=$('canvas'), ctx=canvas.getContext('2d');
  function message(value,error=false){$('message').textContent=value;$('message').classList.toggle('error',error);}
  async function json(url,method='GET',body){
    const response=await fetch(url,{method,headers:body===undefined?{}:{'Content-Type':'application/json'},...(body===undefined?{}:{body:JSON.stringify(body)})});
    const raw=await response.text();
    let data;
    try { data=raw ? JSON.parse(raw) : {}; }
    catch {
      const detail=raw.replace(/<[^>]*>/g,' ').replace(/\s+/g,' ').trim().slice(0,180);
      throw Object.assign(new Error(`서버 응답을 읽지 못했습니다. (${response.status}${detail ? ` · ${detail}` : ''})`),{status:response.status});
    }
    if(!response.ok)throw Object.assign(new Error(data.error||'요청에 실패했습니다.'),{status:response.status,code:data.code});
    return data;
  }
  function local(key,value){try{if(value===undefined)return JSON.parse(localStorage.getItem('article.'+key)||'null');if(value===null)localStorage.removeItem('article.'+key);else localStorage.setItem('article.'+key,JSON.stringify(value));}catch{}return null;}
  function id(){return crypto.randomUUID().replaceAll('-','');}
  function pendingKey(){return selected?'submission.'+selected.connection+'.'+selected.item.item_id:'';}
  function lock(value){busy=value;root.setAttribute('aria-busy',String(value));$('message').classList.toggle('ab-loading',value);$('work').disabled=value||conflict;$('new').disabled=value;$('history').disabled=value;$('delete').disabled=value||!job;
    root.querySelectorAll('[data-ab-stage]').forEach(b=>b.disabled=value||(!job&&Number(b.dataset.abStage)>0));}
  function failure(e){message(e.message,true);if(e.status===409&&e.draftConflict&&job){conflict=true;$('conflict').hidden=false;lock(false);}}
  function action(fn){return async(event)=>{if(busy)return;const button=event?.currentTarget;message('처리 중입니다. 잠시만 기다려주세요.');lock(true);button?.classList.add('ab-loading');button?.setAttribute('aria-busy','true');try{await fn();if($('message').textContent==='처리 중입니다. 잠시만 기다려주세요.')message('처리했습니다.');}catch(e){failure(e);}finally{button?.classList.remove('ab-loading');button?.removeAttribute('aria-busy');if(!job?.busy)lock(false);}};}
  function status(){
    $('save-status').textContent=conflict?'초안 버전 충돌':dirty?'입력 저장 대기 중…':job?'서버에 초안을 보관했습니다.':'';
    if(!job)return;
    $('checklist').textContent=job.can_export?'완성본을 내려받을 수 있습니다. 이미지가 없는 단계는 본문만 포함됩니다.':(job.missing||[]).join(' · ');
    $('export').disabled=!job.can_export;
    $('transcript-status').textContent=job.cue_count?`${job.cue_count}개 자막 · ${job.transcript_status}`:job.transcript_status==='unavailable'?'자막·음성 인식 사용 불가 · 직접 구간을 지정하세요.':'아직 자막을 준비하지 않았습니다.';
    $('suggest').disabled=$('resuggest').disabled=!job.cue_count||!job.article.steps.length;
    $('transcribe').disabled=!job.media_ready;
    const u=job.usage||{};
    if(u.calls!==undefined){const tokens=Number.isFinite(u.input_tokens)&&Number.isFinite(u.output_tokens)?`입력 ${u.input_tokens.toLocaleString()} · 출력 ${u.output_tokens.toLocaleString()} 토큰`:(u.calls?'토큰 수 확인 불가':'새 LLM 호출 없음');
      $('usage').textContent=`최근 작업 · LLM ${u.calls}회 · 캐시 ${u.cache_hits||0}회 · ${tokens}`+(u.duration_ms?` · ${(u.duration_ms/1000).toFixed(1)}초`:'')+(u.cost_usd!=null?` · 제공 비용 $${u.cost_usd.toFixed(4)}`:'');}
    const ex=$('exports');ex.replaceChildren();(job.exports||[]).slice().reverse().forEach(e=>{const a=document.createElement('a');a.href=fileURL('exports',e.file);a.textContent=`ZIP · ${new Date(e.created*1000).toLocaleString()}`;a.download='article.zip';ex.append(a);});
  }
  function write(path,data){
    const ident=job.id;
    const run=writes.catch(()=>{}).then(async()=>{
      if(job?.id!==ident)throw new Error('초안이 변경되었습니다. 다시 시도해주세요.');
      $('save-status').textContent='변경 내용을 저장하는 중…';$('save-status').setAttribute('aria-busy','true');
      try{return await json(`${apiRoot}/jobs/${ident}${path}`,'PATCH',{version:job.version,...data});}
      catch(e){if(e.status===409)e.draftConflict=true;throw e;}
      finally{$('save-status').removeAttribute('aria-busy');}
    });
    writes=run.then(next=>{
      if(job?.id===ident){const edited=job.article;job=next;if(dirty)job.article=edited;}
      return next;
    });
    // A consumer handles the error, but keep the queue from producing an unhandled rejection.
    writes.catch(()=>{});
    return writes;
  }
  async function flush(){
    clearTimeout(saveTimer);await saving;
    if(conflict)throw new Error('최신 초안을 다시 열기 전에 현재 입력을 복사해 보관해주세요.');
    if(!job||!dirty)return;
    const seq=sequence,content=structuredClone(job.article),jobId=job.id;
    const request=write('',{article:content});
    saving=request.then(next=>{if(job?.id!==jobId)return;const latest=job.article;job=next;if(sequence!==seq)job.article=latest;else dirty=false;status();}).catch(e=>{failure(e);throw e;});
    try{await saving;}finally{saving=Promise.resolve();}
    if(dirty)await flush();
  }
  function changed(){dirty=true;sequence++;status();clearTimeout(saveTimer);saveTimer=setTimeout(()=>flush().catch(failure),700);}
  async function patch(data){await flush();await write('',data);status();return job;}
  async function stepPatch(data){const sid=activeStep;await flush();if(!sid)throw new Error('조리 단계를 선택해주세요.');await write(`/steps/${sid}`,data);status();return job;}
  function showStage(n){
    if(n>0&&!job)return;stage=n;root.querySelectorAll('[data-ab-page]').forEach(p=>p.hidden=Number(p.dataset.abPage)!==n);
    root.querySelectorAll('[data-ab-stage]').forEach(b=>b.setAttribute('aria-current',Number(b.dataset.abStage)===n?'step':'false'));
    looping=false;video.pause();if(job)local('last',{id:job.id,stage:n});
    if(n===3)renderMedia();if(n===4)refreshPreview();lock(busy);status();
  }
  async function navigate(n){await flush();if(stage===3)await saveCrop();showStage(n);}
  root.querySelectorAll('[data-ab-stage]').forEach(b=>b.onclick=action(()=>navigate(Number(b.dataset.abStage))));
  root.querySelectorAll('[data-ab-next]').forEach(b=>b.onclick=action(()=>navigate(Number(b.dataset.abNext))));
  function getConfig(){return {template:$('template').value,instructions:$('instructions').value,backend:$('backend').value,model:$('model').value};}
  function fillConfig(c){$('template').value=c.template;$('instructions').value=c.instructions;$('backend').value=c.backend;$('model').value=c.model;}
  $('backend').onchange=()=>{const isCodex=$('backend').value==='codex';if(isCodex!==$('model').value.startsWith('gpt-'))$('model').value=isCodex?Array.from($('model').options).find(o=>o.value.startsWith('gpt-')).value:'sonnet';};
  $('template-apply').onclick=action(async()=>{lock(true);await patch({settings:getConfig()});refreshPreview();message('현재 초안에 템플릿과 생성 지시를 적용했습니다. LLM은 호출하지 않았습니다.');});
  $('template-save').onclick=action(async()=>{const r=await json('/api/helper/settings','PUT',{article:getConfig()});settings=r.article;message('다음 아티클의 기본 설정을 저장했습니다. 현재 초안에는 ‘현재 초안에 적용’을 사용하세요.');});
  $('template-reset').onclick=()=>{fillConfig({...getConfig(),...defaults});message('기본 템플릿을 불러왔습니다. 적용 또는 저장 버튼으로 반영하세요.');};
  async function history(){const r=await json(apiRoot+'/jobs');const sel=$('history');sel.replaceChildren(new Option('초안 선택…',''));r.jobs.forEach(j=>sel.add(new Option((j.dish||j.source.title)+' · '+new Date(j.updated*1000).toLocaleDateString(),j.id)));if(job)sel.value=job.id;}
  async function list(){const r=await json('/api/shorts?refresh=1');if(!r.sheet?.configured)throw new Error('쇼츠 현황판에서 구글 시트를 먼저 연결해주세요.');if(r.error)throw new Error(r.error);items=(r.items||[]).filter(i=>i.status==='uploaded');renderItems();}
  function renderItems(){
    const q=$('search').value.trim().toLowerCase(),el=$('items');el.replaceChildren();
    const matches=items.filter(i=>[i.dish_title,i.video?.title].some(v=>(v||'').toLowerCase().includes(q)));
    if(!matches.length){const p=document.createElement('p');p.className='ab-empty';p.textContent=q?'검색 결과가 없습니다.':'업로드 완료된 요리가 없습니다. 쇼츠 현황판의 상태를 확인해주세요.';el.append(p);}
    const counts=new Map();items.forEach(i=>counts.set(i.item_id,(counts.get(i.item_id)||0)+1));
    matches.forEach(i=>{const b=document.createElement('button');b.type='button';b.className='ab-item';const title=document.createElement('div'),small=document.createElement('small'),tag=document.createElement('span');title.textContent=i.dish_title||i.video?.title||'제목 없음';small.textContent=i.video?.title||'영상 제목 입력 필요';tag.textContent='업로드 완료';title.append(small);b.append(title,tag);b.disabled=!i.item_id||counts.get(i.item_id)>1;b.onclick=action(()=>selectItem(i.item_id));el.append(b);});
  }
  function fillSource(){const f={title:selected.item.video.title,description:selected.item.video.description,url:selected.item.platforms.youtube.url};const saved=local('fields.'+selected.connection+'.'+selected.item.item_id)||{};
    Object.entries(f).forEach(([k,v])=>{$('source-'+k).value=v||saved[k]||'';$('source-'+k).readOnly=!!v?.trim();});
    $('source').hidden=false;$('selected-title').textContent=selected.item.dish_title||'선택한 요리';
    const missing=Object.values(f).some(v=>!v?.trim());$('create').textContent=local(pendingKey())?'저장 결과 확인하고 다음 →':missing?'현황판에 저장하고 다음 →':'이 레시피로 시작 →';
    $('source-note').textContent=missing&&!selected.can_write?'누락값 저장에는 Apps Script 쓰기 연결이 필요합니다. 쇼츠 현황판에서 연결해주세요.':'필수 정보가 모두 있으면 현황판 쓰기 없이 시작합니다.';
  }
  async function selectItem(itemId){lock(true);selected=await json(`${apiRoot}/items/${encodeURIComponent(itemId)}`);fillSource();message('필수 정보를 확인한 뒤 시작하세요.');}
  ['title','description','url'].forEach(k=>$('source-'+k).oninput=()=>{if(selected)local('fields.'+selected.connection+'.'+selected.item.item_id,Object.fromEntries(['title','description','url'].map(x=>[x,$('source-'+x).value])));});
  $('source-refresh').onclick=action(async()=>{const key=pendingKey();selected=await json(`${apiRoot}/items/${encodeURIComponent(selected.item.item_id)}`);local(key,null);fillSource();message('최신 현황판 정보를 확인했습니다. 입력한 보완값은 유지됩니다.');});
  $('search').oninput=renderItems;$('refresh').onclick=action(list);
  $('create').onclick=action(async()=>{
    if(!selected)return;lock(true);
    const button=$('create'), originalLabel=button.textContent;
    function progress(label,detail){
      button.textContent=label;button.classList.add('ab-loading');button.setAttribute('aria-busy','true');
      message(detail);
    }
    try {
    const fields={};for(const [k,input,v] of [['title','title',selected.item.video.title],['description','description',selected.item.video.description],['youtube_url','url',selected.item.platforms.youtube.url]]){
      const value=$('source-'+input).value.trim();if(!value)throw new Error('제목·설명·유튜브 링크를 모두 입력해주세요.');if(!v?.trim())fields[k]=value;
    }
    let pending=local(pendingKey());
    if(pending||Object.keys(fields).length){
      if(!selected.can_write)throw new Error('쇼츠 현황판에서 Apps Script 쓰기 연결을 설정해주세요.');
      pending ||= {connection:selected.connection,revision:selected.revision,request_id:crypto.randomUUID(),fields};local(pendingKey(),pending);
      progress('정보 저장 중…','입력한 정보를 현황판에 저장하고 있습니다. 잠시만 기다려주세요.');
      selected=await json(`${apiRoot}/items/${encodeURIComponent(selected.item.item_id)}`,'PATCH',pending);local(pendingKey(),null);
    }
    progress('아티클 준비 중…','최신 레시피 정보를 확인하고 아티클 초안을 준비하고 있습니다. 잠시만 기다려주세요.');
    const created=await json(apiRoot+'/jobs','POST',{connection:selected.connection,item_id:selected.item.item_id});
    progress('편집 화면 여는 중…','초안이 준비되었습니다. 편집 화면을 불러오고 있습니다.');
    await openJob(created.id,1);message('초안을 만들었습니다. 아티클 생성 또는 직접 편집으로 시작하세요.');
    } finally {
      button.textContent=originalLabel;button.classList.remove('ab-loading');button.removeAttribute('aria-busy');
    }
  });
  async function openJob(ident,nextStage=1){await flush();await writes.catch(()=>{});job=await json(`${apiRoot}/jobs/${ident}`);writes=Promise.resolve();dirty=false;conflict=false;$('conflict').hidden=true;activeStep=job.article.steps[0]?.id||null;fillConfig(job.settings);renderContent();renderMedia();showStage(nextStage);message(job.message,job.status==='error');await history();if(job.busy){lock(true);poll();}else lock(false);}
  $('history').onchange=action(async()=>{if($('history').value)await openJob($('history').value);});
  $('new').onclick=action(async()=>{await flush();if(stage===3)await saveCrop();job=null;selected=null;activeStep=null;$('source').hidden=true;$('history').value='';local('last',null);showStage(0);await list();});
  $('delete').onclick=action(async()=>{if(!job||!confirm('이 아티클 초안과 이미지·내보내기 파일을 삭제할까요?'))return;await flush();await json(`${apiRoot}/jobs/${job.id}`,'DELETE',{version:job.version});job=null;dirty=false;local('last',null);await history();showStage(0);message('초안을 삭제했습니다.');});
  $('source-sync').onclick=action(async()=>{lock(true);const result=await json(`${apiRoot}/items/${encodeURIComponent(job.item_id)}?connection=${encodeURIComponent(job.connection)}`);await patch({source:{title:result.item.video.title,description:result.item.video.description,youtube_url:result.item.platforms.youtube.url}});renderMedia();message('최신 원본 정보를 반영했습니다. 본문은 유지되며 변경된 영상은 다시 준비해주세요.');});
  function renderContent(){
    if(!job)return;for(const k of ['title','intro','ingredients','closing'])$(k).value=job.article[k];renderSteps();renderPending();
    $('video-url').textContent=(job.local?'로컬 영상 · '+(job.video_name||'')+' / ':'')+job.source.youtube_url;status();
  }
  for(const k of ['title','intro','ingredients','closing'])$(k).oninput=()=>{if(!job)return;job.article[k]=$(k).value;changed();};
  function renderSteps(){const el=$('steps');el.replaceChildren();if(!job)return;
    job.article.steps.forEach((s,n)=>{const section=document.createElement('div');section.className='ab-step';const head=document.createElement('div');head.className='ab-step-head';const num=document.createElement('b');num.textContent=String(n+1).padStart(2,'0');const actions=document.createElement('div');
      [['↑',-1],['↓',1],['삭제',0]].forEach(([label,delta])=>{const b=document.createElement('button');b.type='button';b.className='ghost';b.textContent=label;b.setAttribute('aria-label',`${n+1}번 단계 ${label}`);b.disabled=(delta===-1&&n===0)||(delta===1&&n===job.article.steps.length-1);b.onclick=action(async()=>{if(!delta){if(!confirm(`${n+1}번 단계를 삭제할까요?`))return;job.article.steps.splice(n,1);}else [job.article.steps[n],job.article.steps[n+delta]]=[job.article.steps[n+delta],job.article.steps[n]];changed();await flush();renderSteps();});actions.append(b);});
      head.append(num,actions);section.append(head);
      for(const [key,label,tag] of [['title','단계 제목','input'],['body','조리 설명','textarea']]){const field=document.createElement(tag);field.value=s[key];field.id='ab-step-'+s.id+'-'+key;field.maxLength=key==='title'?500:10000;if(tag==='textarea')field.rows=3;const l=document.createElement('label');l.htmlFor=field.id;l.textContent=label;field.oninput=()=>{job.article.steps.find(x=>x.id===s.id)[key]=field.value;changed();};section.append(l,field);}el.append(section);
    });
  }
  $('add-step').onclick=action(async()=>{job.article.steps.push({id:id(),title:'',body:''});changed();await flush();renderSteps();$('steps').lastElementChild.querySelector('input').focus();});
  function renderPending(){
    const p=job.pending_article;$('pending').hidden=!p;if(p)$('pending-text').textContent=p.title+'\n\n'+p.intro+'\n\n'+p.ingredients+'\n\n'+p.steps.map((s,i)=>`${i+1}. ${s.title}\n${s.body}`).join('\n\n')+'\n\n'+p.closing;
    const el=$('questions');el.replaceChildren();el.hidden=!job.questions.length;if(job.questions.length){const h=document.createElement('h4');h.textContent='레시피 정보를 조금 더 알려주세요';el.append(h);job.questions.forEach((q,i)=>{const l=document.createElement('label'),t=document.createElement('textarea');t.id='ab-answer-'+i;t.rows=2;l.htmlFor=t.id;l.textContent=q;el.append(l,t);});const b=document.createElement('button');b.type='button';b.textContent='답변하고 생성 · LLM 사용';b.onclick=action(async()=>{const answers=job.questions.map((q,i)=>({question:q,answer:$('answer-'+i).value.trim()}));if(answers.some(a=>!a.answer))throw new Error('추가 질문에 답변해주세요.');await patch({answers:[...job.answers,...answers]});await operation('generate');});el.append(b);}
  }
  async function operation(name,params={}){await flush();await writes;lock(true);job=await json(`${apiRoot}/jobs/${job.id}/operations/${name}`,'POST',{version:job.version,...params});message(job.message);poll();}
  function poll(){clearTimeout(polling);const ident=job.id;polling=setTimeout(async()=>{try{const live=await json(`${apiRoot}/jobs/${ident}`);if(job?.id!==ident)return;job=live;status();message(job.message,job.status==='error'||job.status==='stale');if(job.busy){poll();return;}lock(false);renderPending();renderMedia();if(stage===4)refreshPreview();await history();if(job.operation==='export'&&job.status==='ready'){const last=job.exports.at(-1);if(last){const a=document.createElement('a');a.href=fileURL('exports',last.file);a.download='article.zip';a.click();}}}catch(e){lock(false);message('상태 확인 실패: '+e.message+' 초안을 다시 열면 작업 상태를 확인할 수 있습니다.',true);}},800);}
  $('generate').onclick=action(async()=>{await patch({settings:getConfig()});await operation('generate');});
  $('regenerate').onclick=action(async()=>{await patch({settings:getConfig()});await operation('generate',{force:true});});
  $('apply').onclick=action(async()=>{await flush();job=await json(`${apiRoot}/jobs/${job.id}/apply`,'POST',{version:job.version});activeStep=job.article.steps[0]?.id||null;renderContent();message('아티클 후보를 적용했습니다. 조리 단계별 구간과 이미지를 선택해주세요.');});
  $('prepare').onclick=action(()=>operation('prepare'));$('transcribe').onclick=action(()=>operation('transcribe'));$('suggest').onclick=action(()=>operation('suggest'));$('resuggest').onclick=action(()=>operation('suggest',{force:true}));
  $('file').onchange=action(async()=>{const file=$('file').files[0];if(!file)return;await flush();lock(true);message('로컬 영상을 업로드하는 중…');const upload=await json('/api/hooks/uploads','POST',{name:file.name,size:file.size});try{const size=upload.chunk_size;for(let start=0,index=0;start<file.size;start+=size,index++){const part=await file.slice(start,start+size).arrayBuffer();const digest=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',part))).map(n=>n.toString(16).padStart(2,'0')).join('');const r=await fetch(`/api/hooks/uploads/${upload.id}/chunks/${index}`,{method:'PUT',headers:{'Content-Type':'application/octet-stream','X-Chunk-SHA256':digest},body:part});if(!r.ok)throw new Error((await r.json()).error||'영상 업로드 실패');message(`영상 업로드 ${Math.round(Math.min(file.size,start+size)/file.size*100)}%`);}const result=await json(`/api/hooks/uploads/${upload.id}/complete`,'POST',{});job=await json(`${apiRoot}/jobs/${job.id}/asset`,'POST',{version:job.version,asset_id:result.asset_id});renderContent();await operation('prepare');}catch(e){await json(`/api/hooks/uploads/${upload.id}`,'DELETE').catch(()=>{});throw e;}finally{$('file').value='';}});
  function fileURL(kind,name){return `${apiRoot}/jobs/${job.id}/files/${kind}/${name}`;}
  function sel(){return job?.selections[activeStep]||{};}
  function baseCrop(){const w=job.info.width,h=job.info.height,[top,bottom]=job.excluded,lo=Math.ceil(top*h),hi=Math.floor((1-bottom)*h),size=Math.min(w,hi-lo);return{x:(w-size)/2/w,y:(lo+(hi-lo-size)/2)/h,size:size/w};}
  function clampCrop(){if(!crop||!job.info)return;const w=job.info.width,h=job.info.height,[top,bottom]=job.excluded;crop.size=Math.max(2/w,Math.min(crop.size,1,(Math.floor((1-bottom)*h)-Math.ceil(top*h))/w));crop.x=Math.max(0,Math.min(crop.x,1-crop.size));crop.y=Math.max(Math.ceil(top*h)/h,Math.min(crop.y,(Math.floor((1-bottom)*h)-crop.size*w)/h));}
  function renderMedia(){if(!job)return;
    $('video-url').textContent=(job.local?'로컬 영상 · '+(job.video_name||'')+' / ':'')+job.source.youtube_url;
    const selector=$('step-select');selector.replaceChildren();job.article.steps.forEach((s,i)=>selector.add(new Option(`${i+1}. ${s.title||'제목 없는 단계'}${job.selections[s.id]?.confirmed?' · 확정':''}`,s.id)));
    if(!job.article.steps.some(s=>s.id===activeStep))activeStep=job.article.steps[0]?.id||null;selector.value=activeStep||'';
    $('step-body').textContent=job.article.steps.find(s=>s.id===activeStep)?.body||'글 작성에서 조리 단계를 추가해주세요.';
    if(job.media_ready){const url=`${apiRoot}/jobs/${job.id}/video`;if(video.getAttribute('src')!==url){video.src=url;video.load();}}
    else {video.removeAttribute('src');video.load();}
    const selection=sel(),duration=job.info?.duration||0;selectedTime=selection.time??null;
    $('start').value=selection.range?.[0]??0;$('end').value=selection.range?.[1]??Number(duration.toFixed(3));$('timeline').max=Math.max(0,duration-.001);
    $('top').value=job.excluded[0]*100;$('bottom').value=job.excluded[1]*100;
    crop=job.info?structuredClone(selection.crop||baseCrop()):null;cropDirty=false;
    const opts=$('suggestions');opts.replaceChildren();(job.suggestions[activeStep]||[]).forEach((span,i)=>{const b=document.createElement('button');b.type='button';b.className='ghost';b.textContent=`구간 후보 ${i+1} · ${span[0].toFixed(1)}–${span[1].toFixed(1)}초`;b.onclick=action(async()=>{await stepPatch({range:span});renderMedia();seek(span[0]);});opts.append(b);});
    const candidates=$('candidate-images');candidates.replaceChildren();(selection.candidates||[]).forEach(c=>{const b=document.createElement('button');b.type='button';const img=document.createElement('img');img.src=fileURL('candidates',c.image);img.alt=`${c.time.toFixed(1)}초 후보`;b.append(img,document.createTextNode(c.time.toFixed(1)+'초'));b.onclick=action(async()=>{seek(c.time);await stepPatch({time:c.time});selectedTime=c.time;imageStatus();});candidates.append(b);});
    $('still').hidden=!selection.image;if(selection.image)$('still').src=fileURL('images',selection.image);
    for(const k of ['range','loop','candidates','capture','use-time','prev','next','exclude'])$(k).disabled=!job.media_ready||!activeStep;
    if(job.media_ready&&selectedTime!==null){if(video.readyState>=1)seek(selectedTime);else video.addEventListener('loadedmetadata',()=>seek(selectedTime),{once:true});}
    cropControls();imageStatus();status();draw();
  }
  function imageStatus(){const s=sel();$('image-status').textContent=s.confirmed&&!cropDirty?'이미지 확정됨':s.image?(s.dirty||cropDirty?'구도가 변경되었습니다. 이미지를 다시 추출해주세요.':'조리 장면과 잘 맞는지 확인하고 확정해주세요.'):'구간과 시점을 선택한 뒤 이미지를 추출하세요.';$('confirm').disabled=!s.image||s.dirty||cropDirty||s.time==null;}
  $('step-select').onchange=action(async()=>{const next=$('step-select').value;await saveCrop();activeStep=next;looping=false;video.pause();renderMedia();});
  function seek(time){if(!Number.isFinite(video.duration))return;video.pause();video.currentTime=Math.max(0,Math.min(video.duration-.001,time));}
  video.addEventListener('timeupdate',()=>{if(looping&&sel().range&&video.currentTime>=sel().range[1])video.currentTime=sel().range[0];$('time').textContent=video.currentTime.toFixed(1)+'초';$('timeline').value=video.currentTime;draw();});
  video.addEventListener('seeked',draw);video.addEventListener('loadeddata',draw);video.addEventListener('ended',()=>{if(looping&&sel().range){video.currentTime=sel().range[0];video.play().catch(()=>{});}});
  video.addEventListener('error',()=>{if(video.getAttribute('src'))message('영상을 재생하지 못했습니다. 영상 준비 단계에서 다시 준비해주세요.',true);});
  $('timeline').oninput=e=>{looping=false;seek(Number(e.target.value));};$('prev').onclick=()=>seek(video.currentTime-.1);$('next').onclick=()=>seek(video.currentTime+.1);
  $('range').onclick=action(async()=>{await stepPatch({range:[Number($('start').value),Number($('end').value)]});renderMedia();message('구간을 저장했습니다. 이 안에서 장면을 선택하세요.');});
  $('loop').onclick=action(async()=>{await stepPatch({range:[Number($('start').value),Number($('end').value)]});looping=true;video.currentTime=sel().range[0];await video.play();});
  $('use-time').onclick=action(async()=>{await stepPatch({range:[Number($('start').value),Number($('end').value)],time:video.currentTime});selectedTime=video.currentTime;imageStatus();message('시점을 선택했습니다. 구도를 조절한 뒤 이미지를 추출하세요.');});
  $('candidates').onclick=action(async()=>{await stepPatch({range:[Number($('start').value),Number($('end').value)]});await operation('candidates',{step_id:activeStep});});
  function cropControls(){if(!crop||!job.info)return;$('crop-x').value=(crop.x*100).toFixed(1);$('crop-y').value=(crop.y*100).toFixed(1);const z=Math.round(baseCrop().size/crop.size*100);$('zoom').value=z;$('zoom-value').textContent=z+'%';}
  function cropChanged(){cropDirty=true;clampCrop();cropControls();draw();imageStatus();clearTimeout(saveTimer);saveTimer=setTimeout(()=>saveCrop().catch(failure),700);}
  async function saveCrop(){if(!cropDirty||!job||!activeStep||!job.info||job.busy||conflict)return;const c=structuredClone(crop);await stepPatch({crop:c});if(JSON.stringify(c)===JSON.stringify(crop))cropDirty=false;imageStatus();}
  $('zoom').oninput=()=>{if(!crop)return;const w=job.info.width,h=job.info.height,old=crop.size;crop.size=baseCrop().size/(Number($('zoom').value)/100);crop.x+=(old-crop.size)/2;crop.y+=(old-crop.size)*w/h/2;cropChanged();};
  $('crop-x').onchange=()=>{if(crop){crop.x=Number($('crop-x').value)/100;cropChanged();}};$('crop-y').onchange=()=>{if(crop){crop.y=Number($('crop-y').value)/100;cropChanged();}};
  $('crop-reset').onclick=()=>{if(job?.info){crop=baseCrop();cropChanged();}};
  function draw(){if(!job?.info||!crop)return;const w=job.info.width,h=job.info.height;canvas.width=Math.min(540,w);canvas.height=Math.round(canvas.width*h/w);ctx.fillStyle='#101217';ctx.fillRect(0,0,canvas.width,canvas.height);if(video.readyState>=2)ctx.drawImage(video,0,0,canvas.width,canvas.height);const [top,bottom]=job.excluded;ctx.fillStyle='#b7373766';ctx.fillRect(0,0,canvas.width,top*canvas.height);ctx.fillRect(0,(1-bottom)*canvas.height,canvas.width,bottom*canvas.height);ctx.strokeStyle='#e6c99f';ctx.lineWidth=3;ctx.strokeRect(crop.x*canvas.width,crop.y*canvas.height,crop.size*canvas.width,crop.size*canvas.width);}
  canvas.onpointerdown=e=>{if(!crop||busy)return;canvas.setPointerCapture(e.pointerId);drag={x:e.clientX,y:e.clientY,crop:{...crop}};};
  canvas.onpointermove=e=>{if(!drag)return;const r=canvas.getBoundingClientRect();crop={...drag.crop,x:drag.crop.x+(e.clientX-drag.x)/r.width,y:drag.crop.y+(e.clientY-drag.y)/r.height};cropChanged();};
  canvas.onpointerup=canvas.onpointercancel=()=>{drag=null;saveCrop().catch(failure);};
  $('exclude').onclick=action(async()=>{await patch({excluded:[Number($('top').value)/100,Number($('bottom').value)/100]});renderMedia();if(Object.values(job.selections).some(v=>v.image&&v.time!=null))await operation('capture');else message('상하 제외 영역을 적용했습니다.');});
  $('capture').onclick=action(async()=>{if(video.readyState<2)throw new Error('영상 프레임을 불러온 뒤 다시 시도해주세요.');looping=false;video.pause();await stepPatch({range:[Number($('start').value),Number($('end').value)],time:video.currentTime,crop});cropDirty=false;await operation('capture',{step_id:activeStep});});
  $('confirm').onclick=action(async()=>{await saveCrop();await stepPatch({confirm:true});renderMedia();message('이미지를 확정했습니다. 다음 단계의 장면을 선택하세요.');});
  const removeImage=document.createElement('button');removeImage.type='button';removeImage.className='ghost';removeImage.textContent='이 단계는 이미지 없이 사용';$('confirm').after(removeImage);
  removeImage.onclick=action(async()=>{clearTimeout(saveTimer);cropDirty=false;await stepPatch({remove_image:true});renderMedia();message('이 단계는 이미지 없이 내보냅니다.');});
  function refreshPreview(){if(job){$('preview').setAttribute('aria-busy','true');$('preview-refresh').textContent='미리보기 불러오는 중…';$('preview-refresh').classList.add('ab-loading');$('preview').src=`${apiRoot}/jobs/${job.id}/preview?v=${job.version}`;}status();}
  $('preview').onload=$('preview').onerror=()=>{$('preview').removeAttribute('aria-busy');$('preview-refresh').textContent='미리보기 새로고침';$('preview-refresh').classList.remove('ab-loading');};
  async function copy(value){try{await navigator.clipboard.writeText(value);}catch{const t=document.createElement('textarea');t.value=value;t.style.cssText='position:fixed;left:-9999px';document.body.append(t);t.select();try{if(!document.execCommand('copy'))throw new Error('텍스트를 복사하지 못했습니다.');}finally{t.remove();}}message('본문을 복사했습니다. 이미지 위치에 해당 파일을 첨부해주세요.');}
  const copyDraft=action(async()=>{await flush();await copy((await json(`${apiRoot}/jobs/${job.id}/text`)).text);});$('copy').onclick=$('copy-draft').onclick=copyDraft;
  $('preview-refresh').onclick=action(async()=>{await flush();refreshPreview();});$('export').onclick=action(()=>operation('export'));
  $('rescue').onclick=action(()=>copy(JSON.stringify({article:job?.article,source:selected?Object.fromEntries(['title','description','url'].map(k=>[k,$('source-'+k).value])):job?.source},null,2)));
  $('reload').onclick=action(async()=>{if(!job)return;clearTimeout(saveTimer);dirty=false;conflict=false;$('conflict').hidden=true;await openJob(job.id,stage);});
  window.addEventListener('beforeunload',e=>{if(dirty||cropDirty){e.preventDefault();e.returnValue='';}});
  async function init(){if(initialized)return;initialized=true;message('설정과 요리 목록을 불러오는 중입니다…');lock(true);try{const data=await json('/api/helper/settings');settings=data.article;defaults=data.article_defaults;fillConfig(settings);await history();await list();const last=local('last');if(last?.id){try{await openJob(last.id,last.stage||1);}catch(e){local('last',null);message('이전 초안을 열지 못했습니다: '+e.message,true);}}else message('요리를 선택해 시작하세요. 단계별 이미지는 선택 사항입니다.');}catch(e){initialized=false;failure(e);}finally{if(!job?.busy)lock(false);}}
  document.addEventListener('helper:tool',e=>{if(e.detail==='article')init();});if(location.hash==='#article')init();
})();
