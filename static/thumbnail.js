'use strict';
// Apply adjustments to a fresh pixel buffer, never to the original frame.
const ThumbnailTone = {
  presets: {original:[0,0,0,0], warm:[5,5,10,20], vivid:[5,20,20,0], bright:[15,-5,10,5]},
  apply(data, values) {
    const [brightness, contrast, saturation, temperature] = values;
    if(values.every(value => value === 0)) return data;
    const light = brightness * 1.275, gain = 2 ** (contrast / 100), color = 1 + saturation / 100, warmth = temperature * .4;
    for(let i=0;i<data.length;i+=4){
      const r=(data[i]+light-127.5)*gain+127.5, g=(data[i+1]+light-127.5)*gain+127.5, b=(data[i+2]+light-127.5)*gain+127.5;
      const luma=.2126*r+.7152*g+.0722*b;
      data[i]=luma+(r-luma)*color+warmth;
      data[i+1]=luma+(g-luma)*color;
      data[i+2]=luma+(b-luma)*color-warmth;
    }
    return data;
  }
};
if(typeof module !== 'undefined') module.exports=ThumbnailTone;
if(typeof document !== 'undefined') (() => {
  const $ = id => document.getElementById(id);
  const canvas = $('canvas'), ctx = canvas.getContext('2d');
  let video = $('video'), objectURL = null, frame = null, fontReady = false, exporting = false;
  let editRevision = 0;
  let generation = 0, offsetX = 0, offsetY = 0, zoom = 1, drag = null;
  const defaults = {subtitle: {size: 88, x: 50, y: 39}, title: {size: 230, x: 50, y: 50}};
  const styles = structuredClone(defaults);
  const toneKeys=['brightness','contrast','saturation','temperature'];
  let tone=[0,0,0,0], toneFrame=null, toneCache=null, toneDirty=true, toneRAF=null;
  function updateToneControls(preset='custom') {
    $('tonePreset').value=preset;
    toneKeys.forEach((key,i)=>{
      $('tone-'+key).value=$('tone-'+key+'-range').value=tone[i];
      $('tone-'+key+'-value').textContent=(tone[i]>0?'+':'')+tone[i];
    });
  }
  function resetTone() {
    tone=[0,0,0,0];toneFrame=toneCache=null;toneDirty=true;
    if(toneRAF!==null){cancelAnimationFrame(toneRAF);toneRAF=null;}
    updateToneControls('original');
  }
  function changeTone() {
    toneDirty=true;
    // Invalidate an approved/exporting image immediately, even before the next paint.
    editRevision++;document.dispatchEvent(new CustomEvent('thumbnail:changed'));
    if(toneRAF===null) toneRAF=requestAnimationFrame(()=>{toneRAF=null;render(false);});
  }
  function tonedFrame() {
    if(tone.every(value=>value===0)) return frame;
    if(toneDirty || toneFrame!==frame){
      toneCache ||= document.createElement('canvas');
      toneCache.width=frame.width;toneCache.height=frame.height;
      const context=toneCache.getContext('2d');context.drawImage(frame,0,0);
      const pixels=context.getImageData(0,0,frame.width,frame.height);
      ThumbnailTone.apply(pixels.data,tone);context.putImageData(pixels,0,0);
      toneFrame=frame;toneDirty=false;
    }
    return toneCache;
  }
  toneKeys.forEach((key,i)=>{
    const input=$('tone-'+key),range=$('tone-'+key+'-range');
    input.oninput=range.oninput=e=>{
      if(e.target.value==='' || !e.target.validity.valid) return;
      const value=Number(e.target.value);if(tone[i]===value)return;
      tone[i]=value;updateToneControls();changeTone();
    };
    input.onchange=()=>{
      const value=input.value===''?tone[i]:Number(input.value);
      const normalized=Math.round(Math.max(-100,Math.min(100,Number.isFinite(value)?value:0)));
      if(tone[i]!==normalized){tone[i]=normalized;updateToneControls();changeTone();}
      else updateToneControls($('tonePreset').value);
    };
  });
  $('tonePreset').onchange=()=>{
    const preset=$('tonePreset').value;if(!ThumbnailTone.presets[preset])return;
    tone=[...ThumbnailTone.presets[preset]];updateToneControls(preset);changeTone();
  };
  $('resetTone').onclick=()=>{resetTone();changeTone();};
  resetTone();
  const message = (id, text, error = false) => { $(id).textContent = text; $(id).classList.toggle('error', error); };
  const clock = seconds => `${String(Math.floor(seconds / 60)).padStart(2, '0')}:${(seconds % 60).toFixed(1).padStart(4, '0')}`;
  const ready = () => video.readyState >= 2 && video.videoWidth > 0 && !video.seeking && Number.isFinite(video.duration);
  function controls() {
    document.querySelectorAll('[data-tone-control]').forEach(control=>control.disabled=!frame);
    $('capture').disabled = !ready() || !video.paused;
    for (const id of ['timeline', 'prev', 'next']) $(id).disabled = !Number.isFinite(video.duration) || video.readyState < 1;
    $('export').disabled = !frame || !fontReady || exporting;
    $('zoom').disabled = $('zoom-range').disabled = $('resetCrop').disabled = !frame;
  }
  function crop() {
    const scale = Math.max(1080 / frame.width, 1920 / frame.height) * zoom;
    const width = frame.width * scale, height = frame.height * scale;
    offsetX = Math.max(-(width - 1080) / 2, Math.min((width - 1080) / 2, offsetX));
    offsetY = Math.max(-(height - 1920) / 2, Math.min((height - 1920) / 2, offsetY));
    return {x: (1080 - width) / 2 + offsetX, y: (1920 - height) / 2 + offsetY, width, height};
  }
  function drawText(kind) {
    const lines = $(kind).value.replace(/\r/g, '').split('\n').slice(0, kind === 'title' ? 2 : 1);
    if (!lines.some(line => line.trim())) return;
    const style = styles[kind];
    let size = style.size;
    const x = style.x * 10.8, y = style.y * 19.2;
    const maxWidth = Math.max(10, Math.min(x, 1080 - x) * 2 - 36);
    ctx.font = `700 ${size}px CookieRun`;
    const measured = Math.max(...lines.map(line => ctx.measureText(line).width));
    if (measured + size * .13 > maxWidth) size *= maxWidth / (measured + size * .13);
    ctx.font = `700 ${size}px CookieRun`;
    ctx.textAlign = 'center'; ctx.textBaseline = 'alphabetic'; ctx.lineJoin = 'round';
    ctx.strokeStyle = $(`${kind}-stroke`).value; ctx.fillStyle = $(`${kind}-fill`).value; ctx.lineWidth = size * .13;
    const metrics = ctx.measureText('한글Ag');
    const ascent = metrics.actualBoundingBoxAscent, descent = metrics.actualBoundingBoxDescent;
    const height = ascent + descent + (lines.length - 1) * size * 1.1;
    const top = Math.max(ctx.lineWidth, Math.min(1920 - height - ctx.lineWidth, y - height / 2));
    lines.forEach((line, i) => {
      const baseline = top + ascent + i * size * 1.1;
      ctx.strokeText(line, x, baseline); ctx.fillText(line, x, baseline);
    });
  }
  function render(changed = true) {
    if (changed) { editRevision++; document.dispatchEvent(new CustomEvent("thumbnail:changed")); }
    ctx.fillStyle = '#171a21'; ctx.fillRect(0, 0, 1080, 1920);
    if (frame) { const c = crop(); ctx.drawImage(tonedFrame(), c.x, c.y, c.width, c.height); }
    if (frame && fontReady) { drawText('subtitle'); drawText('title'); }
    const empty = $('empty');
    empty.hidden = !!frame;
    // 헬퍼 페이지처럼 다른 레이아웃 CSS가 함께 적용되는 경우에도
    // 안내 오버레이가 캔버스를 덮지 않도록 표시 상태를 직접 동기화한다.
    empty.style.display = frame ? 'none' : 'flex';
    controls();
  }
  async function loadFont() {
    fontReady = false; $('retryFont').hidden = true; controls();
    message('fontStatus', 'CookieRun Bold 폰트를 불러오는 중입니다…');
    try {
      const face = new FontFace('CookieRun', 'url(/static/fonts/CookieRun-Bold.otf)', {weight: '700'});
      await face.load(); document.fonts.add(face); fontReady = true;
      message('fontStatus', 'CookieRun Bold · 미리보기와 동일한 이미지로 저장됩니다.');
    } catch { message('fontStatus', '폰트를 불러오지 못했습니다. 다시 시도해 주세요.', true); $('retryFont').hidden = false; }
    render(false);
  }
  $('retryFont').onclick = loadFont;
  function bindVideo(v, token) {
    const valid = fn => () => { if (generation === token) fn(); };
    for (const event of ['loadeddata', 'canplay', 'seeked', 'pause', 'play', 'seeking']) v.addEventListener(event, valid(controls));
    v.addEventListener('loadedmetadata', valid(() => {
      if (!Number.isFinite(v.duration) || v.duration <= 0 || !v.videoWidth) { failVideo(); return; }
      $('timeline').max = Math.max(0, v.duration - .001);
      message('videoStatus', '재생하거나 탐색한 뒤 일시정지하고 ‘이 장면 사용’을 눌러주세요.'); controls();
    }));
    v.addEventListener('timeupdate', valid(() => {
      $('timeline').value = v.currentTime;
      $('time').textContent = `${clock(v.currentTime)} / ${clock(Number.isFinite(v.duration) ? v.duration : 0)}`;
    }));
    v.addEventListener('error', valid(failVideo));
  }
  function failVideo() {
    frame = null; video.pause();
    message('videoStatus', '이 영상을 재생할 수 없습니다. MP4(H.264 영상 / AAC 오디오)로 변환한 뒤 다시 선택해 주세요.', true);
    video.removeAttribute('src'); video.load(); render();
  }
  function resetCrop() { zoom = 1; offsetX = offsetY = 0; $('zoom').value = $('zoom-range').value = 100; $('zoomValue').textContent = '100%'; render(); }
  let heicDecoder;
  function loadHeicDecoder() {
    // Load the bundled decoder only for HEIC/HEIF; the original stays in the browser.
    if (!heicDecoder) {
      heicDecoder = new Promise((resolve, reject) => {
        const script = document.createElement('script');
        script.src = '/static/vendor/heic-to/heic-to.js';
        script.onload = () => typeof window.HeicTo === 'function' ? resolve(window.HeicTo) : reject(new Error('HEIC decoder unavailable'));
        script.onerror = () => { script.remove(); reject(new Error('HEIC decoder unavailable')); };
        document.head.append(script);
      }).catch(error => { heicDecoder = null; throw error; });
    }
    return heicDecoder;
  }
  async function loadFile(file) {
    if (!file) return;
    resetTone();
    generation++; video.pause(); video.removeAttribute('src'); video.load();
    if (objectURL) URL.revokeObjectURL(objectURL);
    objectURL = null; frame = null; drag = null;
    const replacement = video.cloneNode(false); video.replaceWith(replacement); video = replacement;
    bindVideo(video, generation);
    $('videoArea').hidden = false; $('timeline').value = 0; $('time').textContent = '00:00.0 / 00:00.0';
    $('fileInfo').textContent = `${file.name} · ${(file.size / 1024 / 1024).toFixed(1)} MB`;
    $('frameInfo').textContent = '선택한 장면이 여기에 표시됩니다.';
    message('exportStatus', ''); resetCrop();
    const isHeic = /^image\/hei[cf](?:-sequence)?$/i.test(file.type) || /\.hei[cf]$/i.test(file.name);
    const isImage = isHeic || /^image\/(png|jpeg|webp)$/.test(file.type) || /\.(png|jpe?g|webp)$/i.test(file.name);
    $('videoArea').hidden = isImage;
    if (isImage) {
      const token = generation;
      let imageURL = null;
      message('videoStatus', isHeic ? 'HEIC/HEIF 이미지를 변환하는 중입니다…' : '이미지를 불러오는 중입니다…');
      try {
        let source = file;
        if (isHeic) {
          const convert = await loadHeicDecoder();
          if (generation !== token) return;
          source = await convert({blob: file, type: 'image/png'});
          if (generation !== token) return;
        }
        imageURL = URL.createObjectURL(source); objectURL = imageURL;
        const image = new Image(); image.src = imageURL; await image.decode();
        if (generation !== token) return;
        const selected = document.createElement('canvas');
        selected.width = image.naturalWidth; selected.height = image.naturalHeight;
        const context = selected.getContext('2d');
        context.fillStyle = '#000'; context.fillRect(0, 0, selected.width, selected.height);
        context.drawImage(image, 0, 0); frame = selected; resetCrop();
        $('frameInfo').textContent = `이미지 · ${selected.width} × ${selected.height} 원본`;
        message('videoStatus', '이미지를 선택했습니다. 문구와 구도를 조정해 보세요.');
      } catch {
        if (generation === token) message('videoStatus', isHeic ? 'HEIC/HEIF 이미지를 변환하지 못했습니다. 파일을 확인하고 다시 선택해 주세요.' : '이미지를 읽지 못했습니다. 정상적인 PNG, JPG, WebP, HEIC 또는 HEIF 파일을 선택해 주세요.', true);
      } finally {
        if (imageURL) URL.revokeObjectURL(imageURL);
        if (generation === token) { objectURL = null; controls(); }
      }
      return;
    }
    message('videoStatus', '영상을 불러오는 중입니다…');
    objectURL = URL.createObjectURL(file);
    video.src = objectURL; video.load(); controls();
  }
  $('file').onchange = event => { loadFile(event.target.files[0]); event.target.value = ''; };
  for (const event of ['dragenter', 'dragover']) $('drop').addEventListener(event, e => {e.preventDefault(); $('drop').classList.add('dragging');});
  for (const event of ['dragleave', 'drop']) $('drop').addEventListener(event, e => {e.preventDefault(); $('drop').classList.remove('dragging');});
  $('drop').addEventListener('drop', e => loadFile(e.dataTransfer.files[0]));
  function seek(time) { if (!Number.isFinite(video.duration)) return; video.pause(); video.currentTime = Math.max(0, Math.min(video.duration - .001, time)); controls(); }
  $('timeline').oninput = e => seek(Number(e.target.value));
  $('prev').onclick = () => seek(video.currentTime - .1);
  $('next').onclick = () => seek(video.currentTime + .1);
  $('capture').onclick = () => {
    if (!ready() || !video.paused) return;
    try {
      const selected = document.createElement('canvas'); selected.width = video.videoWidth; selected.height = video.videoHeight;
      selected.getContext('2d').drawImage(video, 0, 0); frame = selected; resetCrop();
      $('frameInfo').textContent = `${clock(video.currentTime)} 장면 · ${video.videoWidth} × ${video.videoHeight} 원본`;
      message('videoStatus', '장면을 선택했습니다. 문구와 구도를 조정해 보세요.');
    } catch { message('videoStatus', '장면을 읽지 못했습니다. 다른 시점에서 다시 시도해 주세요.', true); }
  };
  document.querySelectorAll('.type-controls').forEach(container => {
    const kind = container.dataset.kind;
    for (const [key, label, min, max] of [['size', '크기', 24, 320], ['x', '가로 위치', 5, 95], ['y', '세로 위치', 5, 95]]) {
      const id = `${kind}-${key}`;
      const wrapper = document.createElement('div');
      wrapper.innerHTML = `<label for="${id}">${label}<output id="${id}-value"></output></label><input id="${id}" type="number" min="${min}" max="${max}" step="1" value="${styles[kind][key]}"><input id="${id}-range" type="range" aria-label="${kind === 'title' ? '타이틀' : '서브타이틀'} ${label}" min="${min}" max="${max}" step="1" value="${styles[kind][key]}">`;
      container.append(wrapper);
      $(id).oninput = e => {
        if (e.target.value === '' || !e.target.validity.valid) return;
        styles[kind][key] = Number(e.target.value); updateOutputs(); render();
      };
      $(`${id}-range`).oninput = $(id).oninput;
      $(id).onchange = e => {
        const value = e.target.value === '' ? styles[kind][key] : Number(e.target.value);
        styles[kind][key] = Math.round(Math.max(min, Math.min(max, Number.isFinite(value) ? value : defaults[kind][key])));
        updateOutputs(); render();
      };
    }
  });
  function updateOutputs() { for (const kind of ['subtitle', 'title']) for (const key of ['size', 'x', 'y']) { $(`${kind}-${key}`).value = $(`${kind}-${key}-range`).value = styles[kind][key]; $(`${kind}-${key}-value`).textContent = styles[kind][key] + (key === 'size' ? 'px' : '%'); } }
  for (const kind of ['subtitle', 'title']) {
    for (const color of ['fill', 'stroke']) $(`${kind}-${color}`).oninput = render;
  }
  $('subtitle').oninput = render;
  $('title').oninput = () => { const lines = $('title').value.split('\n'); if (lines.length > 2) $('title').value = lines.slice(0, 2).join('\n'); render(); };
  $('resetType').onclick = () => { Object.assign(styles, structuredClone(defaults)); updateOutputs(); render(); };
  $('zoom').oninput = e => {
    if (e.target.value === '' || !e.target.validity.valid) return;
    zoom = Number(e.target.value) / 100;
    $('zoom').value = $('zoom-range').value = Math.round(zoom * 100);
    $('zoomValue').textContent = `${Math.round(zoom * 100)}%`; render();
  };
  $('zoom-range').oninput = $('zoom').oninput;
  $('zoom').onchange = e => {
    const value = e.target.value === '' ? zoom * 100 : Number(e.target.value);
    const percent = Math.round(Math.max(100, Math.min(300, Number.isFinite(value) ? value : 100)));
    zoom = percent / 100; e.target.value = $('zoom-range').value = percent;
    $('zoomValue').textContent = `${percent}%`; render();
  };
  $('resetCrop').onclick = resetCrop;
  canvas.onpointerdown = e => { if (!frame) return; drag = {x: e.clientX, y: e.clientY}; canvas.setPointerCapture(e.pointerId); };
  canvas.onpointermove = e => { if (!drag || !frame) return; const ratio = 1080 / canvas.getBoundingClientRect().width; offsetX += (e.clientX - drag.x) * ratio; offsetY += (e.clientY - drag.y) * ratio; drag = {x: e.clientX, y: e.clientY}; render(); };
  canvas.onpointerup = canvas.onpointercancel = canvas.onlostpointercapture = () => { drag = null; };
  async function exportImage(extension = 'jpg', quality = 1) {
    if (!frame || !fontReady || exporting) throw new Error('장면과 폰트를 먼저 준비해 주세요.');
    exporting = true; controls();
    const token = generation, revision = editRevision;
    try {
      render(false);
      const blob = await new Promise(resolve => canvas.toBlob(resolve, extension === 'jpg' ? 'image/jpeg' : 'image/png', quality));
      if (token !== generation || revision !== editRevision) throw new Error('이미지가 변경되었습니다. 다시 확정해 주세요.');
      if (!blob) throw new Error('이미지를 만들지 못했습니다.');
      return blob;
    } finally { exporting = false; controls(); }
  }
  function download(blob, extension, stamp) {
    const url = URL.createObjectURL(blob), a = document.createElement('a');
    a.href = url; a.download = `shorts-thumbnail-${stamp}.${extension}`; document.body.append(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 60000);
  }
  $('export').onclick = async () => {
    message('exportStatus', '이미지를 만드는 중입니다…');
    const extension = $('format').value;
    try {
      const blob = await exportImage(extension);
      const date = new Date(), pad = n => String(n).padStart(2, '0');
      const stamp = `${date.getFullYear()}${pad(date.getMonth() + 1)}${pad(date.getDate())}-${pad(date.getHours())}${pad(date.getMinutes())}${pad(date.getSeconds())}`;
      download(blob, extension, stamp);
      message('exportStatus', `1080 × 1920 ${extension.toUpperCase()} 다운로드를 시작했습니다.`);
      document.dispatchEvent(new CustomEvent('thumbnail:exported', {detail:{blob, extension, stamp}}));
    } catch (e) { message('exportStatus', e.message, true); }
  };
  function reset() {
    resetTone();
    generation++; video.pause(); video.removeAttribute('src'); video.load();
    if (objectURL) URL.revokeObjectURL(objectURL);
    objectURL = null; frame = null; drag = null; zoom = 1; offsetX = offsetY = 0;
    Object.assign(styles, structuredClone(defaults));
    $('title').value = ''; $('subtitle').value = ''; $('fileInfo').textContent = '';
    $('videoArea').hidden = true; $('frameInfo').textContent = '새 파일을 선택해 편집하세요.';
    $('zoom').value = $('zoom-range').value = 100; $('zoomValue').textContent = '100%';
    message('exportStatus', ''); message('videoStatus', '영상이나 이미지를 선택해 시작하세요.');
    updateOutputs(); render();
  }
  window.ThumbnailEditor = {exportImage, reset, download};
  window.addEventListener('beforeunload', () => { if (objectURL) URL.revokeObjectURL(objectURL); });
  updateOutputs(); render(false); loadFont();
})();
