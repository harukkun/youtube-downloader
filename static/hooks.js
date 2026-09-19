'use strict';
(() => {
  const $ = id => document.getElementById(id);
  let state = null, config, timer, uploadId, controller, uploading = false, activeSegment = null, pendingSegment = null;
  let candidateSignature = '', exportSignature = '', mediaUrl = '';
  const kinds = {taste:'맛 평가', recommendation:'추천', confidence:'자신감·예고', manual:'직접 입력'};
  const statuses = {created:'원본 연결',preparing:'자막 확인 중',needs_input:'입력 필요',no_subtitles:'자막 없음',subtitles_ready:'자막 준비 완료',analyzing:'후보 분석 중',previewing:'영상 준비 중',exporting:'클립 추출 중',ready:'준비 완료',error:'확인 필요',interrupted:'재시도 필요'};
  const showError = error => { $('error').textContent = error?.message || String(error); $('error').hidden = false; };
  const clearError = () => { $('error').hidden = true; };
  async function api(path, method='GET', data) {
    const opts = {method,headers:{}};
    if (data !== undefined) { opts.headers['Content-Type']='application/json'; opts.body=JSON.stringify(data); }
    const response = await fetch('/api/hooks'+path,opts);
    const body = await response.json().catch(()=>({error:'서버 응답을 읽지 못했습니다.'}));
    if (!response.ok) throw new Error(body.error || `요청 실패 (${response.status})`);
    return body;
  }
  const jobPath = suffix => '/jobs/'+state.id+(suffix||'');
  const el = (tag,text,cls) => {const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;};
  function time(t) {const n=Math.max(0,t);return `${Math.floor(n/60).toString().padStart(2,'0')}:${(n%60).toFixed(1).padStart(4,'0')}`;}
  function action(fn) {return async()=>{clearError();try{await fn();}catch(e){showError(e);}};}
  function apply(next) {
    state=next;localStorage.setItem('hooks.job',state.id);if(location.hash!=='#cooking-audio'){const u=new URL(location.href);u.searchParams.set('job',state.id);u.hash='hooks';window.history.replaceState(null,'',u);}
    const busy=state.busy||uploading, hasCues=state.cue_count>0;
    $('statusBadge').textContent=statuses[state.status]||state.status;
    $('statusBadge').classList.toggle('is-loading',state.busy);
    $('results').setAttribute('aria-busy',String(state.busy&&state.status==='exporting'));
    $('export').textContent=state.busy&&state.status==='exporting'?'파일 준비 중…':'선택한 클립 추출';
    $('status').textContent=state.message;
    $('sourceBadge').textContent=hasCues?`${state.cue_count}개 발언 · ${state.subtitle_source==='youtube_auto'?'한국어 자동자막':state.subtitle_source==='youtube_manual'?'한국어 수동자막':state.subtitle_source==='embedded'?'영상 내 자막':'업로드 SRT'}`:'';
    const transcript=state.transcript||'';
    if($('transcriptText').value!==transcript)$('transcriptText').value=transcript;
    $('transcriptText').hidden=!transcript;
    $('transcriptSource').textContent=transcript?`${$('sourceBadge').textContent} · 가져온 자막 전체를 원본 순서로 표시합니다.`:state.busy?'자막을 확인하고 있습니다.':state.status==='no_subtitles'?'사용 가능한 자막이 없습니다. SRT 파일을 추가해주세요.':'원본을 연결하고 자막을 확인하면 전체 텍스트가 표시됩니다.';
    $('analyze').disabled=busy||!hasCues;$('addManual').disabled=busy||!hasCues;
    $('prepare').disabled=busy;$('newJob').disabled=busy;$('history').disabled=busy;
    $('preview').disabled=busy;$('compat').disabled=busy||!state.preview_kind;
    $('exportFormat').disabled=busy;
    $('export').disabled=busy||!state.candidates.some(c=>c.selected)||Boolean(state.needs_alignment&&!state.alignment_confirmed);
    $('useUrl').hidden=!state.asset_id||!state.url;$('useUrl').disabled=busy;
    $('syncPanel').hidden=!hasCues;$('alignmentLabel').hidden=!state.needs_alignment;
    $('durationWarning').hidden=!state.duration_warning&&!state.metadata_warning;
    $('durationWarning').textContent=state.metadata_warning||'영상 길이가 다릅니다. 미리보기에서 시간 일치를 확인해주세요.';
    $('alignment').checked=state.alignment_confirmed;$('alignment').disabled=busy||!state.preview_kind;
    $('saveOffset').disabled=busy;
    if(document.activeElement!==$('offset'))$('offset').value=state.offset;
    $('candidateCount').textContent=state.candidates.length;
    $('selectionCount').textContent=`${state.candidates.filter(c=>c.selected).length}개 선택`;
    const u=state.usage||{};
    $('usage').textContent=u.calls!==undefined?`이번 분석: AI ${u.calls}회 · 저장 결과 ${u.cache_hits||0}구간 재사용`+(u.tokens_available&&u.calls?` · 입력 ${u.input_tokens} / 출력 ${u.output_tokens} 토큰`:u.calls?` · 입력 ${u.input_chars}자`:''):'';
    if(state.source_missing)$('status').textContent+=' · 원본 파일을 다시 연결해주세요. 분석 결과는 유지됩니다.';
    const sig=JSON.stringify([state.candidates,busy]);
    if(sig!==candidateSignature){candidateSignature=sig;renderCandidates(busy);}
    const ex=JSON.stringify([state.exports,busy]);
    if(ex!==exportSignature){exportSignature=ex;renderResults();}
    if(!state.preview_kind&&mediaUrl){mediaUrl='';activeSegment=null;$('player').pause();$('player').removeAttribute('src');$('player').load();$('player').hidden=true;$('emptyVideo').hidden=false;$('segment').textContent='';}
    if(state.preview_kind&&!busy&&['ready','subtitles_ready'].includes(state.status)){
      const url='/api/hooks'+jobPath('/media')+'?kind='+state.preview_kind;
      if(mediaUrl!==url){mediaUrl=url;$('player').src=url;$('player').hidden=false;$('emptyVideo').hidden=true;}
      else if(pendingSegment){playSegment(pendingSegment);pendingSegment=null;}
    }
    clearTimeout(timer);if(state.busy)timer=setTimeout(()=>refresh().catch(showError),1000);
  }
  async function refresh(){apply(await api(jobPath()));}
  async function mutate(suffix, method, data){apply(await api(jobPath(suffix),method,data));}
  function renderCandidates(busy){
    const target=$('candidates');target.replaceChildren();
    if(!state.candidates.length){target.append(el('div',state.cue_count?'후보를 분석하거나 발언을 직접 추가해주세요.':'자막을 확인하면 후보를 찾을 수 있습니다.','empty'));return;}
    for(const c of state.candidates){
      const card=el('div',undefined,'candidate'),head=el('div',undefined,'candidate-head');
      const checkbox=el('input');checkbox.type='checkbox';checkbox.checked=c.selected;checkbox.disabled=busy;checkbox.setAttribute('aria-label',c.text+' 선택');
      checkbox.onchange=action(()=>mutate('/candidates/'+c.id,'PATCH',{selected:checkbox.checked}));
      head.append(checkbox,el('div',c.text,'candidate-text'));card.append(head);
      const duration=c.end-c.start;
      card.append(el('div',`${kinds[c.kind]} · ${time(c.start)} – ${time(c.end)} · ${duration.toFixed(1)}초${duration<3||duration>5?' · 권장 3–5초 밖':''}${c.origin==='auto'?` · 자막 ${time(c.subtitle_start)}–${time(c.subtitle_end)}`:''}`,'candidate-meta'));
      const controls=el('div',undefined,'candidate-controls');
      const inputs={};
      for(const [key,label] of [['start','시작 (초)'],['end','종료 (초)']]){
        const wrap=el('label',label),input=el('input');input.type='number';input.step='.1';input.value=c[key].toFixed(3);input.disabled=busy;input.setAttribute('aria-label',label+' '+c.text);wrap.append(input);controls.append(wrap);inputs[key]=input;
      }
      const save=el('button','구간 저장','secondary');save.disabled=busy;save.onclick=action(async()=>{await mutate('/candidates/'+c.id,'PATCH',{start:Number(inputs.start.value),end:Number(inputs.end.value)});activeSegment=null;});
      const preview=el('button','구간 재생','secondary');preview.dataset.segmentId=c.id;preview.disabled=busy&&pendingSegment?.id!==c.id;preview.onclick=action(async()=>{if(pendingSegment?.id===c.id||(activeSegment?.id===c.id&&!$('player').paused)){pendingSegment=null;activeSegment=null;$('player').pause();syncPlaybackButtons();return;}scrollToPreview();pendingSegment=c;syncPlaybackButtons();if(state.preview_kind){pendingSegment=null;playSegment(c);}else {try{await mutate('/preview','POST',{});}catch(e){pendingSegment=null;syncPlaybackButtons();throw e;}}});
      const remove=el('button','삭제','ghost');remove.disabled=busy;remove.onclick=action(()=>mutate('/candidates/'+c.id,'DELETE'));
      controls.append(save,preview,remove);card.append(controls);target.append(card);
    }
    syncPlaybackButtons();
  }
  function scrollToPreview(){
    const target=$('player').hidden?$('emptyVideo'):$('player');
    target.scrollIntoView({behavior:window.matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth',block:'center'});
  }
  function syncPlaybackButtons(){
    for(const button of $('candidates').querySelectorAll('[data-segment-id]')){
      const playing=pendingSegment?.id===button.dataset.segmentId||(activeSegment?.id===button.dataset.segmentId&&!$('player').paused);
      button.textContent=playing?'구간 재생 멈춤':'구간 재생';button.setAttribute('aria-pressed',String(playing));
    }
  }
  function playSegment(c){activeSegment={id:c.id,start:c.start,end:c.end};$('segment').textContent=`${time(c.start)} – ${time(c.end)}`;$('player').currentTime=Math.max(0,c.start);$('player').play().catch(e=>{if(activeSegment?.id===c.id)activeSegment=null;syncPlaybackButtons();showError(e);});syncPlaybackButtons();}
  for(const event of ['play','pause','ended','emptied'])$('player').addEventListener(event,syncPlaybackButtons);
  $('player').addEventListener('loadedmetadata',()=>{if(pendingSegment){playSegment(pendingSegment);pendingSegment=null;}});
  $('player').addEventListener('timeupdate',()=>{if(activeSegment&&$('player').currentTime>=activeSegment.end){if($('loop').checked){$('player').currentTime=Math.max(0,activeSegment.start);}else{$('player').pause();activeSegment=null;}}});
  $('player').addEventListener('error',()=>{if(mediaUrl)showError(new Error('원본을 재생하지 못했습니다. “재생이 안 되나요?”를 눌러 호환 미리보기를 만들어주세요.'));});
  function renderResults(){
    const target=$('results');target.replaceChildren();if(!state.exports.length){target.textContent='추출한 영상·오디오와 문구·시간 정보를 여기서 다운로드합니다.';return;}
    for(const batch of [...state.exports].reverse()){
      const box=el('div',undefined,'result-batch');box.append(el('strong',new Date(batch.created*1000).toLocaleString('ko-KR')+' · '+(batch.format==='audio'?'오디오 (MP3)':'영상 (MP4)')));
      const href=name=>'/api/hooks'+jobPath('/exports/'+batch.id+'/'+encodeURIComponent(name));
      const complete=batch.complete===true;
      const failedCount=batch.clips.filter(c=>c.status==='error').length;
      if(!complete){
        const processing=state.busy&&state.status==='exporting'&&batch.id===state.exports[state.exports.length-1].id;
        const progress=el('div',undefined,'batch-progress');progress.setAttribute('role','status');progress.setAttribute('aria-live','polite');
        if(processing){const spinner=el('span',undefined,'hook-spinner');spinner.setAttribute('aria-hidden','true');progress.append(spinner);}
        progress.append(el('span',processing?`파일 준비 중 · ${batch.clips.length} / ${batch.total||'?'}개 처리 완료`:'준비가 중단되었습니다. 클립을 다시 선택해 추출해주세요.'));
        box.append(progress);
        if(processing&&batch.total){const bar=el('progress');bar.max=batch.total;bar.value=batch.clips.length;bar.setAttribute('aria-label','클립 준비 진행률');box.append(bar);}
        const waiting=el('button','전체 ZIP · 준비 중','secondary');waiting.disabled=true;box.append(waiting);
      }
      if(complete&&batch.clips.length){const links=el('div',undefined,'result-links');for(const [name,label] of [['clips.zip',failedCount?'성공한 클립 ZIP':'전체 ZIP'],['clips.txt','문구 TXT'],['clips.json','상세 JSON']]){const a=el('a',label);a.href=href(name);links.append(a);}box.append(links);}

      for(const c of batch.clips){const row=el('div',undefined,'result-clip');if(c.status==='finished'){const a=el('a',c.text+(batch.format==='audio'?' · MP3 ↓':' · MP4 ↓'));a.href=href(c.filename);row.append(a);}else{row.append(el('span',c.text+' · '+c.error,'result-error'));}box.append(row);}
      const failed=batch.clips.filter(c=>c.status==='error');
      if(failed.length){const retry=el('button','실패한 '+failed.length+'개 재시도','secondary');retry.disabled=state.busy;retry.onclick=action(()=>mutate('/export','POST',{candidate_ids:failed.map(c=>c.id),format:batch.format||'mp4'}));box.append(retry);}
      target.append(box);
    }
  }
  async function upload(file){
    if(file.size>config.max_video_bytes)throw new Error('영상 파일 용량이 제한을 초과합니다.');
    const registered=await api('/uploads','POST',{name:file.name,size:file.size});uploadId=registered.id;
    controller=new AbortController();$('cancelUpload').hidden=false;$('uploadProgress').hidden=false;
    for(let start=0,index=0;start<file.size;start+=registered.chunk_size,index++){
      const blob=file.slice(start,start+registered.chunk_size),buffer=await blob.arrayBuffer();
      const hash=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',buffer)),b=>b.toString(16).padStart(2,'0')).join('');
      for(let attempt=0;;attempt++){
        try{const r=await fetch(`/api/hooks/uploads/${uploadId}/chunks/${index}`,{method:'PUT',headers:{'Content-Type':'application/octet-stream','X-Chunk-SHA256':hash},body:buffer,signal:controller.signal});if(!r.ok)throw new Error((await r.json()).error);break;}
        catch(e){if(attempt||controller.signal.aborted)throw e;}
      }
      const percent=Math.round(Math.min(file.size,start+registered.chunk_size)/file.size*100);$('uploadProgress').value=percent;$('uploadStatus').textContent=`영상 업로드 ${percent}%`;
    }
    $('uploadStatus').textContent='영상 확인 중…';
    const result=await api('/uploads/'+uploadId+'/complete','POST',{});uploadId=null;return result.asset_id;
  }
  $('cancelUpload').onclick=()=>{controller?.abort();};
  $('prepare').onclick=action(async()=>{
    uploading=true;$('prepare').disabled=true;$('newJob').disabled=true;clearTimeout(timer);
    try{
      const file=$('videoFile').files[0],srt=$('srtFile').files[0],url=$('sourceUrl').value.trim();
      if(!file&&!url&&!state)throw new Error('원본 영상 또는 YouTube 링크를 넣어주세요.');
      if(srt&&srt.size>config.max_srt_bytes)throw new Error('SRT 파일 용량이 제한을 초과합니다.');
      const asset=file?await upload(file):null;
      if(!state)apply(await api('/jobs','POST',{url,asset_id:asset}));
      else {const data={url};if(asset)data.asset_id=asset;apply(await api(jobPath('/source'),'POST',data));}
      if(srt){const r=await fetch('/api/hooks'+jobPath('/subtitles'),{method:'POST',headers:{'Content-Type':'application/octet-stream'},body:srt});const result=await r.json();if(!r.ok)throw new Error(result.error);apply(result);}
      $('videoFile').value='';$('srtFile').value='';mediaUrl='';$('player').removeAttribute('src');$('player').hidden=true;$('emptyVideo').hidden=false;
      await mutate('/prepare','POST',{});await loadHistory();
    }finally{
      if(uploadId){await api('/uploads/'+uploadId,'DELETE').catch(()=>{});uploadId=null;}
      uploading=false;$('cancelUpload').hidden=true;$('uploadProgress').hidden=true;$('uploadStatus').textContent='';$('prepare').disabled=false;$('newJob').disabled=false;if(state)apply(state);
    }
  });
  $('analyze').onclick=action(()=>mutate('/analyze','POST',{}));
  $('preview').onclick=action(()=>{activeSegment=null;return mutate('/preview','POST',{});});
  $('compat').onclick=action(()=>{mediaUrl='';return mutate('/preview','POST',{force:true});});
  $('saveOffset').onclick=action(async()=>{activeSegment=null;await mutate('','PATCH',{offset:Number($('offset').value)});});
  $('alignment').onchange=action(()=>mutate('','PATCH',{alignment_confirmed:$('alignment').checked}));
  $('markStart').onclick=()=>{$('manualStart').value=$('player').currentTime.toFixed(1);};
  $('markEnd').onclick=()=>{$('manualEnd').value=$('player').currentTime.toFixed(1);};
  $('addManual').onclick=action(async()=>{await mutate('/candidates','POST',{text:$('manualText').value,start:Number($('manualStart').value),end:Number($('manualEnd').value)});$('manualText').value='';});
  $('export').onclick=action(()=>mutate('/export','POST',{candidate_ids:state.candidates.filter(c=>c.selected).map(c=>c.id),format:$('exportFormat').value}));
  $('useUrl').onclick=action(async()=>{await mutate('/source','POST',{asset_id:null});mediaUrl='';activeSegment=null;await mutate('/preview','POST',{});});
  async function loadHistory(){const data=await api('/jobs');$('history').replaceChildren(new Option('작업 선택',''));for(const job of data.jobs)$('history').append(new Option(job.video_name||job.youtube_title||job.url,job.id));if(state)$('history').value=state.id;}
  $('history').onchange=action(async()=>{if(!$('history').value)return;activeSegment=null;pendingSegment=null;apply(await api('/jobs/'+$('history').value));$('sourceUrl').value=state.url;});
  $('newJob').onclick=()=>{localStorage.removeItem('hooks.job');location.href='/edit-helper#hooks';};
  (async()=>{config=await api('/config');await loadHistory();const id=new URLSearchParams(location.search).get('job')||localStorage.getItem('hooks.job');if(id){try{apply(await api('/jobs/'+id));$('sourceUrl').value=state.url;$('history').value=id;}catch(e){localStorage.removeItem('hooks.job');showError(e);}}})().catch(showError);
})();
