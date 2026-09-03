"""유튜브 다운로더 - 로컬 웹 UI 서버.

브라우저에서 유튜브 링크를 입력하면 화질 목록을 조회하고,
선택한 화질(또는 mp3 오디오)로 ~/Downloads 에 저장한다.
"""

import json
import os
from datetime import datetime
import subprocess
import sys
import threading
import uuid
import webbrowser
from pathlib import Path

import yt_dlp
from flask import Flask, jsonify, render_template, request

HOST = "127.0.0.1"
PORT = 8765
DEFAULT_DOWNLOAD_DIR = Path.home() / "Downloads"
# 사용자(로컬 계정)별 설정 파일 - 저장 경로 등을 기억한다
CONFIG_FILE = Path.home() / ".youtube-downloader" / "config.json"
HISTORY_FILE = Path.home() / ".youtube-downloader" / "history.json"
HISTORY_MAX = 500

app = Flask(__name__)

# job_id -> 진행 상태 딕셔너리
jobs: dict[str, dict] = {}
jobs_lock = threading.Lock()
settings_lock = threading.Lock()
history_lock = threading.Lock()


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
