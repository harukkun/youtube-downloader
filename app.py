"""유튜브 다운로더 - 로컬 웹 UI 서버.

브라우저에서 유튜브 링크를 입력하면 화질 목록을 조회하고,
선택한 화질(또는 mp3 오디오)로 ~/Downloads 에 저장한다.
"""

import json
import os
from datetime import datetime
import shutil
import subprocess
import sys
import threading
import uuid
import webbrowser
from pathlib import Path

import yt_dlp
from flask import Flask, Response, jsonify, render_template, request, send_from_directory

HOST = "127.0.0.1"
PORT = 8765
DEFAULT_DOWNLOAD_DIR = Path.home() / "Downloads"
# 사용자(로컬 계정)별 설정 파일 - 저장 경로 등을 기억한다
CONFIG_FILE = Path.home() / ".youtube-downloader" / "config.json"
HISTORY_FILE = Path.home() / ".youtube-downloader" / "history.json"
HISTORY_MAX = 500
SHORTS_FILE = Path.home() / ".youtube-downloader" / "shorts.json"
THUMB_DIR = Path.home() / ".youtube-downloader" / "thumbnails"
THUMB_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
THUMB_MAX_BYTES = 10 * 1024 * 1024
HELPER_FILE = Path.home() / ".youtube-downloader" / "helper.json"

# 업로드 헬퍼: LLM 호출 방식/모델 (키는 저장값)
LLM_BACKENDS = {
    "cli": "Claude Code CLI (구독, claude -p)",
    "api": "Anthropic API (ANTHROPIC_API_KEY)",
}
LLM_MODELS = {
    "sonnet": {"label": "Claude Sonnet (빠름, 기본)", "cli": "sonnet", "api": "claude-sonnet-5"},
    "opus": {"label": "Claude Opus (품질 우선)", "cli": "opus", "api": "claude-opus-5"},
}
LLM_TIMEOUT_SEC = 300
LLM_MAX_INPUT_CHARS = 30000

# 레시피 설명 템플릿 기본값. {{ }} 안의 문장은 그 자리에 무엇을 채울지 LLM에게 주는 설명이다.
DEFAULT_RECIPE_TEMPLATE = """🍳 {{요리명}} 만들기

{{요리를 한두 문장으로 소개. 원본에 드러난 특징(맛, 간편함, 포인트)을 담는다}}

━━━━━━━━━━━━━━━━━━━━
🧂 재료 ({{인분. 원본에 없으면 "분량 참고" 라고만 적기}})
{{주재료를 "· 재료 분량" 형식으로 한 줄씩}}

[양념]
{{양념·소스 재료를 "· 재료 분량" 형식으로 한 줄씩. 없으면 [양념] 소제목까지 생략}}

━━━━━━━━━━━━━━━━━━━━
👩‍🍳 만드는 법
{{조리 순서를 "1. " 번호 목록으로. 한 단계는 한두 문장, 불 세기·시간·온도는 원본 그대로 유지}}

━━━━━━━━━━━━━━━━━━━━
💡 이렇게 하면 더 맛있어요
{{원본에 있는 팁·주의점만 "· " 목록으로. 없으면 이 섹션(소제목·구분선 포함) 전체 생략}}

#레시피 #{{요리명 띄어쓰기 없이}} #집밥 #요리 #kfood {{요리와 어울리는 해시태그 2~3개}}
"""
DEFAULT_RECIPE_INSTRUCTIONS = """- 존댓말(~해요 체)로 친근하게. 이모지는 템플릿에 있는 것만 사용.
- 원본의 채널명, 링크, 구독·좋아요 요청, 광고·협찬 문구, 타임스탬프는 모두 제거.
- 재료 분량 단위는 원본 그대로(큰술/작은술/g/ml). 통일할 필요 없음.
"""

RECIPE_SYSTEM_PROMPT = """당신은 요리 유튜브 채널의 영상 설명(Description) 작성 도우미입니다.
사용자가 붙여 넣은 '원본 레시피 설명'의 내용(재료, 분량, 조리 순서, 팁)을 살려서,
'채널 템플릿' 형식에 맞는 새 설명 텍스트로 다시 씁니다.

규칙:
1. 템플릿의 {{ }} 자리에는 그 안의 설명대로 내용을 채우고, {{ }} 표시 자체는 결과에 남기지 않습니다.
   템플릿의 나머지 글자(이모지, 구분선, 소제목, 고정 문구, 줄바꿈 구조)는 그대로 유지합니다.
2. 원본에 없는 사실(재료, 분량, 시간, 온도, 인분)은 만들어 넣지 않습니다.
   원본에 없어서 채울 수 없는 항목은 비워두거나 템플릿 지시대로 처리하고, notes에 그 사실을 적습니다.
3. 문장은 원본을 그대로 복사하지 말고 자연스럽게 다시 씁니다. 사실 정보(재료명, 분량, 순서)는 정확히 유지합니다.
4. 원본이 레시피가 아니거나 재료·조리 정보가 거의 없으면, 있는 정보만으로 작성하고 notes에 그 점을 적습니다.
5. 결과는 한국어, 유튜브 설명란 한도인 5000자 이내.
6. description에는 완성된 설명 텍스트만 넣습니다(머리말·설명·코드블록 없이).
   notes에는 사용자가 업로드 전에 확인해야 할 점을 짧은 한국어 문장 배열로 넣습니다(없으면 빈 배열).
"""
RECIPE_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "description": {"type": "string", "description": "완성된 유튜브 설명 텍스트"},
        "notes": {"type": "array", "items": {"type": "string"}, "description": "업로드 전 확인할 점"},
    },
    "required": ["description", "notes"],
    "additionalProperties": False,
}

# 쇼츠 현황판 상태/플랫폼 정의 (키는 저장값, 값은 화면 라벨)
STATUSES = {
    "before": "제작 전",
    "making": "제작 중",
    "ready": "제작 완료·업로드 대기",
    "uploaded": "업로드 완료",
}
PLATFORMS = {
    "youtube": "유튜브",
    "instagram": "인스타그램",
    "tiktok": "틱톡",
    "naver_clip": "네이버 클립",
}

app = Flask(__name__)

# job_id -> 진행 상태 딕셔너리
jobs: dict[str, dict] = {}
jobs_lock = threading.Lock()
settings_lock = threading.Lock()
history_lock = threading.Lock()
shorts_lock = threading.Lock()
helper_lock = threading.Lock()


# ---------------------------------------------------------------------------
# 다운로드 내역 (사용자별 history.json)
# ---------------------------------------------------------------------------
def _write_json_atomic(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(path)


def load_history() -> list[dict]:
    try:
        with open(HISTORY_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []


def history_add(record: dict) -> None:
    with history_lock:
        items = load_history()
        items.insert(0, record)  # 최신이 앞
        _write_json_atomic(HISTORY_FILE, items[:HISTORY_MAX])


def history_update(record_id: str, **fields) -> None:
    with history_lock:
        items = load_history()
        for it in items:
            if it.get("id") == record_id:
                it.update({k: v for k, v in fields.items() if v is not None})
                break
        else:
            return
        _write_json_atomic(HISTORY_FILE, items)


def history_remove(record_id: str | None) -> int:
    """record_id가 None이면 전체 삭제. 삭제된 개수 반환."""
    with history_lock:
        items = load_history()
        kept = [] if record_id is None else [it for it in items if it.get("id") != record_id]
        _write_json_atomic(HISTORY_FILE, kept)
        return len(items) - len(kept)


def history_with_file_state() -> list[dict]:
    """각 기록에 파일 존재 여부와 현재 크기를 붙여 돌려준다."""
    out = []
    for it in load_history():
        item = dict(it)
        fp = item.get("filepath")
        exists = bool(fp) and os.path.isfile(fp)
        item["exists"] = exists
        item["size"] = os.path.getsize(fp) if exists else None
        out.append(item)
    return out


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# 쇼츠 현황판 (사용자별 shorts.json + thumbnails/)
# ---------------------------------------------------------------------------
def load_shorts() -> list[dict]:
    try:
        with open(SHORTS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []


def save_shorts(items: list[dict]) -> None:
    _write_json_atomic(SHORTS_FILE, items)


def _s(v, limit: int = 20000) -> str:
    """문자열 정리: None → '', 공백 제거, 길이 제한."""
    if v is None:
        return ""
    return str(v).strip()[:limit]


def _empty_platforms() -> dict:
    return {k: {"checked": False, "url": ""} for k in PLATFORMS}


def normalize_item(data: dict, base: dict | None = None) -> dict:
    """요청 데이터를 검증해 항목으로 만든다. base가 있으면 그 위에 덮어쓴다(부분 수정)."""
    item = json.loads(json.dumps(base)) if base else {}

    if "dish_title" in data:
        item["dish_title"] = _s(data.get("dish_title"), 200)
    if "source" in data:
        src = data.get("source") or {}
        item["source"] = {
            "url": _s(src.get("url"), 2000),
            "title": _s(src.get("title"), 500),
            "channel": _s(src.get("channel"), 200),
            "thumbnail": _s(src.get("thumbnail"), 2000),
        }
    if "reference_shorts" in data:
        refs = []
        for r in data.get("reference_shorts") or []:
            if not isinstance(r, dict):
                continue
            ch, url = _s(r.get("channel"), 200), _s(r.get("url"), 2000)
            if ch or url:
                refs.append({"channel": ch, "url": url})
        item["reference_shorts"] = refs
    if "status" in data:
        st = data.get("status")
        if st not in STATUSES:
            raise ValueError(f"알 수 없는 상태값입니다: {st!r}")
        item["status"] = st
    if "platforms" in data:
        raw = data.get("platforms") or {}
        plats = _empty_platforms()
        if isinstance(raw, list):  # ["youtube", ...] 형태도 허용
            for k in raw:
                if k in plats:
                    plats[k]["checked"] = True
        elif isinstance(raw, dict):
            for k in plats:
                pv = raw.get(k) or {}
                if isinstance(pv, bool):
                    pv = {"checked": pv}
                plats[k] = {"checked": bool(pv.get("checked")), "url": _s(pv.get("url"), 2000)}
        item["platforms"] = plats
    if "video" in data:
        v = data.get("video") or {}
        prev_thumb = (item.get("video") or {}).get("thumbnail", "")
        item["video"] = {
            "title": _s(v.get("title"), 500),
            "description": _s(v.get("description"), 20000),
            "pinned_comment": _s(v.get("pinned_comment"), 20000),
            "thumbnail": prev_thumb,  # 썸네일은 업로드 API로만 변경
        }
    if "memo" in data:
        item["memo"] = _s(data.get("memo"), 5000)

    # 기본값 채우기
    item.setdefault("dish_title", "")
    item.setdefault("source", {"url": "", "title": "", "channel": "", "thumbnail": ""})
    item.setdefault("reference_shorts", [])
    item.setdefault("status", "before")
    item.setdefault("platforms", _empty_platforms())
    item.setdefault("video", {"title": "", "description": "", "pinned_comment": "", "thumbnail": ""})
    item.setdefault("memo", "")
    return item


def _find_item(items: list[dict], item_id: str) -> dict | None:
    return next((it for it in items if it.get("id") == item_id), None)


def _remove_thumb_files(item_id: str) -> None:
    if not THUMB_DIR.exists():
        return
    for f in THUMB_DIR.glob(f"{item_id}.*"):
        try:
            f.unlink()
        except OSError:
            pass


def fetch_video_meta(url: str) -> dict:
    """원본 영상/참고 쇼츠 링크의 제목·채널·썸네일을 가져온다."""
    with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "noplaylist": True}) as ydl:
        info = ydl.extract_info(url, download=False)
    return {
        "title": info.get("title") or "",
        "channel": info.get("uploader") or info.get("channel") or "",
        "thumbnail": info.get("thumbnail") or "",
        "webpage_url": info.get("webpage_url") or url,
        "duration": info.get("duration"),
    }


# ---------------------------------------------------------------------------
# 설정 (저장 경로)
# ---------------------------------------------------------------------------
def load_settings() -> dict:
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def save_settings(data: dict) -> None:
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_FILE.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(CONFIG_FILE)


def get_download_dir() -> Path:
    with settings_lock:
        raw = load_settings().get("download_dir")
    if raw:
        p = Path(raw).expanduser()
        if p.is_absolute():
            return p
    return DEFAULT_DOWNLOAD_DIR


def set_download_dir(raw: str) -> Path:
    """경로를 검증하고(없으면 생성) 설정 파일에 저장한다. 문제가 있으면 ValueError."""
    raw = (raw or "").strip()
    if not raw:
        raise ValueError("경로를 입력해 주세요.")
    p = Path(raw).expanduser()
    if not p.is_absolute():
        raise ValueError("절대 경로를 입력해 주세요. (예: /Users/이름/Movies)")
    if p.exists() and not p.is_dir():
        raise ValueError("해당 경로는 폴더가 아닙니다.")
    try:
        p.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise ValueError(f"폴더를 만들 수 없습니다: {e.strerror or e}") from e
    if not os.access(p, os.W_OK):
        raise ValueError("해당 폴더에 쓰기 권한이 없습니다.")
    with settings_lock:
        data = load_settings()
        data["download_dir"] = str(p)
        save_settings(data)
    return p


def choose_folder_dialog(initial: Path) -> Path | None:
    """OS 기본 폴더 선택 창을 띄운다. 취소하면 None. (macOS: osascript)"""
    if sys.platform == "darwin":
        script = (
            'tell application "System Events" to activate\n'
            f'set f to choose folder with prompt "다운로드 저장 폴더를 선택하세요" '
            f'default location POSIX file "{initial}"\n'
            "POSIX path of f"
        )
        r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
        if r.returncode != 0:  # 사용자가 취소함(-128) 등
            return None
        return Path(r.stdout.strip())
    # 다른 OS: tkinter 폴더 선택 창 (가능한 경우)
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        chosen = filedialog.askdirectory(initialdir=str(initial), title="다운로드 저장 폴더 선택")
        root.destroy()
        return Path(chosen) if chosen else None
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# 유틸
# ---------------------------------------------------------------------------
QUALITY_TAGS = {
    4320: "8K",
    2160: "4K",
    1440: "QHD",
    1080: "FHD",
    720: "HD",
    480: "SD",
}


def _clean_error(err: Exception) -> str:
    msg = str(err)
    for prefix in ("ERROR: ", "[youtube] "):
        if msg.startswith(prefix):
            msg = msg[len(prefix):]
    # "[youtube] xxxx: message" 형태에서 앵커 제거
    if "] " in msg and ": " in msg:
        head, _, tail = msg.partition(": ")
        if head.startswith("["):
            msg = tail or msg
    return msg.strip()


def _human_size(num: float | None) -> str | None:
    if not num:
        return None
    for unit in ("B", "KB", "MB", "GB"):
        if num < 1024:
            return f"{num:.0f}{unit}" if unit == "B" else f"{num:.1f}{unit}"
        num /= 1024
    return f"{num:.1f}TB"


def _fmt_size(f: dict) -> float | None:
    return f.get("filesize") or f.get("filesize_approx")


def build_quality_options(info: dict) -> list[dict]:
    """포맷 목록에서 해상도별 선택지를 만든다."""
    formats = info.get("formats") or []

    # 최고 음질 오디오 용량 (영상 용량 추정에 합산)
    audio_only = [f for f in formats if f.get("vcodec") == "none" and f.get("acodec") != "none"]
    best_audio_size = 0.0
    if audio_only:
        best_audio = max(audio_only, key=lambda f: f.get("abr") or f.get("tbr") or 0)
        best_audio_size = _fmt_size(best_audio) or 0.0

    by_height: dict[int, dict] = {}
    for f in formats:
        if f.get("vcodec") == "none" or not f.get("height"):
            continue
        h = int(f["height"])
        fps = int(f.get("fps") or 0)
        size = _fmt_size(f) or 0.0
        is_h264 = str(f.get("vcodec") or "").startswith("avc1")
        cur = by_height.get(h)
        # 같은 해상도면 fps 높은 것, 그다음 tbr 높은 것을 대표로
        key = (fps, f.get("tbr") or 0)
        if cur is None or key > cur["_key"]:
            by_height[h] = {
                "_key": key,
                "fps": fps,
                "size": size,
                "has_audio": f.get("acodec") not in (None, "none"),
                "has_h264": is_h264 or bool(cur and cur["has_h264"]),
            }
        elif is_h264:
            cur["has_h264"] = True

    options = []
    for h in sorted(by_height, reverse=True):
        d = by_height[h]
        label = f"{h}p"
        if d["fps"] > 30:
            label += f"{d['fps']}"
        tag = QUALITY_TAGS.get(h)
        if tag:
            label += f" ({tag})"
        # 영상 용량을 모르면 표시하지 않음 (오디오 용량만 보여주면 오해 소지)
        if d["size"]:
            total = d["size"] + (0 if d["has_audio"] else best_audio_size)
            label += f" · 약 {_human_size(total)}"
        if not d["has_h264"]:
            label += " · ⚠ VP9/AV1 (QuickTime 재생 불가)"
        options.append({"id": str(h), "label": label, "kind": "video", "h264": d["has_h264"]})

    options.append({
        "id": "audio",
        "label": "오디오만 (mp3, 192kbps)"
        + (f" · 약 {_human_size(best_audio_size)}" if best_audio_size else ""),
        "kind": "audio",
    })
    return options


def build_ydl_opts(quality: str, download_dir: Path, job: dict | None = None) -> dict:
    opts: dict = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "outtmpl": str(download_dir / "%(title)s.%(ext)s"),
        "windowsfilenames": False,
        "overwrites": True,
    }
    if quality == "audio":
        opts["format"] = "bestaudio/best"
        opts["postprocessors"] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }]
    else:
        h = int(quality)
        lo = int(h * 0.9)  # 선택한 해상도와 "같은 급"으로 볼 하한 (1080 → 972, 720 → 648)
        # 1) 선택한 해상도의 H.264(avc1) 영상을 최우선으로 고른다. VP9/AV1은 mp4
        #    컨테이너라도 QuickTime Player 등 Apple 기본 앱에서 재생되지 않기 때문.
        #    하한(lo)을 두는 이유: 4K를 골랐는데 1080p H.264로 조용히 내려가지 않도록.
        # 2) 그 해상도에 H.264가 없으면(주로 4K) 같은 해상도의 VP9/AV1로 넘어간다.
        opts["format"] = (
            f"bestvideo[height<={h}][height>{lo}][vcodec^=avc1]+bestaudio[ext=m4a]"
            f"/bestvideo[height<={h}][ext=mp4]+bestaudio[ext=m4a]"
            f"/bestvideo[height<={h}]+bestaudio"
            f"/best[height<={h}]/best"
        )
        opts["merge_output_format"] = "mp4"

    if job is not None:
        opts["progress_hooks"] = [lambda d: _on_progress(job, d)]
        opts["postprocessor_hooks"] = [lambda d: _on_postprocess(job, d)]
    return opts


# ---------------------------------------------------------------------------
# 진행 상태 훅
# ---------------------------------------------------------------------------
def _on_progress(job: dict, d: dict) -> None:
    status = d.get("status")
    if status == "downloading":
        downloaded = d.get("downloaded_bytes") or 0
        total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
        expected = job.get("expected_total") or 0
        done_before = job.get("bytes_done_prev") or 0

        if expected:
            percent = (done_before + downloaded) / expected * 100
        elif total:
            percent = downloaded / total * 100
        else:
            percent = 0
        with jobs_lock:
            job.update({
                "status": "downloading",
                "percent": round(min(percent, 99.9), 1),
                "speed": d.get("speed"),
                "eta": d.get("eta"),
                "downloaded": done_before + downloaded,
                "total": expected or total,
            })
    elif status == "finished":
        total = d.get("total_bytes") or d.get("downloaded_bytes") or 0
        with jobs_lock:
            job["bytes_done_prev"] = (job.get("bytes_done_prev") or 0) + total
            job["stream_index"] = (job.get("stream_index") or 0) + 1
            job["filename"] = d.get("filename")
            if job["stream_index"] >= (job.get("stream_count") or 1):
                job["status"] = "processing"
                job["percent"] = 100
                job["speed"] = None
                job["eta"] = None


def _on_postprocess(job: dict, d: dict) -> None:
    info = d.get("info_dict") or {}
    with jobs_lock:
        if d.get("status") == "started":
            job["status"] = "processing"
            job["postprocessor"] = d.get("postprocessor")
        if d.get("status") == "finished" and info.get("filepath"):
            job["filepath"] = info["filepath"]


# ---------------------------------------------------------------------------
# 다운로드 워커
# ---------------------------------------------------------------------------
def run_download(job_id: str, url: str, quality: str) -> None:
    job = jobs[job_id]
    try:
        download_dir = get_download_dir()
        download_dir.mkdir(parents=True, exist_ok=True)
        with jobs_lock:
            job["download_dir"] = str(download_dir)
        opts = build_ydl_opts(quality, download_dir, job)
        with yt_dlp.YoutubeDL(opts) as ydl:
            # 1) 선택된 포맷 확인 → 총 용량/스트림 수 추정 (전체 진행률 계산용)
            info = ydl.extract_info(url, download=False)
            requested = info.get("requested_formats") or [info]
            expected = sum((_fmt_size(f) or 0) for f in requested)
            with jobs_lock:
                job["stream_count"] = len(requested)
                job["expected_total"] = expected if all(_fmt_size(f) for f in requested) else 0
                job["title"] = info.get("title")
                job["status"] = "downloading"
            history_update(job_id, title=info.get("title"), thumbnail=info.get("thumbnail"),
                           duration=info.get("duration"), uploader=info.get("uploader") or info.get("channel"))

            # 2) 실제 다운로드
            ydl.process_ie_result(info, download=True)

            final = job.get("filepath")
            if not final:
                # 후처리가 없었으면(단일 파일) 준비된 파일명 사용
                final = info.get("filepath") or ydl.prepare_filename(info)
        with jobs_lock:
            job.update({
                "status": "finished",
                "percent": 100,
                "filepath": final,
                "filename": os.path.basename(final) if final else None,
                "speed": None,
                "eta": None,
            })
        history_update(
            job_id, status="finished", filepath=final,
            filename=os.path.basename(final) if final else None,
            download_dir=str(download_dir), finished_at=_now(),
            filesize=os.path.getsize(final) if final and os.path.isfile(final) else None,
        )
    except Exception as e:  # noqa: BLE001 - 사용자에게 그대로 보여줌
        msg = _clean_error(e)
        with jobs_lock:
            job.update({"status": "error", "message": msg})
        history_update(job_id, status="error", message=msg, finished_at=_now())


# ---------------------------------------------------------------------------
# 라우트
# ---------------------------------------------------------------------------
@app.get("/favicon.ico")
def favicon():
    """브라우저가 기본으로 요청하는 /favicon.ico → SVG 파비콘으로 응답."""
    return send_from_directory(app.static_folder, "favicon.svg", mimetype="image/svg+xml", max_age=86400)


@app.get("/")
def index():
    return render_template("index.html", download_dir=str(get_download_dir()))


@app.post("/api/info")
def api_info():
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    if not url:
        return jsonify({"error": "URL을 입력해 주세요."}), 400
    try:
        with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "noplaylist": True}) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": _clean_error(e)}), 400

    return jsonify({
        "title": info.get("title"),
        "thumbnail": info.get("thumbnail"),
        "duration": info.get("duration"),
        "uploader": info.get("uploader") or info.get("channel"),
        "webpage_url": info.get("webpage_url") or url,
        "qualities": build_quality_options(info),
    })


@app.post("/api/download")
def api_download():
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    quality = str(data.get("quality") or "").strip()
    if not url:
        return jsonify({"error": "URL을 입력해 주세요."}), 400
    if quality != "audio" and not quality.isdigit():
        return jsonify({"error": "화질 선택이 올바르지 않습니다."}), 400

    job_id = uuid.uuid4().hex[:12]
    with jobs_lock:
        jobs[job_id] = {
            "id": job_id,
            "status": "starting",
            "percent": 0,
            "quality": quality,
            "url": url,
        }
    history_add({
        "id": job_id,
        "url": url,
        "title": data.get("title"),
        "thumbnail": data.get("thumbnail"),
        "quality": quality,
        "quality_label": data.get("quality_label") or ("오디오만 (mp3)" if quality == "audio" else f"{quality}p"),
        "status": "downloading",
        "started_at": _now(),
        "download_dir": str(get_download_dir()),
    })
    threading.Thread(target=run_download, args=(job_id, url, quality), daemon=True).start()
    return jsonify({"job_id": job_id})


@app.get("/api/progress/<job_id>")
def api_progress(job_id: str):
    with jobs_lock:
        job = jobs.get(job_id)
        if job is None:
            return jsonify({"error": "작업을 찾을 수 없습니다."}), 404
        public = {k: v for k, v in job.items() if not k.startswith("_")}
    return jsonify(public)


def reveal_in_file_manager(path: Path | None) -> None:
    """파일이 있으면 해당 파일을 선택한 상태로, 없으면 다운로드 폴더를 연다."""
    if sys.platform == "darwin":
        if path and path.exists():
            subprocess.Popen(["open", "-R", str(path)])
        else:
            subprocess.Popen(["open", str(get_download_dir())])
    elif sys.platform.startswith("win"):
        if path and path.exists():
            subprocess.Popen(["explorer", "/select,", str(path)])
        else:
            subprocess.Popen(["explorer", str(get_download_dir())])
    else:
        subprocess.Popen(["xdg-open", str(path.parent if path and path.exists() else get_download_dir())])


@app.post("/api/open-folder")
def api_open_folder():
    data = request.get_json(silent=True) or {}
    job_id = data.get("job_id")
    history_id = data.get("history_id")
    path = None
    with jobs_lock:
        job = jobs.get(job_id) if job_id else None
        if job and job.get("filepath"):
            path = Path(job["filepath"])
    if path is None and history_id:
        rec = next((it for it in load_history() if it.get("id") == history_id), None)
        if rec and rec.get("filepath"):
            path = Path(rec["filepath"])
    try:
        reveal_in_file_manager(path)
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": f"폴더를 열 수 없습니다: {e}"}), 500
    return jsonify({"ok": True, "opened": str(path) if path and path.exists() else str(get_download_dir())})


@app.get("/api/history")
def api_history():
    return jsonify({"items": history_with_file_state()})


@app.delete("/api/history/<record_id>")
def api_history_delete(record_id: str):
    return jsonify({"removed": history_remove(record_id)})


@app.delete("/api/history")
def api_history_clear():
    return jsonify({"removed": history_remove(None)})


# ---- 쇼츠 현황판 ------------------------------------------------------------
@app.get("/shorts")
def shorts_page():
    return render_template("shorts.html", statuses=STATUSES, platforms=PLATFORMS)


@app.get("/api/shorts")
def api_shorts_list():
    return jsonify({"items": load_shorts(), "statuses": STATUSES, "platforms": PLATFORMS})


@app.post("/api/shorts")
def api_shorts_create():
    data = request.get_json(silent=True) or {}
    try:
        item = normalize_item(data)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    item["id"] = uuid.uuid4().hex[:12]
    item["created_at"] = item["updated_at"] = _now()
    with shorts_lock:
        items = load_shorts()
        items.insert(0, item)
        save_shorts(items)
    return jsonify(item), 201


@app.put("/api/shorts/<item_id>")
def api_shorts_update(item_id: str):
    data = request.get_json(silent=True) or {}
    with shorts_lock:
        items = load_shorts()
        cur = _find_item(items, item_id)
        if cur is None:
            return jsonify({"error": "항목을 찾을 수 없습니다."}), 404
        try:
            new = normalize_item(data, cur)
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        new["updated_at"] = _now()
        items[items.index(cur)] = new
        save_shorts(items)
    return jsonify(new)


@app.delete("/api/shorts/<item_id>")
def api_shorts_delete(item_id: str):
    with shorts_lock:
        items = load_shorts()
        kept = [it for it in items if it.get("id") != item_id]
        if len(kept) == len(items):
            return jsonify({"error": "항목을 찾을 수 없습니다."}), 404
        save_shorts(kept)
    _remove_thumb_files(item_id)
    return jsonify({"ok": True})


@app.post("/api/shorts/<item_id>/thumbnail")
def api_shorts_thumbnail(item_id: str):
    f = request.files.get("file")
    if f is None or not f.filename:
        return jsonify({"error": "이미지 파일을 선택해 주세요."}), 400
    ext = os.path.splitext(f.filename)[1].lower()
    if ext == ".jpeg":
        ext = ".jpg"
    if ext not in THUMB_EXTS:
        return jsonify({"error": "jpg, png, webp 이미지만 업로드할 수 있습니다."}), 400
    blob = f.read(THUMB_MAX_BYTES + 1)
    if len(blob) > THUMB_MAX_BYTES:
        return jsonify({"error": "이미지는 10MB 이하만 가능합니다."}), 400
    with shorts_lock:
        items = load_shorts()
        cur = _find_item(items, item_id)
        if cur is None:
            return jsonify({"error": "항목을 찾을 수 없습니다."}), 404
        THUMB_DIR.mkdir(parents=True, exist_ok=True)
        _remove_thumb_files(item_id)
        name = f"{item_id}{ext}"
        (THUMB_DIR / name).write_bytes(blob)
        cur.setdefault("video", {})["thumbnail"] = name
        cur["updated_at"] = _now()
        save_shorts(items)
    return jsonify({"thumbnail": name, "url": f"/thumbnails/{name}?v={int(datetime.now().timestamp())}"})


@app.delete("/api/shorts/<item_id>/thumbnail")
def api_shorts_thumbnail_delete(item_id: str):
    with shorts_lock:
        items = load_shorts()
        cur = _find_item(items, item_id)
        if cur is None:
            return jsonify({"error": "항목을 찾을 수 없습니다."}), 404
        cur.setdefault("video", {})["thumbnail"] = ""
        cur["updated_at"] = _now()
        save_shorts(items)
    _remove_thumb_files(item_id)
    return jsonify({"ok": True})


@app.get("/thumbnails/<path:name>")
def thumbnails(name: str):
    return send_from_directory(THUMB_DIR, name, max_age=0)


@app.post("/api/shorts/lookup")
def api_shorts_lookup():
    data = request.get_json(silent=True) or {}
    url = _s(data.get("url"), 2000)
    if not url:
        return jsonify({"error": "URL을 입력해 주세요."}), 400
    try:
        return jsonify(fetch_video_meta(url))
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": _clean_error(e)}), 400


@app.get("/api/shorts/export")
def api_shorts_export():
    payload = {"version": 1, "exported_at": _now(), "items": load_shorts()}
    body = json.dumps(payload, ensure_ascii=False, indent=2)
    fname = f"shorts-{datetime.now().strftime('%Y%m%d-%H%M')}.json"
    return Response(body, mimetype="application/json",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{fname}"})


@app.post("/api/shorts/import")
def api_shorts_import():
    data = request.get_json(silent=True) or {}
    raw_items = data.get("items")
    if isinstance(raw_items, dict):  # export 파일 전체를 그대로 보낸 경우
        raw_items = raw_items.get("items")
    if not isinstance(raw_items, list):
        return jsonify({"error": "items 배열이 필요합니다."}), 400
    mode = data.get("mode", "merge")
    if mode not in ("merge", "replace"):
        return jsonify({"error": "mode는 merge 또는 replace 여야 합니다."}), 400

    incoming: list[dict] = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        try:
            it = normalize_item(raw)
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        it["id"] = _s(raw.get("id"), 64) or uuid.uuid4().hex[:12]
        it["created_at"] = _s(raw.get("created_at"), 40) or _now()
        it["updated_at"] = _s(raw.get("updated_at"), 40) or _now()
        # 썸네일 파일이 실제로 있을 때만 유지
        th = _s((raw.get("video") or {}).get("thumbnail"), 200)
        it["video"]["thumbnail"] = th if th and (THUMB_DIR / th).is_file() else ""
        incoming.append(it)

    with shorts_lock:
        if mode == "replace":
            result, added, updated = incoming, len(incoming), 0
        else:
            result = load_shorts()
            by_id = {it["id"]: i for i, it in enumerate(result)}
            added = updated = 0
            for it in incoming:
                if it["id"] in by_id:
                    result[by_id[it["id"]]] = it
                    updated += 1
                else:
                    result.insert(0, it)
                    added += 1
        save_shorts(result)
    return jsonify({"ok": True, "added": added, "updated": updated, "total": len(result)})


# ---- 유튜브 업로드 헬퍼 ------------------------------------------------------
def load_helper() -> dict:
    try:
        with open(HELPER_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def recipe_defaults() -> dict:
    return {
        "template": DEFAULT_RECIPE_TEMPLATE,
        "instructions": DEFAULT_RECIPE_INSTRUCTIONS,
        "model": "sonnet",
        "backend": "cli",
    }


def normalize_recipe_settings(data: dict, base: dict | None = None) -> dict:
    """레시피 설명 도구 설정을 검증한다. base 위에 보낸 필드만 덮어쓴다."""
    cur = dict(base or recipe_defaults())
    if "template" in data:
        cur["template"] = _s(data.get("template"), LLM_MAX_INPUT_CHARS)
    if "instructions" in data:
        cur["instructions"] = _s(data.get("instructions"), 5000)
    if "model" in data:
        m = data.get("model")
        if m not in LLM_MODELS:
            raise ValueError(f"알 수 없는 모델입니다: {m!r}")
        cur["model"] = m
    if "backend" in data:
        b = data.get("backend")
        if b not in LLM_BACKENDS:
            raise ValueError(f"알 수 없는 호출 방식입니다: {b!r}")
        cur["backend"] = b
    return cur


def get_recipe_settings() -> dict:
    saved = load_helper().get("recipe") or {}
    try:
        return normalize_recipe_settings(saved)
    except ValueError:
        return recipe_defaults()


def llm_environment() -> dict:
    return {
        "cli_available": shutil.which("claude") is not None,
        "api_key_set": bool(os.environ.get("ANTHROPIC_API_KEY")),
    }


def _llm_via_cli(system: str, user: str, schema: dict, model: str) -> dict:
    """로컬 Claude Code CLI(구독)를 헤드리스로 실행해 JSON 결과를 받는다."""
    exe = shutil.which("claude")
    if exe is None:
        raise RuntimeError("Claude Code CLI(claude)를 찾을 수 없습니다. 설치하거나 호출 방식을 API로 바꿔 주세요.")
    cmd = [
        exe, "-p",
        "--output-format", "json",
        "--json-schema", json.dumps(schema, ensure_ascii=False),
        "--system-prompt", system,
        "--model", model,
        "--max-turns", "4",
        "--no-session-persistence",
        "--tools", "",
        "--permission-mode", "dontAsk",
    ]  # 프롬프트는 stdin으로: 가변 인자 옵션(--tools)이 뒤따르는 인자를 삼키지 않도록
    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}  # Claude Code 안에서 실행해도 중첩 허용
    try:
        proc = subprocess.run(cmd, input=user, capture_output=True, text=True, timeout=LLM_TIMEOUT_SEC, env=env)
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"Claude 응답이 {LLM_TIMEOUT_SEC}초 안에 오지 않았습니다.") from e
    if proc.returncode != 0:
        raise RuntimeError(f"claude -p 실패 ({proc.returncode}): {(proc.stderr or proc.stdout)[-800:].strip()}")
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"claude -p 출력이 JSON이 아닙니다: {proc.stdout[:300]}") from e
    if data.get("is_error"):
        raise RuntimeError(f"claude -p 오류: {str(data.get('result'))[:500]}")
    payload = data.get("structured_output")
    if not isinstance(payload, dict):
        raise RuntimeError(f"구조화된 결과가 없습니다 ({data.get('subtype')}): {str(data.get('result'))[:300]}")
    payload["_usage"] = {"cost_usd": data.get("total_cost_usd"), "duration_ms": data.get("duration_ms"), "backend": "cli", "model": model}
    return payload


def _llm_via_api(system: str, user: str, schema: dict, model: str) -> dict:
    """Anthropic API(구조화 출력)로 JSON 결과를 받는다. anthropic 패키지가 필요하다."""
    try:
        import anthropic
    except ImportError as e:
        raise RuntimeError("anthropic 패키지가 없습니다: .venv/bin/pip install anthropic") from e
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY 환경 변수가 설정되어 있지 않습니다.")
    client = anthropic.Anthropic(timeout=LLM_TIMEOUT_SEC)
    try:
        resp = client.messages.create(
            model=model,
            max_tokens=16000,
            system=system,
            messages=[{"role": "user", "content": user}],
            output_config={"format": {"type": "json_schema", "schema": schema}},
        )
    except anthropic.APIStatusError as e:
        raise RuntimeError(f"API 오류 ({e.status_code}): {e.message}") from e
    except anthropic.APIConnectionError as e:
        raise RuntimeError(f"API 연결 실패: {e}") from e
    if resp.stop_reason == "refusal":
        raise RuntimeError("모델이 요청을 거절했습니다.")
    text = next((b.text for b in resp.content if b.type == "text"), "")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"API 응답이 JSON이 아닙니다: {text[:300]}") from e
    payload["_usage"] = {"input_tokens": resp.usage.input_tokens, "output_tokens": resp.usage.output_tokens,
                         "backend": "api", "model": model}
    return payload


def llm_structured(system: str, user: str, schema: dict, backend: str, model_key: str) -> dict:
    spec = LLM_MODELS[model_key]
    if backend == "api":
        return _llm_via_api(system, user, schema, spec["api"])
    return _llm_via_cli(system, user, schema, spec["cli"])


def build_recipe_user_prompt(template: str, instructions: str, source_text: str) -> str:
    parts = ["[채널 템플릿]", template.strip(), ""]
    if instructions.strip():
        parts += ["[추가 지시]", instructions.strip(), ""]
    parts += ["[원본 레시피 설명]", source_text.strip()]
    return "\n".join(parts)


@app.get("/helper")
def helper_page():
    return render_template("helper.html", models=LLM_MODELS, backends=LLM_BACKENDS)


@app.get("/api/helper/settings")
def api_helper_settings():
    return jsonify({
        "recipe": get_recipe_settings(),
        "recipe_defaults": recipe_defaults(),
        "models": LLM_MODELS,
        "backends": LLM_BACKENDS,
        "env": llm_environment(),
    })


@app.put("/api/helper/settings")
def api_helper_settings_save():
    data = request.get_json(silent=True) or {}
    with helper_lock:
        store = load_helper()
        if "recipe" in data:
            try:
                store["recipe"] = normalize_recipe_settings(data.get("recipe") or {}, get_recipe_settings())
            except ValueError as e:
                return jsonify({"error": str(e)}), 400
        _write_json_atomic(HELPER_FILE, store)
    return jsonify({"ok": True, "recipe": get_recipe_settings()})


@app.post("/api/helper/recipe-description")
def api_helper_recipe_description():
    """원본 레시피 설명을 내 채널 템플릿 형식으로 변형한다. 보낸 설정은 저장하지 않는다."""
    data = request.get_json(silent=True) or {}
    source = _s(data.get("source_text"), LLM_MAX_INPUT_CHARS + 1)
    if not source:
        return jsonify({"error": "원본 레시피 설명을 붙여 넣어 주세요."}), 400
    if len(source) > LLM_MAX_INPUT_CHARS:
        return jsonify({"error": f"원본 텍스트는 {LLM_MAX_INPUT_CHARS:,}자 이하만 가능합니다."}), 400
    try:
        cfg = normalize_recipe_settings(data, get_recipe_settings())
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    if not cfg["template"].strip():
        return jsonify({"error": "채널 템플릿이 비어 있습니다."}), 400

    user_prompt = build_recipe_user_prompt(cfg["template"], cfg["instructions"], source)
    try:
        result = llm_structured(RECIPE_SYSTEM_PROMPT, user_prompt, RECIPE_OUTPUT_SCHEMA, cfg["backend"], cfg["model"])
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 502
    description = _s(result.get("description"), 20000)
    notes = [_s(n, 500) for n in (result.get("notes") or []) if isinstance(n, str) and _s(n)]
    return jsonify({"description": description, "notes": notes, "usage": result.get("_usage")})


@app.get("/api/settings")
def api_get_settings():
    return jsonify({"download_dir": str(get_download_dir()), "default_dir": str(DEFAULT_DOWNLOAD_DIR)})


@app.post("/api/settings")
def api_set_settings():
    data = request.get_json(silent=True) or {}
    try:
        p = set_download_dir(data.get("download_dir", ""))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"ok": True, "download_dir": str(p)})


@app.post("/api/choose-folder")
def api_choose_folder():
    chosen = choose_folder_dialog(get_download_dir())
    if chosen is None:
        return jsonify({"cancelled": True})
    try:
        p = set_download_dir(str(chosen))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"ok": True, "download_dir": str(p)})


def _open_browser() -> None:
    webbrowser.open(f"http://{HOST}:{PORT}")


if __name__ == "__main__":
    import sys

    print(f"* 유튜브 다운로더: http://{HOST}:{PORT}  (저장 위치: {get_download_dir()})", flush=True)
    no_browser = "--no-browser" in sys.argv or os.environ.get("NO_BROWSER") == "1"
    if not no_browser:
        threading.Timer(1.0, _open_browser).start()
    app.run(host=HOST, port=PORT, debug=False, threaded=True)
