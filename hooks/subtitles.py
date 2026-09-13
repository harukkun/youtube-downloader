"""Keep source cue identity; overlapping cues are not necessarily duplicates."""
import re
import html
import hashlib
import json
import pysubs2

VERSION = 1


def parse(raw, fmt='srt', preserve_context=False):
    if isinstance(raw, bytes):
        try:
            raw = raw.decode('utf-8-sig')
        except UnicodeDecodeError:
            try:
                raw = raw.decode('cp949')
            except UnicodeDecodeError as e:
                raise ValueError('자막 인코딩을 읽을 수 없습니다. UTF-8 SRT로 저장해주세요.') from e
    if fmt == 'srt' and not re.search(r'\d+:\d{2}:\d{2}[,.]\d+\s*-->\s*\d+:\d{2}:\d{2}[,.]\d+', raw):
        raise ValueError('올바른 SRT 시간 구간이 없습니다.')
    if fmt == 'srt':
        timing = r'\s*\d+:\d{2}:\d{2}[,.]\d+\s*-->\s*\d+:\d{2}:\d{2}[,.]\d+\s*'
        if any('-->' in line and not re.fullmatch(timing, line) for line in raw.splitlines()):
            raise ValueError('손상된 SRT 시간 구간이 있습니다.')
    try:
        events = pysubs2.SSAFile.from_string(raw, format_=fmt)
    except Exception as e:
        raise ValueError('자막 파일을 읽을 수 없습니다.') from e
    cues = []
    for i, event in enumerate(events, 1):
        if event.start < 0 or event.end <= event.start:
            raise ValueError(f'자막 {i}의 시작·종료 시간이 올바르지 않습니다.')
        original = html.unescape(re.sub(r'<[^>]+>', '', event.plaintext)).strip()
        spoken = re.sub(r'\[(?:음악|박수|웃음|Music|Applause)\]', '', original, flags=re.I)
        text = original if preserve_context else spoken
        text = ' '.join(text.split())
        if not text:
            continue
        # Rolling captions repeat the prior line while adding the next one.
        if cues and event.start < cues[-1]['end_ms']:
            previous = cues[-1]['text']
            if text == previous:
                cues[-1]['end_ms'] = max(cues[-1]['end_ms'], event.end)
                continue
            if text.startswith(previous + ' '):
                text = text[len(previous):].strip()
        cues.append({'id': i, 'start_ms': event.start, 'end_ms': event.end,
                     'text': text, 'original_text': original, **({'context_only': not spoken.strip()} if preserve_context else {})})
    if not cues:
        raise ValueError('분석 가능한 발언이 자막에 없습니다.')
    if any(a['start_ms'] > b['start_ms'] for a, b in zip(cues, cues[1:])):
        raise ValueError('자막 시간 순서가 올바르지 않습니다.')
    return cues


def fingerprint(cues, settings):
    data = {'version': VERSION, 'cues': cues, 'settings': settings}
    return hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def chunks(cues, limit=10000, overlap=5):
    start = 0
    while start < len(cues):
        end, size = start, 0
        while end < len(cues) and (size < limit or end == start):
            size += len(cues[end]['text']) + 20
            end += 1
        yield cues[start:end]
        if end == len(cues):
            break
        start = max(start + 1, end - overlap)
