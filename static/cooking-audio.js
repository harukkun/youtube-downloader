'use strict';
(() => {
  const $ = id => document.getElementById('ca-'+id);
  let state=null, timer, uploadId, aborter, uploading=false, signature='', mediaUrl='', segment=null, pending=null;
  const kinds={prep:'손질',ingredient:'재료·분량',mix:'섞기',heat:'불 조절',timing:'시간',doneness:'완성 상태',sequence:'순서'};
  const origins={uploaded:'업로드 SRT',embedded:'영상 자막',youtube_auto:'자동자막',youtube_manual:'수동자막',asr:'음성 인식',manual:'직접 입력',edited:'직접 편집'};
  const el=(tag,text,cls)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;};
  const error=e=>{$('error').textContent=e.message||String(e);$('error').hidden=false;};
  const action=fn=>async()=>{$('error').hidden=true;try{await fn();}catch(e){error(e);}};
  async function request(path,method='GET',data,base='/api/cooking-audio') {
    const options={method,headers:{}};
    if(data!==undefined){options.headers['Content-Type']='application/json';options.body=JSON.stringify(data);}
    const r=await fetch(base+path,options),body=await r.json();if(!r.ok)throw new Error(body.error||'요청 실패');return body;
  }
  const path=suffix=>'/jobs/'+state.id+(suffix||'');
  async function mutate(suffix,method='POST',data={}){apply(await request(path(suffix),method,data));}
  function urlState(){if(state&&location.hash==='#cooking-audio'){const u=new URL(location.href);u.searchParams.set('cooking_job',state.id);history.replaceState(null,'',u);}}
  function switchPanel(){const cooking=location.hash==='#cooking-audio';document.getElementById('hooks').hidden=cooking;document.getElementById('cooking-audio').hidden=!cooking;for(const [id,active] of [['hooksTool',!cooking],['cookingTool',cooking]]){const n=document.getElementById(id);n.classList.toggle('active',active);if(active)n.setAttribute('aria-current','true');else n.removeAttribute('aria-current');}document.getElementById(cooking?'player':'ca-player').pause();urlState();}
  window.addEventListener('hashchange',switchPanel);switchPanel();
  function apply(next){
    if(!state||state.id!==next.id){segment=null;pending=null;mediaUrl='';$('player').pause();$('player').removeAttribute('src');$('player').load();$('player').hidden=true;}
    state=next;localStorage.setItem('cooking-audio.job',state.id);urlState();
    if(Array.from($('history').options).some(o=>o.value===state.id))$('history').value=state.id;
    const busy=state.busy||uploading;
    $('badge').textContent=state.busy?'처리 중':state.status==='error'?'확인 필요':state.status==='interrupted'?'재시도 필요':'조리 대사';$('badge').classList.toggle('is-loading',state.busy);
    $('status').textContent=state.message+(state.source_missing?' · 원본 파일을 다시 연결해주세요.':'');
    for(const id of ['prepare','preview','compat','offset-save','reference-save','reference-fetch','asr','asr-full','add','srt-save','reconnect'])$(id).disabled=busy;
    for(const id of ['create','import','new','history'])$(id).disabled=busy;
    $('cancel').hidden=!state.busy;$('analyze').disabled=busy||!state.transcript_count;
    $('export').disabled=busy||!state.candidates.some(c=>c.selected)||(state.needs_alignment&&!state.alignment_confirmed);
    $('merge').disabled=busy||state.candidates.filter(c=>c.selected).length<2;
    $('alignment-label').hidden=!state.needs_alignment;$('alignment').disabled=busy||!state.preview_kind;$('alignment').checked=state.alignment_confirmed;
    $('warning').hidden=!state.duration_warning;$('warning').textContent='파일과 YouTube 영상 길이가 다릅니다. 발언과 자막 시간을 확인해주세요.';
    if(document.activeElement!==$('offset'))$('offset').value=state.offset;
    if(document.activeElement!==$('reference'))$('reference').value=state.reference;
    $('reference-enabled').checked=state.reference_enabled;
    const u=state.usage||{};$('usage').textContent=u.calls===undefined?'':`이번 분석 AI ${u.calls}회 · 저장 결과 ${u.cache_hits||0}구간 재사용`+(u.tokens_available&&u.calls?` · 입력 ${u.input_tokens} / 출력 ${u.output_tokens} 토큰`:u.calls?` · 입력 ${u.input_chars}자`:'');
    $('selection').textContent=`${state.candidates.filter(c=>c.selected).length}개 선택 · 합계 ${state.selected_duration.toFixed(1)}초`;
    $('overlap').hidden=!state.overlapping.length;
    const sig=JSON.stringify([state.candidates,busy]);if(sig!==signature){signature=sig;renderCandidates(busy);}
    renderResults();
    if(state.preview_kind&&!state.busy){const src='/api/cooking-audio'+path('/media')+'?kind='+state.preview_kind;if(mediaUrl!==src){mediaUrl=src;$('player').src=src;$('player').hidden=false;}else if(pending){play(pending);pending=null;}}
    clearTimeout(timer);if(state.busy)timer=setTimeout(()=>request(path()).then(apply).catch(error),1000);
  }
  function formatTime(value){
    const centiseconds=Math.round(Math.abs(value)*100),minutes=Math.floor(centiseconds/6000);
    return (value<0?'-':'')+String(minutes).padStart(2,'0')+':'+String(Math.floor(centiseconds/100)%60).padStart(2,'0')+':'+String(centiseconds%100).padStart(2,'0');
  }
  function setTime(input,value){input.value=formatTime(value);input.dataset.originalTime=String(value);input.dataset.formattedTime=input.value;}
  function readTime(input){
    // Preserve millisecond precision when only the text or another field was edited.
    if(input.value===input.dataset.formattedTime)return Number(input.dataset.originalTime);
    const match=/^(\d+):([0-5]\d):(\d{2})$/.exec(input.value.trim());
    if(!match)throw new Error('시간을 분:초:소수 두 자리 형식으로 입력해주세요. 예: 01:11:18');
    return Number(match[1])*60+Number(match[2])+Number(match[3])/100;
  }
  function button(text,fn,busy=false){const b=el('button',text,'secondary');b.disabled=busy;b.onclick=action(fn);return b;}
  function renderCandidates(busy){
    $('candidates').replaceChildren();
    if(!state.candidates.length){$('candidates').textContent='자동 후보가 없으면 구간을 지정해 직접 추가할 수 있습니다.';return;}
    state.candidates.forEach((c,index)=>{
      const card=el('div',undefined,'candidate'),head=el('div',undefined,'candidate-head'),check=el('input');check.type='checkbox';check.checked=c.selected;check.disabled=busy;check.setAttribute('aria-label',c.text+' 선택');check.onchange=action(()=>mutate('/candidates/'+c.id,'PATCH',{selected:check.checked}));head.append(check,el('strong',`${index+1}. ${c.text}`));card.append(head);
      card.append(el('p',`${kinds[c.kind]||'조리 대사'} · ${origins[c.origin]||c.origin} · ${(c.end-c.start).toFixed(1)}초${c.reaction?' · 리액션 포함':''}${c.check?' · 분량·발언 확인 필요':''}${c.alternative?' · 부분 인식 대안':''}${c.duplicate?' · 반복 발언 확인':''}${c.text_review?' · 분할·병합 문구 확인':''}`,'hint'));
      if(c.text!==c.original_text)card.append(el('p','원문: '+c.original_text,'hint'));
      const text=el('textarea');text.value=c.text;text.rows=2;text.maxLength=2000;text.disabled=busy;text.setAttribute('aria-label','표시 문구');card.append(text);
      const controls=el('div',undefined,'candidate-controls'),inputs={};
      for(const [k,label] of [['start','시작 (분:초:소수)'],['end','종료 (분:초:소수)']]){const wrap=el('label',label),input=el('input');input.type='text';input.placeholder='00:00:00';setTime(input,c[k]);input.disabled=busy;wrap.append(input);controls.append(wrap);inputs[k]=input;}
      controls.append(button('문구·구간 저장',()=>mutate('/candidates/'+c.id,'PATCH',{text:text.value,start:readTime(inputs.start),end:readTime(inputs.end)}),busy),playbackButton(c,busy),button('현재 재생 위치에서 분할',()=>mutate('/candidates/'+c.id+'/split','POST',{time:$('player').currentTime}),busy||!state.preview_kind),button('삭제',()=>mutate('/candidates/'+c.id,'DELETE'),busy));
      for(const [label,delta] of [['↑',-1],['↓',1]])controls.append(button(label,()=>{const ids=state.candidates.map(x=>x.id);[ids[index],ids[index+delta]]=[ids[index+delta],ids[index]];return mutate('','PATCH',{order:ids});},busy||index+delta<0||index+delta>=state.candidates.length));
      card.append(controls);$('candidates').append(card);
    });
    syncPlaybackButtons();
  }
  function playbackButton(c,busy){
    const b=button('구간 재생',async()=>{
      if(pending?.id===c.id||(segment?.id===c.id&&!$('player').paused)){pending=null;segment=null;$('player').pause();syncPlaybackButtons();return;}
      pending=c;syncPlaybackButtons();
      if(state.preview_kind){pending=null;play(c);}else {try{await mutate('/preview');}catch(e){pending=null;syncPlaybackButtons();throw e;}}
    },busy&&pending?.id!==c.id);
    b.dataset.segmentId=c.id;return b;
  }
  function syncPlaybackButtons(){
    for(const b of $('candidates').querySelectorAll('[data-segment-id]')){
      const playing=pending?.id===b.dataset.segmentId||(segment?.id===b.dataset.segmentId&&!$('player').paused);
      b.textContent=playing?'구간 재생 멈춤':'구간 재생';b.setAttribute('aria-pressed',String(playing));
    }
  }
  function play(c){segment={id:c.id,start:c.start,end:c.end};$('player').currentTime=c.start;$('player').play().catch(e=>{if(segment?.id===c.id)segment=null;syncPlaybackButtons();error(e);});syncPlaybackButtons();}
  for(const event of ['play','pause','ended','emptied'])$('player').addEventListener(event,syncPlaybackButtons);
  $('player').onloadedmetadata=()=>{if(pending){play(pending);pending=null;}};
  $('player').ontimeupdate=()=>{if(segment&&$('player').currentTime>=segment.end){if($('loop').checked)$('player').currentTime=segment.start;else{$('player').pause();segment=null;}}};
  function renderResults(){
    $('results').replaceChildren();$('results').setAttribute('aria-busy',String(state.busy&&state.status==='exporting'));
    for(const batch of [...state.exports].reverse()){
      const box=el('div',undefined,'result-batch'),failed=batch.clips.filter(c=>c.status==='error');box.append(el('strong',new Date(batch.created*1000).toLocaleString('ko-KR')));
      const href=name=>'/api/cooking-audio'+path('/exports/'+batch.id+'/'+encodeURIComponent(name));
      if(!batch.complete){const p=el('p',undefined,'batch-progress');p.setAttribute('role','status');if(state.busy&&batch.id===state.exports.at(-1).id)p.append(el('span',undefined,'hook-spinner'));p.append(el('span',`${state.busy?'음원 준비 중':'준비 중단'} · ${batch.clips.length}/${batch.total}개 처리 완료`));box.append(p);const progress=el('progress');progress.max=batch.total;progress.value=batch.clips.length;box.append(progress,button('전체 ZIP · 준비 중',()=>{},true));}
      else {const links=el('div',undefined,'result-links');for(const [name,label] of [['dialogues.zip',failed.length?'성공한 음원 ZIP':'전체 ZIP'],['dialogues.txt','대사 TXT'],['dialogues.json','상세 JSON']]){const a=el('a',label);a.href=href(name);links.append(a);}box.append(links);}
      for(const c of batch.clips){const row=el('p');if(c.status==='finished'){const a=el('a',c.text+' · MP3 ↓');a.href=href(c.filename);row.append(a);}else row.textContent=c.text+' · '+c.error;box.append(row);}
      const retry=batch.complete?failed.map(c=>c.id):(batch.requested||[]).filter(id=>!batch.clips.some(c=>c.id===id&&c.status==='finished'));
      if(retry.length)box.append(button('실패·미완료 '+retry.length+'개 재시도',()=>mutate('/export','POST',{candidate_ids:retry}),state.busy));
      $('results').append(box);
    }
  }
  async function upload(file){
    const cfg=await request('/config','GET',undefined,'/api/hooks');if(file.size>cfg.max_video_bytes)throw new Error('영상 용량 제한을 초과합니다.');
    const reg=await request('/uploads','POST',{name:file.name,size:file.size},'/api/hooks');uploadId=reg.id;aborter=new AbortController();$('upload').hidden=false;$('upload-cancel').hidden=false;
    try{
      for(let start=0,i=0;start<file.size;start+=reg.chunk_size,i++){
        const bytes=await file.slice(start,start+reg.chunk_size).arrayBuffer(),hash=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes)),b=>b.toString(16).padStart(2,'0')).join('');
        for(let attempt=0;;attempt++){try{const r=await fetch(`/api/hooks/uploads/${uploadId}/chunks/${i}`,{method:'PUT',headers:{'Content-Type':'application/octet-stream','X-Chunk-SHA256':hash},body:bytes,signal:aborter.signal});if(!r.ok)throw new Error((await r.json()).error);break;}catch(e){if(attempt||aborter.signal.aborted)throw e;}}
        $('upload').value=Math.min(100,(start+reg.chunk_size)/file.size*100);
      }
      const result=await request('/uploads/'+uploadId+'/complete','POST',{},'/api/hooks');uploadId=null;return result.asset_id;
    }finally{if(uploadId)await request('/uploads/'+uploadId,'DELETE',undefined,'/api/hooks').catch(()=>{});uploadId=null;$('upload').hidden=true;$('upload-cancel').hidden=true;}
  }
  async function srt(){const file=$('srt').files[0];if(!file)return;const cfg=await request('/config','GET',undefined,'/api/hooks');if(file.size>cfg.max_srt_bytes)throw new Error('자막 용량 제한을 초과합니다.');const r=await fetch('/api/cooking-audio'+path('/subtitles'),{method:'POST',body:file});const data=await r.json();if(!r.ok)throw new Error(data.error);apply(data);$('srt').value='';}
  async function loadHistory(){for(const [id,base] of [['history','/api/cooking-audio'],['hook','/api/hooks']]){const data=await request('/jobs','GET',undefined,base);$(id).replaceChildren(new Option('작업 선택',''));for(const j of data.jobs)$(id).append(new Option(j.video_name||j.youtube_title||j.url,j.id));}if(state)$('history').value=state.id;}
  $('upload-cancel').onclick=()=>aborter?.abort();
  $('create').onclick=action(async()=>{uploading=true;for(const id of ['create','import','new','history'])$(id).disabled=true;if(state)apply(state);try{const file=$('file').files[0];const asset=file?await upload(file):null;apply(await request('/jobs','POST',{asset_id:asset,url:$('url').value.trim()}));await srt();await mutate('/prepare');await loadHistory();$('file').value='';}finally{uploading=false;for(const id of ['create','import','new','history'])$(id).disabled=false;if(state)apply(state);}});
  $('import').onclick=action(async()=>{if(!$('hook').value)throw new Error('후킹 작업을 선택해주세요.');apply(await request('/jobs','POST',{hook_job:$('hook').value}));await mutate('/prepare');await loadHistory();});
  $('reconnect').onclick=action(async()=>{const file=$('file').files[0];if(!file)throw new Error('같은 원본 파일을 선택해주세요.');uploading=true;apply(state);try{const asset=await upload(file);await mutate('/source','POST',{asset_id:asset});}finally{uploading=false;apply(state);}});
  $('srt-save').onclick=action(srt);
  $('history').onchange=action(async()=>{if($('history').value){segment=null;pending=null;apply(await request('/jobs/'+$('history').value));}});
  $('new').onclick=()=>{state=null;localStorage.removeItem('cooking-audio.job');const u=new URL(location.href);u.searchParams.delete('cooking_job');u.hash='cooking-audio';location.href=u;location.reload();};
  $('prepare').onclick=action(()=>mutate('/prepare'));$('analyze').onclick=action(()=>mutate('/analyze'));
  $('preview').onclick=action(()=>mutate('/preview'));$('compat').onclick=action(()=>mutate('/preview','POST',{force:true}));
  $('cancel').onclick=action(()=>mutate('/cancel'));$('offset-save').onclick=action(()=>mutate('','PATCH',{offset:Number($('offset').value)}));
  $('alignment').onchange=action(()=>mutate('','PATCH',{alignment_confirmed:$('alignment').checked}));
  $('reference-fetch').onclick=action(()=>mutate('/references'));$('reference-save').onclick=action(()=>mutate('','PATCH',{reference:$('reference').value,reference_enabled:$('reference-enabled').checked}));
  const range=()=>({start:readTime($('start')),end:readTime($('end'))});
  $('asr').onclick=action(()=>mutate('/asr','POST',range()));$('asr-full').onclick=action(()=>mutate('/asr'));
  $('mark-start').onclick=()=>{setTime($('start'),$('player').currentTime);};$('mark-end').onclick=()=>{setTime($('end'),$('player').currentTime);};
  $('add').onclick=action(()=>mutate('/candidates','POST',{...range(),text:$('manual').value}));
  const selected=()=>state.candidates.filter(c=>c.selected).map(c=>c.id);
  $('merge').onclick=action(()=>mutate('/merge','POST',{candidate_ids:selected()}));$('export').onclick=action(()=>mutate('/export','POST',{candidate_ids:selected()}));
  (async()=>{const cfg=await request('/config');$('asr-status').textContent=cfg.asr_available?'로컬 음성 인식 사용 가능':'로컬 음성 인식 환경 설정 필요 · SRT/YouTube 자막은 바로 사용할 수 있습니다.';await loadHistory();const id=new URL(location.href).searchParams.get('cooking_job')||localStorage.getItem('cooking-audio.job');if(id)apply(await request('/jobs/'+id));})().catch(error);
})();
