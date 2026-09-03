import glob
import os
import queue
import signal
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from typing import Any

from reclip.downloader import build_download_args, parse_progress_line
from reclip.utils import (
    DOWNLOAD_DIR,
    MAX_CONCURRENT_DOWNLOADS,
    MAX_DURATION,
    MAX_ESTIMATED_FILE_SIZE,
    check_disk_space,
    format_bytes,
    friendly_error_message,
    is_safe_url,
    sanitize_filename,
)


class Job:
    """Represents an individual download job and its real-time state."""

    def __init__(
        self,
        job_id: str,
        url: str,
        format_type: str = "video",
        video_quality: str = "best",
        audio_quality: str = "320",
        options: dict[str, Any] | None = None,
        title: str = "",
        uploader: str = "",
        duration: float | None = None,
        filesize: int | None = None,
    ):
        self.id = job_id
        self.url = url
        self.title = title
        self.uploader = uploader
        self.duration = duration
        self.format_type = format_type
        self.video_quality = video_quality
        self.audio_quality = audio_quality
        self.options = options or {}

        self.status = "queued"  # queued, downloading, merging, extracting_audio, processing, done, error, cancelled
        self.progress = {
            "percent": 0.0,
            "downloaded_str": "",
            "total_str": "",
            "speed_str": "",
            "eta_str": "",
            "stage": "queued",
            "status_msg": "Waiting in download queue...",
        }
        self.file_path: str | None = None
        self.filename: str | None = None
        self.filesize: int | None = filesize
        self.error: str | None = None

        self.created_at = time.time()
        self.updated_at = time.time()
        self.retries = 0
        self.max_retries = 2

        self.process: subprocess.Popen | None = None
        self._cancelled = False
        self._listeners: list[queue.Queue] = []
        self._lock = threading.Lock()

    def add_listener(self, q: queue.Queue) -> None:
        with self._lock:
            self._listeners.append(q)

    def remove_listener(self, q: queue.Queue) -> None:
        with self._lock:
            if q in self._listeners:
                self._listeners.remove(q)

    def broadcast(self) -> None:
        """Send current state to all connected SSE listener queues."""
        data = self.to_dict()
        with self._lock:
            dead = []
            for q in self._listeners:
                try:
                    q.put_nowait(data)
                except queue.Full:
                    try:
                        # Drop oldest progress tick so SSE connection remains alive
                        q.get_nowait()
                        q.put_nowait(data)
                    except Exception:
                        dead.append(q)
                except Exception:
                    dead.append(q)
            for q in dead:
                if q in self._listeners:
                    self._listeners.remove(q)

    def update_progress(self, progress_data: dict[str, Any]) -> None:
        self.updated_at = time.time()
        for k, v in progress_data.items():
            if v is not None:
                self.progress[k] = v
        if "stage" in progress_data:
            stage = progress_data["stage"]
            if stage in ("downloading", "merging", "extracting_audio", "processing"):
                self.status = stage
        self.broadcast()

    def get_safe_filename(self, ext: str) -> str:
        """Generate safe, human-readable file name based on selected naming strategy."""
        strategy = self.options.get("filename_strategy", "title")
        base = self.title or "media"
        uploader = self.uploader or ""
        chapter = self.options.get("chapter_title", "")
        if chapter:
            base = f"{base} - {chapter}"

        if strategy == "channel_title" and uploader:
            candidate = f"{uploader} - {base}"
        elif strategy == "title_res" and self.video_quality and self.video_quality not in ("best", "balanced", "fastest"):
            clean_res = str(self.video_quality).rstrip("p")
            candidate = f"{base} [{clean_res}p]" if clean_res.isdigit() else f"{base} [{self.video_quality}]"
        elif strategy == "title_id":
            candidate = f"{base} [{self.id}]"
        else:
            candidate = base

        clean_ext = ext if ext.startswith(".") else f".{ext}"
        return sanitize_filename(f"{candidate}{clean_ext}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "url": self.url,
            "title": self.title,
            "uploader": self.uploader,
            "duration": self.duration,
            "format_type": self.format_type,
            "video_quality": self.video_quality,
            "audio_quality": self.audio_quality,
            "status": self.status,
            "progress": self.progress,
            "filename": self.filename,
            "filesize": self.filesize,
            "error": self.error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class JobManager:
    """Manages the download queue, bounded concurrency, worker threads, and process lifecycles."""

    def __init__(self, max_concurrent: int = MAX_CONCURRENT_DOWNLOADS):
        self.max_concurrent = max_concurrent
        self.jobs: dict[str, Job] = {}
        self.waiting_queue: deque[str] = deque()
        self.active_jobs: dict[str, Job] = {}
        self.lock = threading.Lock()

    def create_job(
        self,
        url: str,
        format_type: str = "video",
        video_quality: str = "best",
        audio_quality: str = "320",
        options: dict[str, Any] | None = None,
        title: str = "",
        uploader: str = "",
        duration: float | None = None,
        filesize: int | None = None,
    ) -> Job:
        safe, msg = is_safe_url(url)
        if not safe:
            raise ValueError(msg)

        if MAX_DURATION > 0 and duration and duration > MAX_DURATION:
            raise ValueError(f"Video duration ({int(duration)}s) exceeds maximum allowed duration ({MAX_DURATION}s).")

        if MAX_ESTIMATED_FILE_SIZE > 0 and filesize and filesize > MAX_ESTIMATED_FILE_SIZE:
            raise ValueError(
                f"Estimated file size ({format_bytes(filesize)}) exceeds maximum allowed limit ({format_bytes(MAX_ESTIMATED_FILE_SIZE)})."
            )

        has_space, disk_msg = check_disk_space(required_bytes=filesize or 0)
        if not has_space:
            raise RuntimeError(disk_msg)

        job_id = uuid.uuid4().hex[:10]
        job = Job(
            job_id=job_id,
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

        with self.lock:
            self.jobs[job_id] = job
            self.waiting_queue.append(job_id)

        self._process_queue()
        return job

    def get_job(self, job_id: str) -> Job | None:
        with self.lock:
            return self.jobs.get(job_id)

    def list_jobs(self) -> list[dict[str, Any]]:
        with self.lock:
            return [job.to_dict() for job in self.jobs.values()]

    def get_active_file_paths(self) -> set[str]:
        """Return paths of files belonging to active or done jobs that should not be deleted."""
        paths = set()
        with self.lock:
            for job in self.jobs.values():
                if job.file_path:
                    paths.add(os.path.abspath(job.file_path))
                # Also protect active job output templates
                out_pattern = os.path.join(DOWNLOAD_DIR, f"{job.id}.*")
                for f in glob.glob(out_pattern):
                    paths.add(os.path.abspath(f))
        return paths

    def cancel_job(self, job_id: str) -> bool:
        """Cancel an active or queued job, terminating processes and cleaning temporary files."""
        job = self.get_job(job_id)
        if not job:
            return False

        with self.lock:
            job._cancelled = True
            job.status = "cancelled"
            job.progress["status_msg"] = "Download cancelled by user."

            # If waiting in queue, simply remove it
            if job_id in self.waiting_queue:
                self.waiting_queue.remove(job_id)
                job.broadcast()
                return True

            # If active, terminate process
            proc = job.process
            if job_id in self.active_jobs:
                del self.active_jobs[job_id]

        if proc and proc.poll() is None:
            self._terminate_process_tree(proc)

        self._clean_job_temp_files(job_id, clean_all=True)
        job.broadcast()
        self._process_queue()
        return True

    def retry_job(self, job_id: str) -> bool:
        """Retry a failed or cancelled job with bounded retries and format fallback."""
        job = self.get_job(job_id)
        if not job:
            return False

        with self.lock:
            if job.status not in ("error", "cancelled"):
                return False

            if job.retries >= job.max_retries:
                job.error = f"Maximum retry limit ({job.max_retries}) reached."
                job.broadcast()
                return False

            job.retries += 1
            job._cancelled = False
            job.error = None
            job.status = "queued"

            # Smart retry: on attempt 2, fall back to broadly compatible default format
            if job.retries == 2:
                if job.format_type == "video" and job.video_quality not in ("best", "balanced", "fastest"):
                    job.video_quality = "best"
                elif job.format_type == "audio" and job.audio_quality not in ("original", "192"):
                    job.audio_quality = "192"

            job.progress = {
                "percent": 0.0,
                "downloaded_str": "",
                "total_str": "",
                "speed_str": "",
                "eta_str": "",
                "stage": "queued",
                "status_msg": f"Queued for retry (attempt {job.retries + 1}/{job.max_retries + 1})...",
            }
            self.waiting_queue.append(job_id)

        job.broadcast()
        self._process_queue()
        return True

    def reorder_queue(self, job_id: str, new_index: int) -> bool:
        """Move a queued job to a different position in the waiting queue."""
        with self.lock:
            if job_id not in self.waiting_queue:
                return False
            self.waiting_queue.remove(job_id)
            idx = max(0, min(new_index, len(self.waiting_queue)))
            self.waiting_queue.insert(idx, job_id)
            return True

    def remove_job(self, job_id: str) -> bool:
        """Remove a job from memory and delete its output file."""
        self.cancel_job(job_id)
        with self.lock:
            job = self.jobs.pop(job_id, None)
            if job and job.file_path and os.path.exists(job.file_path):
                try:
                    os.remove(job.file_path)
                except OSError:
                    pass
            self._clean_job_temp_files(job_id, clean_all=True)
            return True

    def _process_queue(self) -> None:
        """Check the queue and launch worker threads up to max_concurrent."""
        with self.lock:
            while len(self.active_jobs) < self.max_concurrent and self.waiting_queue:
                job_id = self.waiting_queue.popleft()
                job = self.jobs.get(job_id)
                if not job or job._cancelled:
                    continue

                job.status = "downloading"
                job.progress["stage"] = "downloading"
                job.progress["status_msg"] = "Starting download..."
                self.active_jobs[job_id] = job
                job.broadcast()

                t = threading.Thread(
                    target=self._run_download_worker,
                    args=(job,),
                    daemon=True,
                )
                t.start()

    def _run_download_worker(self, job: Job) -> None:
        """Execute yt-dlp in a background thread and stream progress lines."""
        cmd, out_template = build_download_args(
            job_id=job.id,
            url=job.url,
            format_type=job.format_type,
            video_quality=job.video_quality,
            audio_quality=job.audio_quality,
            options=job.options,
        )

        try:
            # Platform-specific process group creation
            kwargs: dict[str, Any] = {
                "stdout": subprocess.PIPE,
                "stderr": subprocess.STDOUT,
                "text": True,
                "bufsize": 1,
                "shell": False,
            }
            if sys.platform != "win32":
                kwargs["preexec_fn"] = os.setsid

            proc = subprocess.Popen(cmd, **kwargs)
            job.process = proc

            # Read stdout lines for real-time progress
            raw_output = []
            if proc.stdout:
                for line in proc.stdout:
                    if job._cancelled:
                        break
                    raw_output.append(line)
                    parsed = parse_progress_line(line)
                    if parsed:
                        job.update_progress(parsed)

            proc.wait(timeout=600)

            if job._cancelled:
                self._clean_job_temp_files(job.id, clean_all=True)
                return

            if proc.returncode != 0:
                full_log = "".join(raw_output)
                job.status = "error"
                job.error = friendly_error_message(full_log)
                job.progress["status_msg"] = f"Error: {job.error}"
                self._clean_job_temp_files(job.id, clean_all=True)
                job.broadcast()
                return

            # Download completed, locate the output file
            candidates = glob.glob(os.path.join(DOWNLOAD_DIR, f"{job.id}.*"))
            final_files = [f for f in candidates if not f.endswith((".part", ".ytdl", ".temp"))]

            if not final_files:
                job.status = "error"
                job.error = "Download completed but output file could not be located."
                job.progress["status_msg"] = job.error
                job.broadcast()
                return

            # Pick the primary file based on requested format
            chosen_file = final_files[0]
            if job.format_type == "audio":
                audio_matches = [f for f in final_files if f.endswith((".mp3", ".m4a", ".opus", ".flac", ".aac"))]
                if audio_matches:
                    chosen_file = audio_matches[0]
            elif job.format_type == "subtitles":
                sub_matches = [f for f in final_files if f.endswith((".srt", ".vtt", ".ass"))]
                if sub_matches:
                    chosen_file = sub_matches[0]
            else:
                video_matches = [f for f in final_files if f.endswith((".mp4", ".mkv", ".webm"))]
                if video_matches:
                    chosen_file = video_matches[0]

            # Clean extra files with the same job_id prefix (preserving subtitles and thumbnails)
            for f in final_files:
                if f != chosen_file:
                    if not f.endswith((".srt", ".vtt", ".ass", ".jpg", ".webp", ".png")):
                        try:
                            os.remove(f)
                        except OSError:
                            pass

            ext = os.path.splitext(chosen_file)[1]
            job.file_path = chosen_file
            job.filesize = os.path.getsize(chosen_file)
            job.filename = job.get_safe_filename(ext)
            job.status = "done"
            job.progress["percent"] = 100.0
            job.progress["stage"] = "done"
            job.progress["status_msg"] = "Download complete."
            job.broadcast()

        except subprocess.TimeoutExpired:
            self._terminate_process_tree(proc)
            job.status = "error"
            job.error = "Download exceeded time limit (10 minutes)."
            job.progress["status_msg"] = job.error
            self._clean_job_temp_files(job.id, clean_all=True)
            job.broadcast()
        except Exception as e:
            job.status = "error"
            job.error = friendly_error_message(str(e))
            job.progress["status_msg"] = job.error
            self._clean_job_temp_files(job.id, clean_all=True)
            job.broadcast()
        finally:
            with self.lock:
                if job.id in self.active_jobs:
                    del self.active_jobs[job.id]
            self._process_queue()

    def _terminate_process_tree(self, proc: subprocess.Popen) -> None:
        """Safely and aggressively terminate a process and all its children."""
        try:
            if sys.platform == "win32":
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                    capture_output=True,
                    timeout=5,
                )
            else:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except (ProcessLookupError, OSError):
                    proc.kill()
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    def _clean_job_temp_files(self, job_id: str, clean_all: bool = False) -> None:
        """Remove any temporary fragments or partial files for a specific job."""
        patterns = [f"{job_id}.*.part", f"{job_id}.*.ytdl", f"{job_id}.*.temp"]
        if clean_all:
            patterns.append(f"{job_id}.*")
        for pattern in patterns:
            for f in glob.glob(os.path.join(DOWNLOAD_DIR, pattern)):
                try:
                    os.remove(f)
                except OSError:
                    pass


# Global singleton JobManager instance
job_manager = JobManager()
