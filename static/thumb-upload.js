'use strict';
// 쇼츠 현황판 썸네일 등록 — /shorts 와 /helper(썸네일 만들기)가 같이 쓰는 작은 헬퍼.
// 이미지는 브라우저에서 1080×1920 JPEG 로 다시 인코딩해 용량을 줄인 뒤 POST /api/shorts/thumbnail 로 보낸다.
// 서버는 Apps Script 웹 앱을 거쳐 구글 드라이브에 저장하고 시트의 해당 행에 기록한다 (sheets/Code.gs doPost).
window.ThumbUpload = (() => {
  const WIDTH = 1080, HEIGHT = 1920;

  /** 파일/Blob → 1080×1920 JPEG(품질 0.9) Blob. 비율이 다르면 검은 여백을 두고 안에 맞춘다. */
  async function normalizeImage(source) {
    const url = URL.createObjectURL(source);
    try {
      const image = new Image();
      image.src = url;
      await image.decode();
      const canvas = document.createElement('canvas');
      canvas.width = WIDTH; canvas.height = HEIGHT;
      const ctx = canvas.getContext('2d');
      ctx.fillStyle = '#000'; ctx.fillRect(0, 0, WIDTH, HEIGHT);
      const scale = Math.min(WIDTH / image.naturalWidth, HEIGHT / image.naturalHeight);
      const w = Math.round(image.naturalWidth * scale), h = Math.round(image.naturalHeight * scale);
      ctx.drawImage(image, Math.round((WIDTH - w) / 2), Math.round((HEIGHT - h) / 2), w, h);
      const blob = await new Promise(resolve => canvas.toBlob(resolve, 'image/jpeg', 0.9));
      if (!blob) throw new Error('이미지를 변환하지 못했습니다.');
      return blob;
    } catch (err) {
      if (err instanceof Error && err.message.includes('변환')) throw err;
      throw new Error('이미지를 읽을 수 없습니다. PNG·JPG·WebP 파일인지 확인해 주세요.');
    } finally {
      URL.revokeObjectURL(url);
    }
  }

  /** 시트의 특정 행에 썸네일 등록. 실패하면 서버 메시지를 담은 Error. */
  async function upload({ row, srcUrl, dish, blob, filename }) {
    const form = new FormData();
    form.append('row', String(row));
    form.append('src_url', srcUrl || '');
    form.append('dish', dish || '');
    form.append('file', blob, filename || `thumbnail-${row}.jpg`);
    const r = await fetch('/api/shorts/thumbnail', { method: 'POST', body: form });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.error || ('업로드 실패 (' + r.status + ')'));
    return data;
  }

  /** lh3 링크가 안 뜰 때 <img onerror> 에서 쓸 대체 주소. */
  function fallbackUrl(url) {
    const m = /googleusercontent\.com\/d\/([\w-]+)|[?&]id=([\w-]+)/.exec(url || '');
    return m ? `https://drive.google.com/thumbnail?id=${m[1] || m[2]}&sz=w640` : '';
  }

  /** 현황판 항목 → 썸네일 만들기(대상 행 지정) 링크. */
  function helperUrl(item) {
    const q = new URLSearchParams({ row: String(item.row), src: item.source?.url || '', dish: item.dish_title || '' });
    return '/helper?' + q.toString() + '#thumbnail';
  }

  return { normalizeImage, upload, fallbackUrl, helperUrl };
})();
