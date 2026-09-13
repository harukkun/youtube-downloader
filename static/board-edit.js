'use strict';
// Shared pure form state and request ordering; also exercised by local Node tests.
const BoardEdit = (() => {
  function flatOf(it) {
    const flat = {status:it.status, dish:it.dish_title || '', src_url:it.source?.url || '',
      src_title:it.source?.title || '', src_channel:it.source?.channel || '',
      ref_urls:it.ref_urls ?? (it.reference_shorts || []).map(r => r.url).join('\n'),
      ref_channels:it.ref_channels ?? (it.reference_shorts || []).map(r => r.channel).join('\n'),
      title:it.video?.title || '', desc:it.video?.description || '', pinned:it.video?.pinned_comment || '', memo:it.memo || ''};
    for (const [k, v] of Object.entries(it.platforms || {})) { flat[k + '_on'] = !!v.checked; flat[k + '_url'] = v.url || ''; }
    return flat;
  }
  function diffFlat(before, after) { return Object.fromEntries(Object.entries(after).filter(([k,v]) => before[k] !== v)); }
  function requestGate() {
    let sequence = 0, generation = 0, writing = false;
    return {
      read: () => ({sequence:++sequence, generation}),
      accepts: token => !writing && token.sequence === sequence && token.generation === generation,
      begin: () => { writing = true; generation++; },
      end: () => { writing = false; generation++; },
    };
  }
  return {flatOf, diffFlat, requestGate};
})();
if (typeof module !== 'undefined') module.exports = BoardEdit;
