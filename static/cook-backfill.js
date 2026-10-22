/* One request per recipe keeps progress visible and retries independent. */
class CookBackfillQueue {
  constructor(request, changed = () => {}) {
    this.request = request;
    this.changed = changed;
    this.items = [];
    this.errors = [];
    this.skipped = 0;
    this.busy = false;
    this.stop = false;
  }
  async scan() {
    if (this.busy) return;
    this.busy = true;
    this.items = []; this.errors = []; this.skipped = 0;
    this.changed();
    try {
      const progress = this.scanProgress || {cursor:3, connection:'', skipped:0, items:[], errors:[]};
      let {cursor, connection, skipped} = progress;
      const {items, errors} = progress, seen = new Set(items.map(item => item.itemId));
      this.scanProgress = progress;
      for (let page = 0; page < 2000; page++) {
        const data = await this.request('GET', `/api/cook-dict/backfill?cursor=${cursor}&connection=${encodeURIComponent(connection)}`);
        if (connection && connection !== data.connection) {
          this.scanProgress = null;
          throw Error('시트 연결이 변경되었습니다. 대상을 다시 확인해 주세요.');
        }
        connection = data.connection;
        skipped += data.skipped || 0;
        errors.push(...(data.errors || []));
        for (const item of data.items) {
          if (seen.has(item.itemId)) {
            this.scanProgress = null;
            throw Error('목록이 변경되었습니다. 대상을 다시 확인해 주세요.');
          }
          seen.add(item.itemId); items.push({...item, status:'pending', error:''});
        }
        if (data.nextCursor === null) {
          this.items = items; this.errors = errors; this.skipped = skipped; this.connection = connection;
          this.scanProgress = null;
          return;
        }
        if (!Number.isInteger(data.nextCursor) || data.nextCursor <= cursor) {
          this.scanProgress = null;
          throw Error('현황판 목록 응답을 확인해 주세요.');
        }
        cursor = data.nextCursor;
        Object.assign(progress, {cursor, connection, skipped});
        this.changed();
      }
      throw Error('현황판이 너무 큽니다. 완료 항목을 별도 시트로 정리해 주세요.');
    } catch (error) {
      if (error.code === 'connection_changed') this.scanProgress = null;
      throw error;
    } finally { this.busy = false; this.changed(); }
  }
  async run(retry = false) {
    if (this.busy) return;
    this.busy = true; this.stop = false;
    const targets = this.items.filter(item => item.status === (retry ? 'failed' : 'pending'));
    this.runTotal = targets.length; this.runDone = 0; this.retrying = retry;
    try {
      for (const item of targets) {
        if (this.stop) break;
        item.status = 'working'; item.error = ''; this.changed();
        try {
          const result = await this.request('POST', `/api/cook-dict/backfill/${encodeURIComponent(item.itemId)}`,
            {connection:this.connection, revision:item.revision});
          if (!result.saved && !result.skipped) throw Error('저장 결과를 확인하지 못했습니다. 재시도해 주세요.');
          item.status = result.skipped ? 'skipped' : 'saved';
        } catch (error) {
          item.status = 'failed'; item.error = error.message;
          if (error.code === 'connection_changed') this.stop = true;
        }
        this.runDone++;
        this.changed();
      }
    } finally { this.busy = false; this.changed(); }
  }
}
if (typeof module !== 'undefined') module.exports = {CookBackfillQueue};
if (typeof document !== 'undefined') (() => {
  const el = id => document.getElementById(id);
  const names = {pending:'대기', working:'추출·저장 중…', saved:'준비 완료 · 사진 추가 필요', skipped:'기존 정보가 있어 건너뜀', failed:'실패'};
  async function request(method, url, body) {
    const response = await fetch(url, {method, headers:{'Content-Type':'application/json'},
      ...(body ? {body:JSON.stringify(body)} : {})});
    const data = await response.json().catch(() => ({}));
    if (!response.ok || !data.ok) {
      const error = Error(data.error || `요청 실패 (${response.status})`); error.code = data.code; throw error;
    }
    return data;
  }
  const queue = new CookBackfillQueue(request, render);
  let scanning = false;
  let hasScanned = false;
  function render() {
    const counts = Object.fromEntries(Object.keys(names).map(key => [key, queue.items.filter(item => item.status === key).length]));
    el('cookBackfillScan').disabled = queue.busy;
    el('cookBackfillStart').disabled = queue.busy || !counts.pending;
    el('cookBackfillRetry').disabled = queue.busy || !counts.failed;
    el('cookBackfillStop').disabled = !queue.busy || scanning || queue.stop;
    el('cookBackfillScan').textContent = scanning ? '대상 확인 중…' : hasScanned ? '대상 다시 확인' : '기존 완료 항목 레시피 준비';
    el('cookBackfillStart').textContent = queue.busy && !scanning && !queue.retrying ? '처리 중…' : '처리 시작';
    el('cookBackfillRetry').textContent = queue.busy && !scanning && queue.retrying ? '재시도 중…' : '실패한 항목 재시도';
    el('cookBackfillStop').textContent = queue.busy && queue.stop && !scanning ? '현재 항목 완료 후 중지합니다…' : '현재 항목 후 중지';
    el('cookBackfillActivity').hidden = !queue.busy;
    if (queue.busy) {
      const active = queue.items.find(item => item.status === 'working');
      el('cookBackfillActivityTitle').textContent = scanning ? '현황판에서 대상을 확인하고 있습니다' : queue.stop ? '현재 항목을 마무리하고 있습니다' : `레시피 ${queue.runDone} / ${queue.runTotal}개 처리`;
      el('cookBackfillCurrent').textContent = scanning ? `현재 ${queue.scanProgress?.items.length || 0}개 발견 · 전체 목록 확인 중` : active ? `${active.title} · AI 추출 및 시트 저장 중` : '다음 항목을 준비하고 있습니다';
      const progress = el('cookBackfillProgress');
      if (scanning) progress.removeAttribute('value');
      else { progress.max = Math.max(queue.runTotal, 1); progress.value = queue.runDone; }
      progress.setAttribute('aria-label', scanning ? '현황판 대상 조회 중' : '레시피 처리 진행률');
      el('cookBackfillHint').textContent = scanning ? 'Google 응답에 따라 몇 분 걸릴 수 있습니다. 전체 확인 후 시작할 수 있습니다.' : '이 화면을 열어 두세요. 다른 탭이나 앱은 사용해도 됩니다.';
    }
    el('cookBackfillStatus').textContent = scanning ? `대상 항목을 확인하고 있습니다… 현재 ${queue.scanProgress?.items.length || 0}개 확인` :
      `대상 ${queue.items.length}개 · 완료 ${counts.saved} · 대기 ${counts.pending} · 처리 중 ${counts.working} · 실패 ${counts.failed} · 기존 정보로 제외 ${queue.skipped + counts.skipped} · ID 확인 필요 ${queue.errors.length}`;
    const rows = queue.items.map(item => ({title:item.title, message:`${names[item.status]}${item.error ? ' — '+item.error : ''}`}));
    rows.push(...queue.errors.map(item => ({title:item.title, message:item.error})));
    el('cookBackfillResults').replaceChildren(...rows.map(row => {
      const li = document.createElement('li'); li.textContent = `${row.title} — ${row.message}`; return li;
    }));
  }
  el('cookBackfillScan').onclick = async () => {
    scanning = true; el('cookBackfillPanel').hidden = false;
    let error;
    try { await queue.scan(); } catch (e) { error = e.message; }
    finally {
      scanning = false; hasScanned = true; render();
      if (error) el('cookBackfillStatus').textContent = `${error}${queue.scanProgress ? ' 다시 누르면 중단된 조회부터 이어서 확인합니다.' : ''}`;
    }
  };
  el('cookBackfillStart').onclick = () => queue.run();
  el('cookBackfillRetry').onclick = () => queue.run(true);
  el('cookBackfillStop').onclick = () => { queue.stop = true; render(); };
  window.addEventListener('beforeunload', event => {
    if (queue.busy) { event.preventDefault(); event.returnValue = ''; }
  });
})();
