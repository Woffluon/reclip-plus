import ipaddress
import os
import re
import shutil
import socket
import threading
import time
import urllib.parse
from collections import OrderedDict
from typing import Any

# Project root directory (one level above reclip package)
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

# Configuration with environment variable overrides
DOWNLOAD_DIR = os.path.abspath(
    os.environ.get("RECLIP_DOWNLOAD_DIR", os.path.join(PROJECT_ROOT, "downloads"))
)
MAX_CONCURRENT_DOWNLOADS = int(os.environ.get("MAX_CONCURRENT_DOWNLOADS", "2"))
MAX_PLAYLIST_ITEMS = int(os.environ.get("MAX_PLAYLIST_ITEMS", "50"))
MAX_DURATION = int(os.environ.get("MAX_DURATION", "0"))  # 0 = unlimited, in seconds
MAX_ESTIMATED_FILE_SIZE = int(os.environ.get("MAX_ESTIMATED_FILE_SIZE", "0"))  # bytes, 0 = unlimited
DOWNLOAD_TTL = int(os.environ.get("DOWNLOAD_TTL", "3600"))  # seconds (default 1 hour)
MIN_FREE_DISK_SPACE_MB = int(os.environ.get("MIN_FREE_DISK_SPACE_MB", "500"))
YTDLP_CONCURRENT_FRAGMENTS = int(os.environ.get("YTDLP_CONCURRENT_FRAGMENTS", "4"))
METADATA_CACHE_TTL = int(os.environ.get("METADATA_CACHE_TTL", "600"))  # 10 minutes
METADATA_CACHE_MAX_SIZE = int(os.environ.get("METADATA_CACHE_MAX_SIZE", "500"))
DELETE_AFTER_DOWNLOAD = os.environ.get("DELETE_AFTER_DOWNLOAD", "false").lower() in ("true", "1", "yes")
ENABLE_SSRF_PROTECTION = os.environ.get("ENABLE_SSRF_PROTECTION", "true").lower() in ("true", "1", "yes")

# Ensure download directory exists
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# Windows reserved file names
WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
    "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9"
}

# Cloud metadata IPs and domains to explicitly block
BLOCKED_HOSTS = {
    "localhost", "127.0.0.1", "::1", "0.0.0.0",
    "metadata.google.internal", "instance-data",
    "169.254.169.254", "metadata.packet.net",
    "100.100.100.200"
}


def is_safe_url(url: str, check_dns: bool = True) -> tuple[bool, str]:
    """Validate that the URL is a safe HTTP/HTTPS URL and not pointing to local/private network.

    Protects against SSRF, cloud metadata access, and loopback exploits.
    """
    if not url or not isinstance(url, str):
        return False, "No URL provided."

    url = url.strip()
    try:
        parsed = urllib.parse.urlsplit(url)
    except Exception:
        return False, "Invalid URL structure."

    if parsed.scheme not in ("http", "https"):
        return False, f"Unsupported URL scheme '{parsed.scheme}'. Only http and https are allowed."

    hostname = parsed.hostname
    if not hostname:
        return False, "URL is missing a valid hostname."

    hostname_lower = hostname.lower()

    if not ENABLE_SSRF_PROTECTION:
        return True, ""

    # Direct hostname blocklist
    if hostname_lower in BLOCKED_HOSTS or hostname_lower.endswith(".localhost"):
        return False, "Access to localhost and cloud metadata endpoints is prohibited."

    def _is_forbidden_ip(ip_obj: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
        to_test = [ip_obj]
        mapped = getattr(ip_obj, "ipv4_mapped", None)
        if mapped:
            to_test.append(mapped)
        for check in to_test:
            if (
                check.is_loopback
                or check.is_private
                or check.is_link_local
                or check.is_multicast
                or check.is_reserved
                or check.is_unspecified
            ):
                return True
        return False

    # Check for IPv4 literal (standard, octal, decimal, or hex representation)
    try:
        packed = socket.inet_aton(hostname_lower)
        ip = ipaddress.ip_address(packed)
        if _is_forbidden_ip(ip):
            return False, f"Access to private/local IP address ({ip}) is prohibited."
    except (OSError, ValueError):
        pass

    # Check for IPv6 literal (with or without brackets)
    clean_host = hostname_lower.strip("[]")
    try:
        ip = ipaddress.ip_address(clean_host)
        if _is_forbidden_ip(ip):
            return False, f"Access to private/local IP address ({ip}) is prohibited."
    except ValueError:
        pass

    # Resolve hostname to check underlying IP addresses
    if check_dns:
        try:
            addr_info = socket.getaddrinfo(hostname, None)
            for item in addr_info:
                sockaddr = item[4]
                ip_str = sockaddr[0]
                ip = ipaddress.ip_address(ip_str)
                if _is_forbidden_ip(ip):
                    return False, f"Hostname resolves to a private or forbidden IP ({ip_str})."
        except socket.gaierror:
            return False, "Could not resolve hostname."
        except Exception as e:
            return False, f"DNS resolution failed: {e}"

    return True, ""


def sanitize_filename(name: str, max_length: int = 120, default: str = "download") -> str:
    """Sanitize a filename for safe use across Windows, macOS, and Linux.

    Prevents directory traversal, illegal chars, control chars, and Windows device names.
    """
    if not name or not isinstance(name, str):
        name = default

    name = name.strip()

    # If it's a directory path or traversal attempt (starts with slash/drive/traversal), extract basename
    if (
        name.startswith(("./", "../", "/", "\\"))
        or re.match(r"^[A-Za-z]:[/\\]", name)
        or re.search(r"\.\.[/\\]", name)
    ):
        name = os.path.basename(name.replace("\\", "/").rstrip("/"))

    # Separate base name and extension
    base, ext = os.path.splitext(name)

    # Handle case where filename was something like ".mp4"
    if not ext and base.startswith(".") and len(base) > 1:
        ext = base
        base = default

    # Remove control characters and illegal filename characters: <>:"/\|?* and null byte
    base = re.sub(r'[\x00-\x1f\x7f<>:"/\\|?*]', "", base)
    ext = re.sub(r'[\x00-\x1f\x7f<>:"/\\|?*]', "", ext)

    # Strip dots and spaces from start/end
    base = base.strip(". ")
    if not base:
        base = default

    # Windows reserved names check
    if base.upper() in WINDOWS_RESERVED_NAMES:
        base = f"_{base}"

    # Truncate to max_length while leaving room for extension
    max_base_len = max(10, max_length - len(ext))
    base = base[:max_base_len].strip(". ")

    return f"{base}{ext}"


def parse_time_str(time_str: Any) -> int | None:
    """Parse a time string (HH:MM:SS or MM:SS or seconds) into integer seconds."""
    if time_str is None:
        return None
    if isinstance(time_str, (int, float)):
        return max(0, int(time_str))

    s = str(time_str).strip()
    if not s:
        return None

    if s.isdigit():
        return int(s)

    parts = s.split(":")
    try:
        if len(parts) == 3:
            h, m, sec = parts
            return int(h) * 3600 + int(m) * 60 + int(float(sec))
        elif len(parts) == 2:
            m, sec = parts
            return int(m) * 60 + int(float(sec))
        elif len(parts) == 1:
            return int(float(parts[0]))
    except (ValueError, TypeError):
        return None
    return None


def format_time_str(seconds: float | None) -> str:
    """Format seconds into HH:MM:SS or MM:SS string."""
    if seconds is None or seconds < 0:
        return "--:--"
    total_seconds = int(seconds)
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    secs = total_seconds % 60
    if hours > 0:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def format_bytes(num_bytes: float | None) -> str:
    """Format byte count into human-readable string (KiB, MiB, GiB)."""
    if num_bytes is None or num_bytes < 0:
        return "Unknown size"
    for unit in ["B", "KiB", "MiB", "GiB", "TiB"]:
        if abs(num_bytes) < 1024.0:
            return f"{num_bytes:.1f} {unit}"
        num_bytes /= 1024.0
    return f"{num_bytes:.1f} PiB"


def check_disk_space(required_bytes: int = 0) -> tuple[bool, str]:
    """Verify that there is sufficient free disk space in the download directory."""
    try:
        usage = shutil.disk_usage(DOWNLOAD_DIR)
        min_free_bytes = MIN_FREE_DISK_SPACE_MB * 1024 * 1024
        needed = min_free_bytes + max(0, required_bytes)
        if usage.free < needed:
            avail_mb = usage.free // (1024 * 1024)
            need_mb = needed // (1024 * 1024)
            return False, f"Insufficient disk space: {avail_mb} MB available, requires at least {need_mb} MB."
        return True, ""
    except Exception as e:
        return False, f"Unable to check disk space: {e}"


class MetadataCache:
    """Thread-safe in-memory cache for media metadata with TTL and size eviction."""

    def __init__(self, ttl: int = METADATA_CACHE_TTL, max_size: int = METADATA_CACHE_MAX_SIZE):
        self.ttl = ttl
        self.max_size = max_size
        self._cache: OrderedDict[str, tuple[float, dict[str, Any]]] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, url: str) -> dict[str, Any] | None:
        with self._lock:
            if url not in self._cache:
                return None
            ts, data = self._cache[url]
            if time.time() - ts > self.ttl:
                del self._cache[url]
                return None
            # Move to end (LRU)
            self._cache.move_to_end(url)
            return data

    def set(self, url: str, data: dict[str, Any]) -> None:
        with self._lock:
            # Clean expired first
            now = time.time()
            expired_keys = [k for k, (ts, _) in self._cache.items() if now - ts > self.ttl]
            for k in expired_keys:
                del self._cache[k]

            if len(self._cache) >= self.max_size:
                # Evict oldest
                self._cache.popitem(last=False)

            self._cache[url] = (now, data)
            self._cache.move_to_end(url)

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._cache)


# Global metadata cache instance
metadata_cache = MetadataCache()


def cleanup_downloads(
    download_dir: str = DOWNLOAD_DIR,
    ttl_seconds: int = DOWNLOAD_TTL,
    active_files: set[str] | None = None
) -> int:
    """Clean up completed or stale temporary files older than TTL.

    active_files contains absolute file paths that must NOT be removed.
    Returns the number of files removed.
    """
    if not os.path.exists(download_dir):
        return 0

    active = active_files or set()
    now = time.time()
    removed_count = 0

    try:
        for entry in os.scandir(download_dir):
            if entry.is_dir():
                continue
            path = entry.path
            if path in active:
                continue

            try:
                mtime = entry.stat().st_mtime
                age = now - mtime
                # Temporary download fragments (.part, .ytdl) older than 15 mins
                is_temp_fragment = entry.name.endswith((".part", ".ytdl", ".temp"))
                if is_temp_fragment and age > 900 or age > ttl_seconds:
                    os.remove(path)
                    removed_count += 1
            except (OSError, FileNotFoundError):
                pass
    except Exception:
        pass

    return removed_count


def friendly_error_message(err_str: str) -> str:
    """Translate raw yt-dlp error strings into safe, user-friendly messages.

    Removes any internal system paths to prevent information leakage.
    """
    if not err_str:
        return "An unknown error occurred."

    clean_err = str(err_str)
    # Strip internal paths
    clean_err = re.sub(r'[A-Za-z]:\\[^:\n]+', '[server-path]', clean_err)
    clean_err = re.sub(r'/(?:home|app|tmp|var|usr)/[^\s:\n]+', '[server-path]', clean_err)

    low = clean_err.lower()

    if "unsupported url" in low:
        return "This URL is not supported by yt-dlp."
    if "private video" in low or "this video is private" in low:
        return "This video is private."
    if "video unavailable" in low or "this video is unavailable" in low:
        return "This video is unavailable or has been removed."
    if "sign in" in low or "login" in low or "authentication" in low:
        return "Authentication required by the platform to access this content."
    if "http error 403" in low or "forbidden" in low:
        return "Access denied (HTTP 403) by the media platform."
    if "http error 404" in low or "not found" in low:
        return "Media not found (HTTP 404)."
    if "http error 429" in low or "too many requests" in low or "rate-limit" in low or "rate limit" in low:
        return "Rate limit reached. Please wait a few minutes before trying again."
    if "copyright" in low:
        return "This content is blocked due to a copyright claim."
    if "geo" in low or "region" in low or "not available in your country" in low:
        return "This media is geographically restricted."
    if "timed out" in low or "timeout" in low:
        return "Connection timed out while fetching media. Please try again."
    if "ffmpeg" in low and ("not found" in low or "missing" in low):
        return "Server error: ffmpeg is required for this operation but is not installed."
    if "no space left" in low or "disk full" in low:
        return "Server storage is full. Please contact the administrator."
    if "extractor" in low and "failed" in low:
        return "Extractor failure: the platform may have changed its structure."

    # Return last line of error if multiple lines, truncated safely
    lines = [line.strip() for line in clean_err.splitlines() if line.strip()]
    if lines:
        last_line = lines[-1]
        if last_line.startswith("ERROR:"):
            last_line = last_line[6:].strip()
        if len(last_line) > 120:
            return last_line[:117] + "..."
        return last_line

    return "Download failed."
