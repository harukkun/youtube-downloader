'use strict';
(() => {
  const isFileDrag = event => Array.from(event.dataTransfer?.types || []).includes('Files');
  // Keep a dropped file from navigating away from the editing session.
  for (const type of ['dragover', 'drop']) {
    document.addEventListener(type, event => {
      if (isFileDrag(event)) event.preventDefault();
    });
  }
  for (const zone of document.querySelectorAll('[data-video-drop]')) {
    const input = zone.querySelector('input[type="file"]');
    const message = zone.querySelector('.video-drop-message');
    let depth = 0;
    const reset = () => { depth = 0; zone.classList.remove('is-dragging'); };
    const error = text => { message.textContent = text; message.classList.add('is-error'); };
    zone.addEventListener('dragenter', event => {
      if (!isFileDrag(event) || input.disabled) return;
      event.preventDefault();
      depth++;
      zone.classList.add('is-dragging');
    });
    zone.addEventListener('dragover', event => {
      if (!isFileDrag(event)) return;
      event.preventDefault();
      event.dataTransfer.dropEffect = input.disabled ? 'none' : 'copy';
    });
    zone.addEventListener('dragleave', () => { if (--depth <= 0) reset(); });
    zone.addEventListener('drop', event => {
      if (!isFileDrag(event)) return;
      event.preventDefault();
      reset();
      if (input.disabled) return;
      const files = Array.from(event.dataTransfer.files);
      if (files.length !== 1) return error('원본 영상은 한 번에 한 개만 첨부해주세요.');
      if (!/\.(mp4|mov)$/i.test(files[0].name)) return error('MP4 또는 MOV 영상 파일을 첨부해주세요.');
      const transfer = new DataTransfer();
      transfer.items.add(files[0]);
      input.files = transfer.files;
      input.dispatchEvent(new Event('change', {bubbles: true}));
    });
    input.addEventListener('change', () => {
      message.textContent = '';
      message.classList.remove('is-error');
    });
    document.addEventListener('dragend', reset);
    document.addEventListener('drop', reset);
  }
})();
