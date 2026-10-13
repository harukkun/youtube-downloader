'use strict';
// Shared by the recipe editor and final upload review. No generated text is HTML.
(function(root) {
  function youtubeTags(value) {
    const seen = new Set();
    return String(value || '').split(/[,，\r\n]+/u).map(tag =>
      tag.normalize('NFC').trim().replace(/^[#＃]+/u, '').trim().replace(/\s+/gu, ' ')
    ).filter(tag => {
      const key = tag.toLowerCase();
      if (!tag || seen.has(key)) return false;
      seen.add(key); return true;
    });
  }
  function youtubeLength(tags) {
    return tags.reduce((n, tag) => n + Array.from(tag).length + (tag.includes(' ') ? 2 : 0), 0) + Math.max(0, tags.length - 1);
  }
  function hashtags(text) {
    const seen = new Set(), tags = [];
    for (const match of text.matchAll(/(?<![\p{L}\p{M}\p{N}_/#])#[\p{L}\p{M}\p{N}_]+/gu)) {
      const tag = match[0].normalize('NFC'), key = tag.toLowerCase();
      if (!seen.has(key)) { seen.add(key); tags.push(tag); }
    }
    return tags;
  }
  async function copy(text, element) {
    try { await navigator.clipboard.writeText(text); return true; } catch {}
    // Leave the visible text selected if fallback copying is also unavailable.
    element.focus({preventScroll:true});
    if (typeof element.select === 'function') element.select();
    else {
      const selection = window.getSelection(), range = document.createRange();
      range.selectNodeContents(element); selection.removeAllRanges(); selection.addRange(range);
    }
    try { return document.execCommand('copy'); } catch { return false; }
  }
  const api = {youtubeTags, youtubeLength, hashtags, copy};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.RecipeTags = api;
})(globalThis);
