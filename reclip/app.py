import os
import platform
import queue
import re
import shutil
import subprocess
import threading
import time
from collections.abc import Generator

from flask import Flask, Response, jsonify, render_template, request, send_file

from reclip.downloader import (
    build_ytdlp_cli_command,
    get_media_info,
    get_playlist_info,
)
from reclip.jobs import job_manager
from reclip.utils import (
    DELETE_AFTER_DOWNLOAD,
    DOWNLOAD_DIR,
    DOWNLOAD_TTL,
    MAX_PLAYLIST_ITEMS,
    MIN_FREE_DISK_SPACE_MB,
    cleanup_downloads,
    friendly_error_message,
    is_safe_url,
    metadata_cache,
)

package_dir = os.path.abspath(os.path.dirname(__file__))
app = Flask(
    __name__,
    template_folder=os.path.join(package_dir, "templates"),
    static_folder=os.path.join(package_dir, "static"),
)
app.config["JSON_AS_ASCII"] = False

# Periodic background cleaner
_last_cleanup_time = 0.0
_cleanup_lock = threading.Lock()


def maybe_trigger_cleanup() -> None:
    """Run cleanup of stale temporary files at most once every 10 minutes."""
    global _last_cleanup_time
    now = time.time()
    if now - _last_cleanup_time < 600:
        return
    with _cleanup_lock:
        if now - _last_cleanup_time < 600:
            return
        _last_cleanup_time = now
        active_files = job_manager.get_active_file_paths()
        threading.Thread(
            target=cleanup_downloads,
            args=(DOWNLOAD_DIR, DOWNLOAD_TTL, active_files),
            daemon=True,
        ).start()


@app.before_request
def before_request_hook() -> None:
    maybe_trigger_cleanup()


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/health")
def health_check():
    """Machine-readable healthcheck endpoint for Docker and Coolify."""
    yt_dlp_ok = False
    yt_dlp_version = "unknown"
    try:
        proc = subprocess.run(
            ["yt-dlp", "--version"],
            capture_output=True,
            text=True,
            timeout=5,
            shell=False,
        )
        if proc.returncode == 0:
            yt_dlp_ok = True
            yt_dlp_version = proc.stdout.strip()
    except Exception:
        pass

    ffmpeg_ok = False
    try:
        proc = subprocess.run(
            ["ffmpeg", "-version"],
            capture_output=True,
            text=True,
            timeout=5,
            shell=False,
        )
        if proc.returncode == 0:
            ffmpeg_ok = True
    except Exception:
        pass

    writable = os.access(DOWNLOAD_DIR, os.W_OK)
    disk_free_mb = 0
    try:
        disk_free_mb = shutil.disk_usage(DOWNLOAD_DIR).free // (1024 * 1024)
    except Exception:
        pass

    disk_ok = disk_free_mb >= MIN_FREE_DISK_SPACE_MB
    all_ok = yt_dlp_ok and ffmpeg_ok and writable and disk_ok

    status_code = 200 if all_ok else 503
    return jsonify({
        "status": "healthy" if all_ok else "degraded",
        "service": "ReClip Plus",
        "python_version": platform.python_version(),
        "yt_dlp": {
            "available": yt_dlp_ok,
            "version": yt_dlp_version,
        },
        "ffmpeg": {
            "available": ffmpeg_ok,
        },
        "storage": {
            "writable": writable,
            "free_mb": disk_free_mb,
            "min_required_mb": MIN_FREE_DISK_SPACE_MB,
        },
        "queue": {
            "active_count": len(job_manager.active_jobs),
            "queued_count": len(job_manager.waiting_queue),
            "total_jobs": len(job_manager.jobs),
        },
    }), status_code


@app.route("/api/info", methods=["POST"])
def fetch_info():
    data = request.get_json(silent=True) or {}
    url = data.get("url", "").strip()
    if not url:
        return jsonify({"error": "No URL provided."}), 400

    safe, msg = is_safe_url(url)
    if not safe:
        return jsonify({"error": msg}), 400

    # Check in-memory metadata cache first
    cached = metadata_cache.get(url)
    if cached:
        return jsonify(cached)

    try:
        info = get_media_info(url)
        metadata_cache.set(url, info)
        return jsonify(info)
    except TimeoutError as e:
        return jsonify({"error": str(e)}), 408
    except ValueError as e:
        return jsonify({"error": friendly_error_message(str(e))}), 400
    except Exception as e:
        return jsonify({"error": friendly_error_message(str(e))}), 500


@app.route("/api/playlist", methods=["POST"])
def fetch_playlist():
    data = request.get_json(silent=True) or {}
    url = data.get("url", "").strip()
    if not url:
        return jsonify({"error": "No URL provided."}), 400

    limit = data.get("limit", MAX_PLAYLIST_ITEMS)
    try:
        limit = max(1, min(int(limit), MAX_PLAYLIST_ITEMS))
    except (ValueError, TypeError):
        limit = MAX_PLAYLIST_ITEMS

    safe, msg = is_safe_url(url)
    if not safe:
        return jsonify({"error": msg}), 400

    try:
        playlist_data = get_playlist_info(url, max_items=limit)
        return jsonify(playlist_data)
    except TimeoutError as e:
        return jsonify({"error": str(e)}), 408
    except ValueError as e:
        return jsonify({"error": friendly_error_message(str(e))}), 400
    except Exception as e:
        return jsonify({"error": friendly_error_message(str(e))}), 500


@app.route("/api/download", methods=["POST"])
def start_download():
    data = request.get_json(silent=True) or {}
    url = data.get("url", "").strip()
    if not url:
        return jsonify({"error": "No URL provided."}), 400

    format_type = data.get("format", "video")
    if format_type not in ("video", "audio", "subtitles"):
        format_type = "video"

    video_quality = data.get("video_quality") or data.get("format_id") or "best"
    audio_quality = data.get("audio_quality", "320")
    title = data.get("title", "").strip()
    uploader = data.get("uploader", "").strip()
    duration = data.get("duration")
    filesize = data.get("filesize")
    options = data.get("options") or {}

    # Check cache for missing metadata
    cached = metadata_cache.get(url)
    if cached:
        if not duration and cached.get("duration"):
            duration = cached["duration"]
        if not filesize and cached.get("filesize"):
            filesize = cached["filesize"]
        if not title and cached.get("title"):
            title = cached["title"]
        if not uploader and cached.get("uploader"):
            uploader = cached["uploader"]

    try:
        job = job_manager.create_job(
            url=url,
            format_type=format_type,
            video_quality=video_quality,
            audio_quality=audio_quality,
            options=options,
            title=title,
            uploader=uploader,
            duration=duration,
            filesize=filesize,
        )
        return jsonify({
            "job_id": job.id,
            "status": job.status,
            "message": "Job queued successfully.",
        }), 201
    except ValueError as e:
        return jsonify({"error": friendly_error_message(str(e))}), 400
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 507  # Insufficient storage
    except Exception as e:
        return jsonify({"error": friendly_error_message(str(e))}), 500


@app.route("/api/status/<job_id>")
def get_job_status(job_id: str):
    if not re.match(r"^[a-f0-9]{10}$", job_id):
        return jsonify({"error": "Invalid job ID."}), 400

    job = job_manager.get_job(job_id)
    if not job:
        return jsonify({"error": "Job not found."}), 404

    return jsonify(job.to_dict())


@app.route("/api/events/<job_id>")
def stream_job_events(job_id: str):
    """Server-Sent Events (SSE) endpoint providing real-time live download progress."""
    if not re.match(r"^[a-f0-9]{10}$", job_id):
        return jsonify({"error": "Invalid job ID."}), 400

    job = job_manager.get_job(job_id)
    if not job:
        return jsonify({"error": "Job not found."}), 404

    import json

    def event_stream() -> Generator[str, None, None]:
        # Send initial snapshot immediately
        yield f"data: {json.dumps(job.to_dict())}\n\n"
        if job.status in ("done", "error", "cancelled"):
            return

        listener_q: queue.Queue = queue.Queue(maxsize=500)
        job.add_listener(listener_q)
        try:
            while True:
                try:
                    data = listener_q.get(timeout=15)
                    yield f"data: {json.dumps(data)}\n\n"
                    if data.get("status") in ("done", "error", "cancelled"):
                        break
                except queue.Empty:
                    # Heartbeat comment to keep the SSE connection alive
                    yield ": keep-alive\n\n"
        finally:
            job.remove_listener(listener_q)

    return Response(
        event_stream(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.route("/api/cancel/<job_id>", methods=["POST"])
def cancel_job(job_id: str):
    if not re.match(r"^[a-f0-9]{10}$", job_id):
        return jsonify({"error": "Invalid job ID."}), 400

    ok = job_manager.cancel_job(job_id)
    if not ok:
        return jsonify({"error": "Job not found or already finished."}), 404
    return jsonify({"ok": True, "job_id": job_id, "status": "cancelled"})


@app.route("/api/retry/<job_id>", methods=["POST"])
def retry_job(job_id: str):
    if not re.match(r"^[a-f0-9]{10}$", job_id):
        return jsonify({"error": "Invalid job ID."}), 400

    ok = job_manager.retry_job(job_id)
    if not ok:
        return jsonify({"error": "Job cannot be retried or does not exist."}), 400
    return jsonify({"ok": True, "job_id": job_id, "status": "queued"})


@app.route("/api/reorder", methods=["POST"])
def reorder_queue():
    data = request.get_json(silent=True) or {}
    job_id = data.get("job_id", "")
    new_index = data.get("new_index", 0)

    if not re.match(r"^[a-f0-9]{10}$", job_id):
        return jsonify({"error": "Invalid job ID."}), 400

    ok = job_manager.reorder_queue(job_id, int(new_index))
    if not ok:
        return jsonify({"error": "Job is not currently in the waiting queue."}), 400
    return jsonify({"ok": True, "job_id": job_id, "new_index": new_index})


@app.route("/api/jobs", methods=["GET"])
def list_jobs():
    return jsonify({
        "jobs": job_manager.list_jobs(),
        "active_count": len(job_manager.active_jobs),
        "queued_count": len(job_manager.waiting_queue),
    })


@app.route("/api/jobs/<job_id>", methods=["DELETE"])
def delete_job(job_id: str):
    if not re.match(r"^[a-f0-9]{10}$", job_id):
        return jsonify({"error": "Invalid job ID."}), 400

    job_manager.remove_job(job_id)
    return jsonify({"ok": True})


@app.route("/api/generate-command", methods=["POST"])
def generate_command():
    """Return the equivalent yt-dlp CLI command for user settings without exposing server paths."""
    data = request.get_json(silent=True) or {}
    url = data.get("url", "").strip()
    if not url:
        return jsonify({"error": "No URL provided."}), 400

    format_type = data.get("format", "video")
    video_quality = data.get("video_quality", "best")
    audio_quality = data.get("audio_quality", "320")
    options = data.get("options") or {}
    filename_strategy = options.get("filename_strategy", "title")

    cli_cmd = build_ytdlp_cli_command(
        url=url,
        format_type=format_type,
        video_quality=video_quality,
        audio_quality=audio_quality,
        options=options,
        filename_strategy=filename_strategy,
    )
    return jsonify({"command": cli_cmd})


@app.route("/api/file/<job_id>")
def download_file(job_id: str):
    if not re.match(r"^[a-f0-9]{10}$", job_id):
        return jsonify({"error": "Invalid job ID."}), 400

    job = job_manager.get_job(job_id)
    if not job or job.status != "done" or not job.file_path:
        return jsonify({"error": "File is not ready or job does not exist."}), 404

    # Security check: verify path is strictly inside DOWNLOAD_DIR
    real_file_path = os.path.normcase(os.path.realpath(job.file_path))
    real_dl_dir = os.path.normcase(os.path.realpath(DOWNLOAD_DIR))
    if not os.path.isfile(real_file_path):
        return jsonify({"error": "File not found on server disk."}), 404

    try:
        common = os.path.commonpath([real_dl_dir, real_file_path])
        if common != real_dl_dir:
            return jsonify({"error": "Access denied."}), 403
    except ValueError:
        return jsonify({"error": "Access denied."}), 403

    download_name = job.filename or os.path.basename(real_file_path)

    response = send_file(
        real_file_path,
        as_attachment=True,
        download_name=download_name,
    )

    # Safe delete after delivery: hook to close of response stream without risking active transfer
    if DELETE_AFTER_DOWNLOAD:
        response.direct_passthrough = False

        def on_stream_finished():
            try:
                if os.path.isfile(real_file_path):
                    os.remove(real_file_path)
            except OSError:
                pass
        response.call_on_close(on_stream_finished)

    return response


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8899))
    host = os.environ.get("HOST", "0.0.0.0")
    try:
        app.run(host=host, port=port)
    except KeyboardInterrupt:
        pass

