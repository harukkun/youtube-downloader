"""Local media operations; remote input is restricted to canonical YouTube IDs."""
import json
import re
import subprocess
import threading
from pathlib import Path
from urllib.parse import urlsplit, parse_qs
import yt_dlp
from yt_dlp.networking import Request
from ydl_common import ydl_opts

ENCODING = threading.Semaphore(1)


def youtube_url(raw):
    if not raw:
        return ''
    u = urlsplit(raw.strip())
    if u.scheme != 'https' or u.username or u.password or u.port not in (None, 443):
        raise ValueError('https YouTube 단일 영상 URL을 입력해주세요.')
    if u.hostname in ('youtu.be', 'www.youtu.be'):
        ident = u.path.strip('/')
    elif u.hostname in ('youtube.com', 'www.youtube.com', 'm.youtube.com'):
        if u.path == '/watch':
            ident = parse_qs(u.query).get('v', [''])[0]
        elif u.path.startswith(('/shorts/', '/live/', '/embed/')):
            ident = u.path.split('/')[2]
        else:
            ident = ''
    else:
        ident = ''
    if not re.fullmatch(r'[\w-]{11}', ident, re.ASCII):
        raise ValueError('YouTube 단일 영상 URL을 입력해주세요.')
    return 'https://www.youtube.com/watch?v=' + ident


def run(args, timeout=1200):
    try:
        p = subprocess.run(args, capture_output=True, timeout=timeout)
    except FileNotFoundError as e:
        raise ValueError('FFmpeg와 ffprobe 설치를 확인해주세요.') from e
    except subprocess.TimeoutExpired as e:
        raise ValueError('영상 처리 시간이 초과되었습니다. 다시 시도해주세요.') from e
    if p.returncode:
        raise ValueError('영상 처리 실패: ' + p.stderr.decode(errors='replace')[-600:])
    return p.stdout


def probe(path):
    data = json.loads(run(['ffprobe', '-v', 'error', '-show_format', '-show_streams', '-of', 'json', str(path)], 60))
    video = next((s for s in data['streams'] if s['codec_type'] == 'video' and not s.get('disposition', {}).get('attached_pic')), None)
    if not video:
        raise ValueError('영상 트랙이 없는 파일입니다.')
    duration = float(data.get('format', {}).get('duration', 0))
    if not 0 < duration < float('inf'):
        raise ValueError('영상 길이를 확인할 수 없습니다.')
    audio = next((s for s in data['streams'] if s['codec_type'] == 'audio'), None)
    return {'duration': duration, 'width': video['width'], 'height': video['height'],
            'video_codec': video['codec_name'], 'audio_codec': audio['codec_name'] if audio else None,
            'subtitles': [s['index'] for s in data['streams'] if s['codec_type'] == 'subtitle'
                          and s['codec_name'] in ('subrip', 'mov_text', 'ass', 'ssa', 'webvtt', 'text')]}


def embedded(path, index):
    return run(['ffmpeg', '-v', 'error', '-i', str(path), '-map', f'0:{index}', '-f', 'srt', '-'], 120)


def remote_info(url):
    opts = ydl_opts({'quiet': True, 'no_warnings': True, 'noplaylist': True, 'skip_download': True,
                     'socket_timeout': 25, 'retries': 1, 'extractor_retries': 1})
    with yt_dlp.YoutubeDL(opts) as y:
        info = y.extract_info(youtube_url(url), download=False)
        if info.get('is_live'):
            raise ValueError('진행 중인 라이브 영상은 지원하지 않습니다.')
        return info


def remote_subtitle(info):
    tracks, source = None, None
    for key in ('ko', 'ko-KR', 'ko-orig'):
        if info.get('subtitles', {}).get(key):
            tracks, source = info['subtitles'][key], 'youtube_manual'
            break
    if not tracks:
        for key in ('ko-orig', 'ko'):
            options = info.get('automatic_captions', {}).get(key, [])
            options = [t for t in options if not parse_qs(urlsplit(t['url']).query).get('tlang')]
            if options:
                tracks, source = options, 'youtube_auto'
                break
    if not tracks:
        return None
    track = next((t for fmt in ('srt', 'vtt') for t in tracks if t['ext'] == fmt), None)
    if not track:
        raise ValueError('가져올 수 있는 SRT/VTT 자막 형식이 없습니다.')
    with yt_dlp.YoutubeDL({'socket_timeout': 25, 'quiet': True}) as y:
        with y.urlopen(Request(track['url'], headers=info.get('http_headers') or {})) as response:
            raw = response.read(5 * 1024 * 1024 + 1)
    if not raw or len(raw) > 5 * 1024 * 1024:
        raise ValueError('자막 응답이 비어 있거나 너무 큽니다. 다시 시도해주세요.')
    return raw, track['ext'], source


def download(url, directory, progress):
    directory.mkdir(parents=True, exist_ok=True)
    existing = list(directory.glob('source.*'))
    for path in existing:
        if path.suffix not in ('.part', '.ytdl'):
            try:
                probe(path)
                return path
            except ValueError:
                pass
    opts = ydl_opts({'quiet': True, 'no_warnings': True, 'noplaylist': True,
                     'format': 'bestvideo[height<=1080][vcodec^=avc1]+bestaudio[ext=m4a]/bestvideo[height<=1080]+bestaudio/best[height<=1080]',
                     'merge_output_format': 'mp4', 'outtmpl': str(directory / 'source.%(ext)s'),
                     'socket_timeout': 30, 'retries': 2,
                     'progress_hooks': [lambda d: progress(d.get('_percent_str', '').strip())]})
    with yt_dlp.YoutubeDL(opts) as y:
        y.extract_info(youtube_url(url), download=True)
    for path in directory.glob('source.*'):
        if path.suffix in ('.mp4', '.mkv', '.webm', '.mov'):
            probe(path)
            return path
    raise ValueError('다운로드한 원본 파일을 찾지 못했습니다.')


def encode(source, output, start=None, end=None, preview=False):
    args = ['ffmpeg', '-v', 'error', '-y']
    if start is not None:
        args += ['-ss', str(start)]
    args += ['-i', str(source)]
    if end is not None:
        args += ['-t', str(end - start)]
    args += ['-map', '0:v:0', '-map', '0:a:0?', '-sn', '-dn', '-map_metadata', '-1',
             '-vf', "scale=w='min(1280,iw)':h='min(720,ih)':force_original_aspect_ratio=decrease:force_divisible_by=2" if preview else 'scale=trunc(iw/2)*2:trunc(ih/2)*2',
             '-c:v', 'libx264', '-crf', '23' if preview else '18', '-preset', 'fast' if preview else 'medium',
             '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-b:a', '192k', '-movflags', '+faststart', str(output)]
    with ENCODING:
        try:
            run(args, 14400 if preview else 3600)
            probe(output)
        except Exception:
            output.unlink(missing_ok=True)
            raise


def encode_audio(source, output, start, end):
    args = ['ffmpeg', '-v', 'error', '-y', '-ss', str(start), '-i', str(source),
            '-t', str(end - start), '-map', '0:a:0', '-vn', '-sn', '-dn',
            '-map_metadata', '-1', '-c:a', 'libmp3lame', '-b:a', '192k', str(output)]
    with ENCODING:
        try:
            run(args, 3600)
            data = json.loads(run(['ffprobe', '-v', 'error', '-show_format', '-show_streams',
                                   '-of', 'json', str(output)], 60))
            if not any(s['codec_type'] == 'audio' for s in data['streams']) or float(data['format'].get('duration', 0)) <= 0:
                raise ValueError('추출한 오디오 파일을 확인할 수 없습니다.')
        except Exception:
            output.unlink(missing_ok=True)
            raise
