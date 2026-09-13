'use strict';
window.RecipeEditor = (() => {
  const $ = id => document.getElementById(id);
  const MODELS = JSON.parse(document.getElementById('helperModels').textContent);
  const processMode = document.body.dataset.uploadProcess === 'true';
  let saved = null, defaults = null, generation = 0, completed = false, restoring = false, notes = [];
  function toast(text) { const el = $('toast'); el.textContent = text; el.className = 'show'; setTimeout(() => el.className = '', 2500); }
  function showMsg(el, text, kind) { el.textContent = text; el.className = 'msg ' + kind; el.classList.toggle('hidden', !text); }
  async function api(method, url, body) {
    const r = await fetch(url, {method, headers: {'Content-Type':'application/json'}, ...(body === undefined ? {} : {body:JSON.stringify(body)})});
    const data = await r.json(); if (!r.ok) throw new Error(data.error || '요청에 실패했습니다.'); return data;
  }
  function notify() { if (!restoring) document.dispatchEvent(new CustomEvent('recipe:changed')); }
  // ---- 템플릿 설정 ------------------------------------------------------------
  function currentRecipeSettings() {
    return { template: $('tplText').value, instructions: $('insText').value, model: $('modelSel').value, backend: $('backendSel').value };
  }
  function fillRecipeSettings(s) {
    $('tplText').value = s.template || ''; $('insText').value = s.instructions || '';
    $('modelSel').value = s.model || 'gpt-5.6-sol'; $('backendSel').value = s.backend || 'codex';
    filterModels(); updateTplState();
  }
  function isDirty() {
    if (!saved) return false;
    const c = currentRecipeSettings();
    return ['template', 'instructions', 'model', 'backend'].some(k => (c[k] || '') !== (saved[k] || ''));
  }
  function updateTplState() {
    const st = $('tplState');
    const dirty = isDirty();
    st.textContent = dirty ? '· 저장되지 않은 변경 있음 (변형에는 현재 내용이 쓰입니다)' : ('· ' + (MODELS[$('modelSel').value]?.label || ''));
    st.classList.toggle('dirty', dirty);
  }
  ['tplText', 'insText', 'modelSel', 'backendSel'].forEach(id => {
    $(id).addEventListener('input', updateTplState); $(id).addEventListener('change', updateTplState);
  });

  function filterModels() {
    const codex = $('backendSel').value === 'codex';
    for (const option of $('modelSel').options) {
      option.hidden = option.disabled = !!MODELS[option.value].codex !== codex;
    }
    if ($('modelSel').selectedOptions[0]?.disabled) $('modelSel').value = codex ? 'gpt-5.6-sol' : 'sonnet';
  }
  $('backendSel').addEventListener('change', () => { filterModels(); updateTplState(); });
  function renderEnv(env) {
    const parts = [env.codex_available ? "Codex CLI 사용 가능" : "Codex CLI 없음"];
    parts.push(env.cli_available ? 'CLI 사용 가능' : '<span class="bad">CLI(claude) 없음</span>');
    parts.push(env.api_key_set ? 'API 키 있음' : '<span class="bad">API 키 없음</span>');
    $('envInfo').innerHTML = parts.join(' · ');
  }

  async function loadSettings() {
    try {
      const d = await api('GET', '/api/helper/settings');
      saved = d.recipe; defaults = d.recipe_defaults;
      fillRecipeSettings(saved);
      renderEnv(d.env || {});
    } catch (e) { showMsg($('tplMsg'), '설정을 불러오지 못했습니다: ' + e.message, 'err'); }
  }

  $('tplSaveBtn').addEventListener('click', async () => {
    const btn = $('tplSaveBtn'); btn.disabled = true;
    try {
      const d = await api('PUT', '/api/helper/settings', { recipe: currentRecipeSettings() });
      saved = d.recipe; updateTplState();
      showMsg($('tplMsg'), '템플릿을 저장했습니다. 다음 실행 때도 유지됩니다.', 'ok');
      setTimeout(() => showMsg($('tplMsg'), '', ''), 2500);
    } catch (e) { showMsg($('tplMsg'), e.message, 'err'); }
    finally { btn.disabled = false; }
  });
  $('tplResetBtn').addEventListener('click', () => {
    if (!defaults) return;
    if (!confirm('템플릿과 추가 지시를 기본값으로 되돌릴까요? (저장을 눌러야 적용됩니다)')) return;
    fillRecipeSettings({ ...defaults, model: $('modelSel').value, backend: $('backendSel').value });
    invalidate(); notify();
  });

  // ---- 입력 / 결과 ------------------------------------------------------------
  let activePlatform = 'instagram';
  const recipePlatforms = { instagram: '인스타그램', youtube: '유튜브', tiktok: '틱톡' };
  let pendingQuestions = [];
  let collectedInfo = [];
  function activeOutput() { return $(activePlatform + 'Out'); }
  function selectPlatform(platform) {
    activePlatform = platform;
    document.querySelectorAll('.platform-tab').forEach(btn => {
      const active = btn.dataset.platform === platform;
      btn.classList.toggle('active', active); btn.setAttribute('aria-selected', String(active));
    });
    document.querySelectorAll('.platform-output').forEach(el => el.classList.toggle('hidden', el.dataset.platform !== platform));
    $('resultLabel').setAttribute('for', activeOutput().id);
    updateCounts();
  }
  document.querySelectorAll('.platform-tab').forEach(btn => btn.addEventListener('click', () => selectPlatform(btn.dataset.platform)));

  function updateCounts() {
    const n = $('srcText').value.length;
    $('srcCount').textContent = n.toLocaleString() + '자';
    const m = Array.from(activeOutput().value).length;
    $('outCount').textContent = m.toLocaleString() + '자 · 권장 300–500자';
    $('outCount').classList.toggle('over', m > 500 || (m > 0 && m < 300));
  }
  function clearClarification() {
    pendingQuestions = []; collectedInfo = []; $('questionList').replaceChildren(); $('clarifyBox').classList.add('hidden');
    showMsg($('answerErr'), '', 'err');
  }
  $('srcText').addEventListener('input', () => {
    updateCounts(); clearClarification();
    try { !processMode && localStorage.setItem('helper.recipe.src', $('srcText').value); } catch {}
  });
  Object.keys(recipePlatforms).forEach(key => $(key + 'Out').addEventListener('input', updateCounts));
  try { if (!processMode) $('srcText').value = localStorage.getItem('helper.recipe.src') || ''; } catch {}
  updateCounts();

  $('pasteBtn').addEventListener('click', async () => {
    try {
      const t = await navigator.clipboard.readText();
      if (!t) { toast('클립보드가 비어 있습니다', true); return; }
      $('srcText').value = t; $('srcText').dispatchEvent(new Event('input', {bubbles:true}));
    } catch { toast('클립보드를 읽을 수 없습니다. 직접 붙여 넣어 주세요 (⌘V)', true); $('srcText').focus(); }
  });
  $('clearSrcBtn').addEventListener('click', () => { $('srcText').value = ''; $('srcText').dispatchEvent(new Event('input', {bubbles:true})); $('srcText').focus(); });
  $('clearOutBtn').addEventListener('click', () => { invalidate(); setResult({}, [], null); notify(); });

  function setResult(results, resultNotes, usage) {
    notes = resultNotes || [];
    Object.keys(recipePlatforms).forEach(key => { $(key + 'Out').value = results?.[key] || ''; });
    $('notesList').innerHTML = (notes || []).map(n => '<li>' + n.replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c])) + '</li>').join('');
    $('notesBox').classList.toggle('hidden', !(notes && notes.length));
    let u = '';
    if (usage) {
      const bits = [];
      if (usage.model) bits.push(usage.model);
      if (usage.duration_ms) bits.push((usage.duration_ms / 1000).toFixed(1) + '초');
      if (usage.cost_usd != null) bits.push('$' + Number(usage.cost_usd).toFixed(3) + ' 상당');
      if (usage.input_tokens != null) bits.push(usage.input_tokens + '→' + usage.output_tokens + ' 토큰');
      u = bits.join(' · ');
    }
    $('usageInfo').textContent = u;
    updateCounts();
  }

  function renderQuestions(questions) {
    pendingQuestions = questions || [];
    const list = $('questionList'); list.replaceChildren();
    pendingQuestions.forEach((question, index) => {
      const wrap = document.createElement('div'); wrap.className = 'question';
      const label = document.createElement('label'); label.htmlFor = 'answer-' + index; label.textContent = (index + 1) + '. ' + question;
      const input = document.createElement('textarea'); input.id = 'answer-' + index; input.rows = 2; input.placeholder = '여기에 답변해 주세요';
      wrap.append(label, input); list.append(wrap);
    });
    $('clarifyBox').classList.remove('hidden');
    $('clarifyBox').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    list.querySelector('textarea')?.focus();
  }

  let generating = false;
  async function generate(additionalInfo = []) {
    if (generating) return;
    const token = ++generation;
    const src = $('srcText').value.trim();
    if (!src) { toast('원본 레시피 설명을 붙여 넣어 주세요', true); $('srcText').focus(); return; }
    generating = true; completed = false; notify();
    const btn = additionalInfo.length ? $('answerBtn') : $('genBtn'); const label = btn.innerHTML;
    $('genBtn').disabled = true; $('answerBtn').disabled = true;
    btn.innerHTML = '<span class="spinner"></span>생성 중… (수십 초 걸릴 수 있어요)';
    showMsg($('genErr'), '', 'err');
    showMsg($('answerErr'), '', 'err');
    const started = Date.now();
    try {
      const d = await api('POST', '/api/helper/recipe-description', {
        source_text: src, additional_info: additionalInfo, ...currentRecipeSettings()
      });
      if (token !== generation) return;
      if (d.status === 'needs_input') {
        collectedInfo = additionalInfo; setResult({}, d.notes, d.usage); renderQuestions(d.questions);
        toast('추가 정보가 필요합니다');
      } else {
        clearClarification(); setResult(d.results, d.notes, d.usage); selectPlatform(processMode ? 'youtube' : 'instagram');
        completed = true;
        toast('세 플랫폼 생성 완료 (' + ((Date.now() - started) / 1000).toFixed(0) + '초)');
      }
    } catch (e) {
      if (token !== generation) return;
      showMsg(additionalInfo.length ? $('answerErr') : $('genErr'), e.message, 'err');
    } finally {
      if (token !== generation) return;
      generating = false;
      notify(); $('genBtn').disabled = false; $('answerBtn').disabled = false; btn.innerHTML = label;
    }
  }
  $('genBtn').addEventListener('click', () => { clearClarification(); generate(); });
  $('answerBtn').addEventListener('click', () => {
    const answers = pendingQuestions.map((question, index) => ({ question, answer: $('answer-' + index)?.value.trim() || '' }));
    const missing = answers.findIndex(item => !item.answer);
    if (missing >= 0) { showMsg($('answerErr'), '모든 질문에 답변해 주세요.', 'err'); $('answer-' + missing).focus(); return; }
    generate([...collectedInfo, ...answers]);
  });

  $('copyBtn').addEventListener('click', async () => {
    const text = activeOutput().value;
    if (!text) { toast('복사할 내용이 없습니다', true); return; }
    try { await navigator.clipboard.writeText(text); }
    catch { activeOutput().focus(); activeOutput().select(); document.execCommand('copy'); }
    toast(recipePlatforms[activePlatform] + ' 게시글을 복사했습니다');
  });

  const ready = loadSettings();

  const root = document.querySelector('[data-tool="recipe"].panel');
  function invalidate() {
    generation++; completed = false; generating = false;
    $('genBtn').disabled = $('answerBtn').disabled = false;
    $('genBtn').textContent = '플랫폼별 게시글 생성 →'; $('answerBtn').textContent = '답변하고 게시글 생성 →';
  }
  root.addEventListener('input', e => {
    if (['srcText','tplText','insText','modelSel','backendSel'].includes(e.target.id)) { invalidate(); clearClarification(); }
    notify();
  });
  function snapshot() {
    return {source:$('srcText').value, settings:currentRecipeSettings(), results:Object.fromEntries(Object.keys(recipePlatforms).map(k => [k,$(k+'Out').value])), pendingQuestions, collectedInfo, notes,
      answers:pendingQuestions.map((_,i) => $('answer-'+i)?.value || ''), completed};
  }
  async function restore(state = {}) {
    await ready; restoring = true; generation++; generating = false;
    $('genBtn').disabled = $('answerBtn').disabled = false;
    $('genBtn').textContent = '플랫폼별 게시글 생성 →'; $('answerBtn').textContent = '답변하고 게시글 생성 →';
    $('srcText').value = state.source || ''; fillRecipeSettings(state.settings || saved || defaults || {});
    clearClarification(); setResult(state.results || {}, state.notes || [], null);
    collectedInfo = state.collectedInfo || [];
    if (state.pendingQuestions?.length) { renderQuestions(state.pendingQuestions); (state.answers || []).forEach((v,i) => { if ($('answer-'+i)) $('answer-'+i).value=v; }); }
    completed = !!state.completed; selectPlatform(processMode ? 'youtube' : 'instagram'); restoring = false;
  }
  return {ready, snapshot, restore, cancel:invalidate, valid:() => completed && !generating && !pendingQuestions.length && !!$('youtubeOut').value.trim()};
})();
