(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  let mode = 'videos', files = [], current = null, timer = null, submitting = false;
  const fmt = n => `${Math.floor(n / 60)}:${(n % 60).toFixed(2).padStart(5, '0')}`;
  function error(message = '') { $('aeError').textContent = message; $('aeError').hidden = !message; }
  async function api(path, options = {}) {
    const response = await fetch(path, options);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw Error(data.error || `요청 실패 (${response.status}). 다시 시도해주세요.`);
    return data;
  }
  const post = (path, data = {}) => api(path, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(data)});
  function addFiles(items) {
    if (submitting) return;
    error();
    for (const f of items) {
      if (!/\.(mp4|mov)$/i.test(f.name)) { error('MP4 또는 MOV 영상을 선택해주세요.'); continue; }
      if (!f.size || f.size > 2 * 1024 ** 3) { error('각 영상은 0 바이트보다 크고 2 GB 이하여야 합니다.'); continue; }
      if (files.length >= 40) { error('영상은 최대 40개까지 넣을 수 있습니다.'); break; }
      if (!files.some(v => v.file.name === f.name && v.file.size === f.size && v.file.lastModified === f.lastModified)) files.push({file:f});
    }
    renderFiles();
  }
  function renderFiles() {
    $('aeFileList').replaceChildren();
    files.forEach((item, i) => {
      const li = document.createElement('li'), name = document.createElement('span');
      name.textContent = `${String(i + 1).padStart(2, '0')}  ${item.file.name}`; name.title = item.file.name; li.append(name);
      [['↑', '앞으로 이동', -1], ['↓', '뒤로 이동', 1], ['×', '제거', 0]].forEach(([text, label, offset]) => {
        const b = document.createElement('button'); b.type = 'button'; b.className = 'ghost'; b.textContent = text; b.setAttribute('aria-label', `${item.file.name} ${label}`);
        b.disabled = !!offset && (i + offset < 0 || i + offset >= files.length);
        b.onclick = () => { if (offset) [files[i], files[i+offset]] = [files[i+offset], files[i]]; else files.splice(i,1); renderFiles(); }; li.append(b);
      });
      $('aeFileList').append(li);
    });
  }
  document.querySelectorAll('[data-mode]').forEach(b => b.onclick = () => {
    mode = b.dataset.mode;
    document.querySelectorAll('[data-mode]').forEach(v => v.setAttribute('aria-pressed', String(v === b)));
    $('aeProject').hidden = mode !== 'project';
    $('aeDropTitle').textContent = mode === 'project' ? '원본 영상 첨부 (필요한 경우)' : '영상 소스를 여기에 놓으세요';
  });
  $('aeFiles').onchange = e => { addFiles(e.target.files); e.target.value = ''; };
  $('aeDrop').ondragover = e => { e.preventDefault(); $('aeDrop').classList.add('dragging'); };
  $('aeDrop').ondragleave = () => $('aeDrop').classList.remove('dragging');
  $('aeDrop').ondrop = e => { e.preventDefault(); $('aeDrop').classList.remove('dragging'); addFiles(e.dataTransfer.files); };
  $('aeJson').onchange = () => { $('aeJsonName').textContent = $('aeJson').files[0]?.name || ''; if ($('aeJson').files.length) $('aeProjectList').value = ''; };
  $('aeProjectList').onchange = async () => {
    $('aeFolderBox').hidden = true; $('aeFolder').replaceChildren(new Option('현재 타임라인', ''));
    if (!$('aeProjectList').value) return;
    $('aeJson').value = ''; $('aeJsonName').textContent = '';
    try {
      const data = await api(`/api/auto-edit/projects/${$('aeProjectList').value}/folders`);
      data.folders.forEach(f => $('aeFolder').add(new Option(`${f.name} · 영상 ${f.count}개`, f.id)));
      $('aeFolderBox').hidden = !data.folders.length;
    } catch(e) { error(e.message); }
  };
  $('aeRule').onchange = () => { $('aeRuleHint').textContent = $('aeRule').value === 'both' ? '두 감지를 모두 켜면 조용하면서 화면도 멈춘 구간만 제거합니다.' : '정지 구간의 대사도 잘릴 수 있어요. 대사를 더 보존하려면 ‘무음이면서 정지’를 선택하세요.'; };
  async function loadProjects() {
    const selected = $('aeProjectList').value;
    const data = await api('/api/auto-edit/projects');
    $('aeProjectList').replaceChildren(new Option(data.projects.length ? '프로젝트 선택' : '발견한 프로젝트가 없습니다 · JSON으로 첨부하세요', ''));
    data.projects.forEach(p => $('aeProjectList').add(new Option(p.name, p.id)));
    $('aeProjectList').value = selected;
  }
  $('aeRefresh').onclick = () => loadProjects().catch(e => error(e.message));
  async function upload(item, index) {
    if (item.assetId) return item.assetId;
    const info = await post('/api/hooks/uploads', {name:item.file.name, size:item.file.size});
    try {
      for (let start = 0, chunk = 0; start < item.file.size; start += info.chunk_size, chunk++) {
        const blob = await item.file.slice(start, start + info.chunk_size).arrayBuffer();
        const hash = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', blob)), b => b.toString(16).padStart(2,'0')).join('');
        await api(`/api/hooks/uploads/${info.id}/chunks/${chunk}`, {method:'PUT', headers:{'X-Chunk-SHA256':hash}, body:blob});
        const percent = Math.round(100 * (index + Math.min(start + info.chunk_size, item.file.size) / item.file.size) / files.length);
        $('aeMessage').textContent = `영상 업로드 중 · ${index + 1}/${files.length} · ${item.file.name}`;
        $('aeProgressBar').value = percent; $('aePercent').textContent = `${percent}%`;
      }
      item.assetId = (await post(`/api/hooks/uploads/${info.id}/complete`)).asset_id;
      return item.assetId;
    } catch(e) { await api(`/api/hooks/uploads/${info.id}`, {method:'DELETE'}).catch(() => {}); throw e; }
  }
  $('aeForm').onsubmit = async e => {
    e.preventDefault(); if (submitting) return; error();
    let draft = null;
    try {
      if (mode === 'project' && $('aeJson').files.length) {
        const file = $('aeJson').files[0];
        if (file.size > 8 * 1024 ** 2) throw Error('프로젝트 JSON은 8 MB 이내여야 합니다.');
        try { draft = JSON.parse(await file.text()); } catch { throw Error('읽을 수 없는 프로젝트입니다. 암호화되지 않은 draft_content.json을 선택해주세요.'); }
      }
      if (mode === 'project' && !draft && !$('aeProjectList').value) throw Error('캡컷 프로젝트를 선택하거나 JSON을 첨부해주세요.');
      if (mode === 'videos' && !files.length) throw Error('편집할 영상을 넣어주세요.');
      if (!$('aeSilence').checked && !$('aeFreeze').checked) throw Error('무음 또는 정지 구간을 하나 이상 선택해주세요.');
      submitting = true; current = null; $('aeFields').disabled = true; clearTimeout(timer);
      $('aeEmpty').hidden = true; $('aeResult').hidden = true; $('aeProgress').hidden = false; $('aeCancel').hidden = true;
      $('aeMessage').textContent = '소스를 준비하고 있습니다.'; $('aeProgressBar').value = 0; $('aePercent').textContent = '';
      const ids = []; for (let i = 0; i < files.length; i++) ids.push(await upload(files[i], i));
      const opts = {silence:$('aeSilence').checked, freeze:$('aeFreeze').checked, mode:$('aeRule').value,
        noise_db:Number($('aeNoise').value), silence_seconds:Number($('aeSilenceTime').value), freeze_seconds:Number($('aeFreezeTime').value), freeze_db:Number($('aeFreezeDb').value), padding:Number($('aePadding').value)};
      const job = await post('/api/auto-edit/jobs', {name:$('aeName').value, asset_ids:ids, options:opts,
        ...(mode === 'project' ? {project_id:$('aeProjectList').value || null, folder_id:$('aeFolder').value || null, draft} : {})});
      current = job.id; localStorage.setItem('auto-edit-job',current); render(job); poll(current); loadHistory().catch(() => {});
    } catch(e) { error(e.message); $('aeProgress').hidden = true; $('aeEmpty').hidden = false; }
    finally { submitting = false; $('aeFields').disabled = false; }
  };
  function render(job) {
    $('aeEmpty').hidden = true; $('aeProgress').hidden = !job.busy; $('aeResult').hidden = job.status !== 'finished';
    $('aeMessage').textContent = job.message; $('aeProgressBar').value = job.progress; $('aePercent').textContent = `${job.progress}%`;
    $('aeCancel').hidden = !job.busy; $('aeCancel').disabled = false;
    if (['failed','interrupted','cancelled'].includes(job.status)) { error(job.message); $('aeEmpty').hidden = false; }
    if (job.status !== 'finished') return;
    error(); const r = job.result;
    $('aeResultName').textContent = job.name; $('aeBefore').textContent = fmt(r.original_duration); $('aeAfter').textContent = fmt(r.edited_duration);
    $('aeSaved').textContent = r.cuts.length ? `${fmt(r.removed_duration)} 단축 · ${r.cuts.length}곳 제거 · ${r.segments}개 영상 클립` : job.message;
    $('aeTimeline').replaceChildren();
    r.cuts.forEach(c => { const el = document.createElement('i'); el.style.left = `${100*c.start/r.original_duration}%`; el.style.width = `${100*(c.end-c.start)/r.original_duration}%`; el.title = `${fmt(c.start)}–${fmt(c.end)} ${c.reason}`; $('aeTimeline').append(el); });
    $('aeWarnings').textContent = (job.warnings || []).join(' '); $('aeOpenStatus').textContent = '';
    $('aeDownload').href = `/api/auto-edit/jobs/${job.id}/draft`;
    $('aeCutsTitle').textContent = `제거 구간 ${r.cuts.length}곳 보기`; $('aeCuts').replaceChildren();
    r.cuts.forEach(c => { const li = document.createElement('li'); const time = document.createElement('span'), reason = document.createElement('span'); time.textContent = `${fmt(c.start)} → ${fmt(c.end)}`; reason.textContent = c.reason; li.append(time, reason); $('aeCuts').append(li); });
  }
  async function poll(id) {
    clearTimeout(timer);
    try { const job = await api(`/api/auto-edit/jobs/${id}`); if (id !== current) return; render(job); if (job.busy) timer = setTimeout(() => poll(id),1200); else loadHistory().catch(() => {}); }
    catch(e) { if (id === current) { error(e.message + ' 최근 편집의 새로고침으로 다시 확인할 수 있습니다.'); } }
  }
  $('aeCancel').onclick = async () => { $('aeCancel').disabled = true; try { await post(`/api/auto-edit/jobs/${current}/cancel`); } catch(e) { error(e.message); $('aeCancel').disabled = false; } };
  $('aeOpen').onclick = async () => { $('aeOpen').disabled = true; $('aeOpenStatus').textContent = '캡컷을 여는 중…'; try { const r = await post(`/api/auto-edit/jobs/${current}/open`); $('aeOpenStatus').textContent = r.message; } catch(e) { $('aeOpenStatus').textContent = e.message; } finally { $('aeOpen').disabled = false; } };
  async function loadHistory() {
    const data = await api('/api/auto-edit/jobs'); $('aeHistory').replaceChildren();
    if (!data.jobs.length) { const p = document.createElement('p'); p.className = 'hint'; p.textContent = '완료한 편집본은 여기에 보관됩니다.'; $('aeHistory').append(p); }
    const labels = {finished:'완료', failed:'실패', interrupted:'중단됨', cancelled:'취소됨', queued:'대기 중', analyzing:'분석 중', exporting:'저장 중'};
    data.jobs.forEach(j => { const b = document.createElement('button'); b.type='button'; b.className='job'; const n=document.createElement('span'), s=document.createElement('small'); n.textContent=j.name; s.textContent=labels[j.status] || j.status; b.append(n,s); b.onclick=()=>{ if (submitting) return; error(); current=j.id; localStorage.setItem('auto-edit-job',current); poll(current); }; $('aeHistory').append(b); });
  }
  $('aeHistoryRefresh').onclick = () => { loadHistory().catch(e => error(e.message)); if (current) poll(current); };
  loadProjects().catch(e => error(e.message)); loadHistory().catch(e => error(e.message));
  current = localStorage.getItem('auto-edit-job'); if (current) poll(current);
})();
