import json
import os
import re
import shlex
import subprocess
from typing import Any

from reclip.utils import (
    DOWNLOAD_DIR,
    YTDLP_CONCURRENT_FRAGMENTS,
    format_bytes,
    format_time_str,
    is_safe_url,
    parse_time_str,
)


def parse_ytdlp_json(stdout: str) -> dict[str, Any]:
    """Parse yt-dlp JSON output.

    With -j yt-dlp prints one JSON object per line. If an extractor emits multiple
    items, stdout may contain multiple lines. Return the first valid JSON object.
    """
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            continue
    raise ValueError("yt-dlp returned no valid JSON data.")


def get_media_info(url: str, timeout: int = 45) -> dict[str, Any]:
    """Extract metadata, formats, chapters, and subtitles for a media URL using yt-dlp."""
    safe, msg = is_safe_url(url)
    if not safe:
        raise ValueError(msg)

    cmd = [
        "yt-dlp",
        "--no-playlist",
        "--no-warnings",
        "--no-colors",
        "--skip-download",
        "-j",
        url,
    ]

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,
        )
    except subprocess.TimeoutExpired:
        raise TimeoutError("Timed out fetching video metadata.")
    except FileNotFoundError:
        raise RuntimeError("yt-dlp executable not found.")

    if proc.returncode != 0:
        err = proc.stderr.strip() or proc.stdout.strip()
        last_line = err.splitlines()[-1] if err else "Failed to extract metadata."
        raise ValueError(last_line)

    info = parse_ytdlp_json(proc.stdout)

    # Process and sort video formats by resolution
    best_by_height: dict[int, dict[str, Any]] = {}
    for f in info.get("formats", []):
        height = f.get("height")
        vcodec = f.get("vcodec", "none")
        if height and vcodec != "none":
            tbr = f.get("tbr") or 0
            if height not in best_by_height or tbr > (best_by_height[height].get("tbr") or 0):
                best_by_height[height] = f

    formats = []
    for height in sorted(best_by_height.keys(), reverse=True):
        f = best_by_height[height]
        filesize = f.get("filesize") or f.get("filesize_approx")
        size_str = format_bytes(filesize) if filesize else ""
        formats.append({
            "id": str(f.get("format_id", "")),
            "label": f"{height}p",
            "height": height,
            "vcodec": f.get("vcodec", ""),
            "acodec": f.get("acodec", ""),
            "fps": f.get("fps"),
            "size": size_str,
        })

    # Chapters
    chapters = []
    for ch in info.get("chapters") or []:
        start = ch.get("start_time", 0)
        end = ch.get("end_time", 0)
        chapters.append({
            "title": ch.get("title", "Untitled Chapter"),
            "start_time": start,
            "end_time": end,
            "start_str": format_time_str(start),
            "end_str": format_time_str(end),
        })

    # Subtitles
    subtitles = []
    manual_subs = info.get("subtitles") or {}
    for lang, sub_list in manual_subs.items():
        name = sub_list[0].get("name") if sub_list else lang
        subtitles.append({"code": lang, "name": name or lang, "auto": False})

    auto_subs = info.get("automatic_captions") or {}
    for lang, sub_list in auto_subs.items():
        if lang not in manual_subs:
            name = sub_list[0].get("name") if sub_list else lang
            subtitles.append({"code": lang, "name": f"{name or lang} (Auto)", "auto": True})

    duration = info.get("duration")
    duration_str = format_time_str(duration) if duration else None

    filesize = info.get("filesize") or info.get("filesize_approx")
    size_str = format_bytes(filesize) if filesize else None

    return {
        "id": info.get("id", ""),
        "title": info.get("title", ""),
        "thumbnail": info.get("thumbnail", ""),
        "duration": duration,
        "duration_string": duration_str,
        "uploader": info.get("uploader", "") or info.get("channel", ""),
        "filesize": filesize,
        "filesize_str": size_str,
        "formats": formats,
        "chapters": chapters,
        "subtitles": subtitles,
        "webpage_url": info.get("webpage_url", url),
    }


def get_playlist_info(url: str, max_items: int = 50, timeout: int = 45) -> dict[str, Any]:
    """Extract playlist items using flat-playlist mode without downloading metadata for every video."""
    safe, msg = is_safe_url(url)
    if not safe:
        raise ValueError(msg)

    cmd = [
        "yt-dlp",
        "--flat-playlist",
        "--playlist-end",
        str(max_items),
        "--no-warnings",
        "--no-colors",
        "-J",
        url,
    ]

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,
        )
    except subprocess.TimeoutExpired:
        raise TimeoutError("Timed out fetching playlist info.")
    except FileNotFoundError:
        raise RuntimeError("yt-dlp executable not found.")

    if proc.returncode != 0:
        err = proc.stderr.strip() or proc.stdout.strip()
        last_line = err.splitlines()[-1] if err else "Failed to fetch playlist."
        raise ValueError(last_line)

    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        raise ValueError("yt-dlp returned invalid JSON for playlist.")

    entries = data.get("entries", [])
    items = []
    for entry in entries:
        if not entry:
            continue
        entry_url = entry.get("url") or entry.get("webpage_url")
        if not entry_url:
            entry_id = entry.get("id")
            if entry_id:
                entry_url = f"https://www.youtube.com/watch?v={entry_id}"
            else:
                continue

        duration = entry.get("duration")
        items.append({
            "id": entry.get("id", ""),
            "title": entry.get("title", "Untitled"),
            "url": entry_url,
            "duration": duration,
            "duration_str": format_time_str(duration) if duration else "",
            "uploader": entry.get("uploader", ""),
        })

    return {
        "title": data.get("title", "Playlist"),
        "count": len(items),
        "items": items,
        "urls": [item["url"] for item in items],
    }


def build_download_args(
    job_id: str,
    url: str,
    format_type: str = "video",
    video_quality: str = "best",
    audio_quality: str = "320",
    options: dict[str, Any] | None = None,
) -> tuple[list[str], str]:
    """Construct command-line arguments for yt-dlp execution.

    Returns (cmd_args, out_template).
    """
    opts = options or {}
    out_template = os.path.join(DOWNLOAD_DIR, f"{job_id}.%(ext)s")

    cmd = [
        "yt-dlp",
        "--no-playlist",
        "--newline",
        "--no-exec",
        "--no-colors",
        "--concurrent-fragments",
        str(YTDLP_CONCURRENT_FRAGMENTS),
        "--progress-delta",
        "0.5",
        "--progress-template",
        "download:[RECLIP_PROGRESS] %(progress._percent_str)s|%(progress._downloaded_bytes_str)s|%(progress._total_bytes_str|progress._total_bytes_estimate_str)s|%(progress._speed_str)s|%(progress._eta_str)s",
        "--progress-template",
        "postprocess:[RECLIP_POSTPROCESS] %(progress.status)s",
        "-o",
        out_template,
    ]

    # Format handling
    if format_type == "audio":
        # Ensure only audio is downloaded, avoiding massive video stream downloads
        cmd.extend(["-f", "ba/bestaudio/best"])
        if audio_quality == "original":
            # Direct extraction / stream copy without re-encoding
            cmd.extend(["-x"])
        else:
            # Transcode to MP3 with specified bitrate
            kbps = str(audio_quality).replace("k", "").replace("K", "")
            if kbps not in ("128", "192", "256", "320"):
                kbps = "320"
            cmd.extend(["-x", "--audio-format", "mp3", "--audio-quality", f"{kbps}K"])
    elif format_type == "subtitles":
        # Subtitle file only download
        sub_lang = opts.get("sub_lang") or "en"
        safe_lang = re.sub(r"[^\w\-]", "", str(sub_lang))
        cmd.extend([
            "--skip-download",
            "--write-subs",
            "--sub-langs", safe_lang,
            "--convert-subs", "srt",
        ])
        if opts.get("auto_subs"):
            cmd.append("--write-auto-subs")
    else:
        # Video formats
        vq_str = str(video_quality).strip().lower()
        if vq_str == "best":
            cmd.extend(["-f", "bestvideo+bestaudio/best", "--merge-output-format", "mp4"])
        elif vq_str == "balanced":
            # Broad compatibility (AVC1/H.264 + AAC in MP4 container)
            cmd.extend([
                "-f",
                "bv*[ext=mp4][vcodec^=avc1]+ba*[ext=m4a]/b[ext=mp4]/best",
                "--merge-output-format",
                "mp4",
            ])
        elif vq_str == "fastest":
            # Pre-merged single stream to avoid separate downloads and ffmpeg merge
            cmd.extend(["-f", "b[ext=mp4]/b/best"])
        elif vq_str.rstrip("p").isdigit() and (
            vq_str.endswith("p") or vq_str in ("144", "240", "360", "480", "720", "1080", "1440", "2160", "4320")
        ):
            # Target height resolution limit (e.g. 1080, 720, 1080p)
            height = int(vq_str.rstrip("p"))
            cmd.extend([
                "-f",
                f"bv*[height<={height}]+ba/b[height<={height}]/best",
                "--merge-output-format",
                "mp4",
            ])
        else:
            # Custom format_id (e.g. specific format picked by user)
            safe_fmt = re.sub(r"[^\w\-\+\/]", "", str(video_quality))
            cmd.extend(["-f", f"{safe_fmt}+bestaudio/best", "--merge-output-format", "mp4"])

    # Partial video download (time sections)
    start_time = parse_time_str(opts.get("start_time"))
    end_time = parse_time_str(opts.get("end_time"))
    if start_time is not None and end_time is not None and end_time <= start_time:
        raise ValueError(
            f"End time ({opts.get('end_time')}) must be greater than start time ({opts.get('start_time')})."
        )
    if start_time is not None or end_time is not None:
        start_s = format_time_str(start_time or 0)
        end_s = format_time_str(end_time) if end_time is not None else "inf"
        # Standard yt-dlp section syntax: *00:01:00-00:02:30
        section_str = f"*{start_s}-{end_s}"
        cmd.extend(["--download-sections", section_str, "--force-keyframes-at-cuts"])

    # Chapter splitting support
    if opts.get("split_chapters"):
        cmd.append("--split-chapters")

    # Subtitles
    sub_lang = opts.get("sub_lang")
    if sub_lang and format_type != "subtitles":
        safe_lang = re.sub(r"[^\w\-]", "", str(sub_lang))
        cmd.extend(["--write-subs", "--sub-langs", safe_lang])
        if opts.get("auto_subs"):
            cmd.append("--write-auto-subs")
        if opts.get("embed_subs"):
            cmd.append("--embed-subs")

    # Embed flags
    if opts.get("embed_metadata", True):
        cmd.append("--embed-metadata")
    if opts.get("embed_thumbnail", False) and format_type != "audio":
        cmd.append("--embed-thumbnail")
    if opts.get("embed_chapters", False):
        cmd.append("--embed-chapters")

    # Append URL at end
    cmd.append(url)
    return cmd, out_template


def parse_progress_line(line: str) -> dict[str, Any] | None:
    """Parse a single stdout line from yt-dlp into a structured progress update dictionary."""
    line = line.strip()
    if not line:
        return None

    # Custom structured template output
    if "[RECLIP_PROGRESS]" in line:
        try:
            parts = line.split("[RECLIP_PROGRESS]")[1].strip().split("|")
            if len(parts) >= 5:
                pct_str, dl_str, total_str, speed_str, eta_str = [p.strip() for p in parts[:5]]
                pct_val = 0.0
                clean_pct = pct_str.replace("%", "").strip()
                if clean_pct:
                    try:
                        pct_val = float(clean_pct)
                    except ValueError:
                        pct_val = 0.0

                return {
                    "stage": "downloading",
                    "percent": round(pct_val, 1),
                    "downloaded_str": dl_str if dl_str and dl_str != "NA" else "",
                    "total_str": total_str if total_str and total_str != "NA" else "",
                    "speed_str": speed_str if speed_str and speed_str != "NA" else "",
                    "eta_str": eta_str if eta_str and eta_str != "NA" else "",
                }
        except Exception:
            pass

    # Postprocess marker
    if "[RECLIP_POSTPROCESS]" in line:
        status = line.split("[RECLIP_POSTPROCESS]")[1].strip().lower()
        return {
            "stage": "processing",
            "percent": 100.0,
            "status_msg": f"Processing media ({status})...",
        }

    # Standard yt-dlp download progress regex fallback
    # e.g.: [download]  42.5% of ~  12.34MiB at    3.45MiB/s ETA 00:08
    dl_match = re.search(
        r"\[download\]\s+([\d.]+)%\s+of\s+~?\s*([\d.]+\s*\w+)(?:\s+at\s+([\d.]+\s*\w+/s))?(?:\s+ETA\s+([\d:]+))?",
        line,
    )
    if dl_match:
        try:
            pct = float(dl_match.group(1))
            total = dl_match.group(2)
            speed = dl_match.group(3) or ""
            eta = dl_match.group(4) or ""
            return {
                "stage": "downloading",
                "percent": round(pct, 1),
                "total_str": total,
                "speed_str": speed,
                "eta_str": eta,
            }
        except Exception:
            pass

    # Merger and audio extraction detection
    if "[Merger]" in line or "Merging formats into" in line:
        return {"stage": "merging", "status_msg": "Merging video and audio streams..."}
    if "[ExtractAudio]" in line or "Destination:" in line and line.endswith(".mp3"):
        return {"stage": "extracting_audio", "status_msg": "Extracting audio track..."}
    if "[Fixup" in line:
        return {"stage": "processing", "status_msg": "Fixing container metadata..."}

    return None


def build_ytdlp_cli_command(
    url: str,
    format_type: str = "video",
    video_quality: str = "best",
    audio_quality: str = "320",
    options: dict[str, Any] | None = None,
    filename_strategy: str = "title",
) -> str:
    """Generate a clean, copyable yt-dlp command representing the user's selected configuration.

    No server-internal paths are included.
    """
    opts = options or {}
    template_map = {
        "title": "%(title)s.%(ext)s",
        "channel_title": "%(uploader)s - %(title)s.%(ext)s",
        "title_res": "%(title)s [%(resolution)s].%(ext)s",
        "title_id": "%(title)s [%(id)s].%(ext)s",
        "original": "%(title)s.%(ext)s",
    }
    out_tmpl = template_map.get(filename_strategy, "%(title)s.%(ext)s")

    tokens = ["yt-dlp", "--no-playlist", "-o", out_tmpl]

    if format_type == "audio":
        tokens.extend(["-f", "ba/bestaudio/best"])
        if audio_quality == "original":
            tokens.append("-x")
        else:
            kbps = str(audio_quality).replace("k", "").replace("K", "")
            if kbps not in ("128", "192", "256", "320"):
                kbps = "320"
            tokens.extend(["-x", "--audio-format", "mp3", "--audio-quality", f"{kbps}K"])
    elif format_type == "subtitles":
        sub_lang = opts.get("sub_lang") or "en"
        safe_lang = re.sub(r"[^\w\-]", "", str(sub_lang))
        tokens.extend([
            "--skip-download",
            "--write-subs",
            "--sub-langs", safe_lang,
            "--convert-subs", "srt",
        ])
        if opts.get("auto_subs"):
            tokens.append("--write-auto-subs")
    else:
        vq_str = str(video_quality).strip().lower()
        if vq_str == "best":
            tokens.extend(["-f", "bestvideo+bestaudio/best", "--merge-output-format", "mp4"])
        elif vq_str == "balanced":
            tokens.extend([
                "-f",
                "bv*[ext=mp4][vcodec^=avc1]+ba*[ext=m4a]/b[ext=mp4]/best",
                "--merge-output-format",
                "mp4",
            ])
        elif vq_str == "fastest":
            tokens.extend(["-f", "b[ext=mp4]/b/best"])
        elif vq_str.rstrip("p").isdigit() and (
            vq_str.endswith("p") or vq_str in ("144", "240", "360", "480", "720", "1080", "1440", "2160", "4320")
        ):
            height = int(vq_str.rstrip("p"))
            tokens.extend([
                "-f",
                f"bv*[height<={height}]+ba/b[height<={height}]/best",
                "--merge-output-format",
                "mp4",
            ])
        else:
            safe_fmt = re.sub(r"[^\w\-\+\/]", "", str(video_quality))
            tokens.extend(["-f", f"{safe_fmt}+bestaudio/best", "--merge-output-format", "mp4"])

    start_time = parse_time_str(opts.get("start_time"))
    end_time = parse_time_str(opts.get("end_time"))
    if start_time is not None or end_time is not None:
        start_s = format_time_str(start_time or 0)
        end_s = format_time_str(end_time) if end_time is not None else "inf"
        tokens.extend(["--download-sections", f"*{start_s}-{end_s}", "--force-keyframes-at-cuts"])

    if opts.get("split_chapters"):
        tokens.append("--split-chapters")

    sub_lang = opts.get("sub_lang")
    if sub_lang and format_type != "subtitles":
        safe_lang = re.sub(r"[^\w\-]", "", str(sub_lang))
        tokens.extend(["--write-subs", "--sub-langs", safe_lang])
        if opts.get("auto_subs"):
            tokens.append("--write-auto-subs")
        if opts.get("embed_subs"):
            tokens.append("--embed-subs")

    if opts.get("embed_metadata", True):
        tokens.append("--embed-metadata")
    if opts.get("embed_thumbnail", False) and format_type != "audio":
        tokens.append("--embed-thumbnail")
    if opts.get("embed_chapters", False):
        tokens.append("--embed-chapters")

    tokens.append(url)
    return shlex.join(tokens)
