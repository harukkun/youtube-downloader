"""FFmpeg detectors and timeline interval operations (seconds)."""
import math
import re
import subprocess

DEFAULTS = dict(silence=True, freeze=True, mode='either', noise_db=-35,
                silence_seconds=0.7, freeze_seconds=1.5, freeze_db=-50, padding=0.12)


def options(raw):
    if not isinstance(raw, dict):
        raise ValueError('편집 설정 형식이 올바르지 않습니다.')
    out = DEFAULTS | raw
    for key in ('silence', 'freeze'):
        if type(out[key]) is not bool:
            raise ValueError('제거할 구간을 선택해주세요.')
    if not (out['silence'] or out['freeze']):
        raise ValueError('무음 또는 정지 구간을 하나 이상 선택해주세요.')
    if out['mode'] not in ('either', 'both'):
        raise ValueError('올바른 제거 방식을 선택해주세요.')
    for key, low, high in [('noise_db', -80, -5), ('silence_seconds', .1, 30),
                           ('freeze_seconds', .2, 60), ('freeze_db', -80, -20), ('padding', 0, 2)]:
        try:
            n = float(out[key])
        except (ValueError, TypeError):
            raise ValueError('편집 기준은 숫자로 입력해주세요.')
        if not math.isfinite(n) or not low <= n <= high:
            raise ValueError(f'{key} 값은 {low}–{high} 범위여야 합니다.')
        out[key] = n
    return {key: out[key] for key in DEFAULTS}


def union(ranges):
    result = []
    for start, end in sorted(ranges):
        if end <= start:
            continue
        if result and start <= result[-1][1] + 1e-6:
            result[-1][1] = max(result[-1][1], end)
        else:
            result.append([start, end])
    return result


def intersect(a, b):
    a, b = union(a), union(b)
    out, i, j = [], 0, 0
    while i < len(a) and j < len(b):
        lo, hi = max(a[i][0], b[j][0]), min(a[i][1], b[j][1])
        if hi > lo:
            out.append([lo, hi])
        if a[i][1] < b[j][1]:
            i += 1
        else:
            j += 1
    return union(out)


def complement(ranges, duration):
    cursor, out = 0, []
    for start, end in union(intersect(ranges, [[0, duration]])):
        if start > cursor:
            out.append([cursor, start])
        cursor = end
    if cursor < duration:
        out.append([cursor, duration])
    return out


def parse_log(text, kind, duration):
    # FFmpeg leaves *_end out when a silent/frozen interval reaches EOF.
    pattern = rf'{kind}_(start|end):\s*(-?[\d.]+)'
    ranges, start = [], None
    for event, value in re.findall(pattern, text):
        value = max(0, min(float(value), duration))
        if event == 'start':
            start = value
        elif start is not None:
            ranges.append([start, value])
            start = None
    if start is not None:
        ranges.append([start, duration])
    return union(ranges)


def detect(path, duration, has_audio, opts, runner=None):
    args = ['ffmpeg', '-hide_banner', '-nostdin', '-nostats', '-i', str(path)]
    if opts['freeze']:
        args += ['-map', '0:v:0', '-vf',
                 f"scale=320:-2,freezedetect=n={opts['freeze_db']}dB:d={opts['freeze_seconds']}"]
    else:
        args += ['-vn']
    if opts['silence'] and has_audio:
        args += ['-map', '0:a:0', '-af',
                 f"silencedetect=n={opts['noise_db']}dB:d={opts['silence_seconds']}"]
    else:
        args += ['-an']
    if not opts['freeze'] and not (opts['silence'] and has_audio):
        return {'silence': [], 'freeze': []}
    args += ['-f', 'null', '-']
    if runner:
        log = runner(args)
    else:
        p = subprocess.run(args, capture_output=True, text=True, timeout=14400)
        if p.returncode:
            raise ValueError('영상 분석에 실패했습니다: ' + p.stderr[-500:])
        log = p.stderr
    return {kind: parse_log(log, kind, duration) for kind in ('silence', 'freeze')}
