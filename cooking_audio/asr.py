"""Local cancellable speech recognition with content/range/model cache."""
import hashlib
import json
import os
import subprocess
import threading
import time
from pathlib import Path
from hooks import media
from hooks.store import uid

LOCK = threading.Lock()
MODEL = 'mlx-community/whisper-small-mlx'


def config():
    python = os.environ.get('COOKING_ASR_PYTHON', str(Path(__file__).resolve().parents[1] / '.venv-asr/bin/python'))
    available = False
    if Path(python).is_file():
        try:
            available = subprocess.run([python, '-c', "import importlib.util; raise SystemExit(0 if importlib.util.find_spec('mlx_whisper') else 1)"], capture_output=True, timeout=5).returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            pass
    return {'python': python, 'model': os.environ.get('COOKING_ASR_MODEL', MODEL), 'available': available}


def transcribe(source, start, end, cache, notify, cancelled, digest=None):
    cfg = config()
    if not cfg['available']:
        raise ValueError('로컬 음성 인식 환경이 없습니다. scripts/setup-cooking-asr.sh를 실행하거나 COOKING_ASR_PYTHON을 설정해주세요.')
    if not digest:
        h = hashlib.sha256()
        with source.open('rb') as f:
            while block := f.read(1024 * 1024):
                h.update(block)
        digest = h.hexdigest()
    key = hashlib.sha256(json.dumps([digest, start, end, cfg['model'], 'ko', 'words-v1']).encode()).hexdigest()
    path = cache / ('asr-' + key + '.json')
    with LOCK:
        if cancelled():
            raise InterruptedError('음성 인식을 취소했습니다.')
        if path.exists():
            return json.loads(path.read_text()), True
        duration = media.probe(source)['duration']
        crop_start, crop_end = max(0, start - 1), min(duration, end + 1)
        work = cache / ('asr-work-' + uid())
        work.mkdir()
        audio, output = work / 'audio.wav', work / 'result.json'
        try:
            notify('음성 인식용 구간 준비 중')
            media.run(['ffmpeg', '-v', 'error', '-y', '-ss', str(crop_start), '-i', str(source),
                       '-t', str(crop_end-crop_start), '-vn', '-ac', '1', '-ar', '16000', str(audio)])
            notify('로컬 음성 인식 중 · 최초 실행은 모델 다운로드로 시간이 걸릴 수 있습니다.')
            with (work / 'worker.log').open('w+') as log:
                process = subprocess.Popen([cfg['python'], str(Path(__file__).with_name('asr_worker.py')),
                                            str(audio), str(output), '--model', cfg['model']], stdout=log, stderr=log)
                deadline = time.monotonic() + 14400
                try:
                    while process.poll() is None:
                        if cancelled() or time.monotonic() > deadline:
                            raise InterruptedError('음성 인식이 취소되었거나 처리 시간이 초과되었습니다.')
                        time.sleep(.2)
                    if process.returncode:
                        log.seek(max(0, log.tell()-1500))
                        raise ValueError('로컬 음성 인식 실패 · 모델 다운로드/메모리/설치를 확인해주세요. ' + log.read()[-1000:])
                finally:
                    if process.poll() is None:
                        process.terminate()
                        try:
                            process.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait()
            result = json.loads(output.read_text())
            cues = []
            for segment in result.get('segments', []):
                words = []
                for word in segment.get('words', []):
                    a, b = crop_start + word['start'], crop_start + word['end']
                    if a < end and b > start:
                        words.append({'start': max(start, a), 'end': min(end, b), 'word': word['word']})
                if not words:
                    continue
                text = ''.join(w['word'] for w in words).strip()
                if text and words[-1]['end'] > words[0]['start']:
                    cues.append({'id': len(cues)+1, 'start_ms': round(words[0]['start']*1000),
                                 'end_ms': round(words[-1]['end']*1000), 'text': text, 'original_text': text, 'words': words})
            if not cues:
                raise ValueError('이 구간에서 인식된 대사가 없습니다. 구간을 넓히거나 직접 입력해주세요.')
            temp = path.with_suffix('.tmp')
            temp.write_text(json.dumps(cues, ensure_ascii=False))
            os.replace(temp, path)
            return cues, False
        finally:
            import shutil
            shutil.rmtree(work, ignore_errors=True)
