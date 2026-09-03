import os
import tempfile
import time

from reclip.utils import (
    MetadataCache,
    cleanup_downloads,
    format_bytes,
    format_time_str,
    friendly_error_message,
    is_safe_url,
    parse_time_str,
    sanitize_filename,
)


class TestUrlValidationAndSSRF:
    def test_valid_public_urls(self):
        valid_urls = [
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "https://vimeo.com/76979871",
            "https://soundcloud.com/user/track",
            "http://example.com/video.mp4",
        ]
        for url in valid_urls:
            # check_dns=False to test syntactic and literal IP guards deterministically
            safe, msg = is_safe_url(url, check_dns=False)
            assert safe, f"Expected {url} to be safe, got error: {msg}"

    def test_blocked_schemes(self):
        invalid_schemes = [
            "file:///etc/passwd",
            "ftp://ftp.example.com/file.mp4",
            "gopher://gopher.example.com",
            "javascript:alert(1)",
            "data:text/html,test",
        ]
        for url in invalid_schemes:
            safe, msg = is_safe_url(url, check_dns=False)
            assert not safe
            assert "Unsupported URL scheme" in msg or "Invalid" in msg

    def test_localhost_and_loopback_rejection(self):
        blocked = [
            "http://localhost/video.mp4",
            "http://localhost:8899/api",
            "http://127.0.0.1/video.mp4",
            "http://127.0.0.2:8080/test",
            "http://[::1]/video.mp4",
            "http://sub.localhost/media",
        ]
        for url in blocked:
            safe, msg = is_safe_url(url, check_dns=False)
            assert not safe, f"Expected {url} to be rejected"

    def test_private_ip_ranges_rejection(self):
        private_ips = [
            "http://10.0.0.1/video.mp4",
            "http://172.16.0.1/stream",
            "http://172.31.255.255/video",
            "http://192.168.1.1/router",
            "http://0.0.0.0/test",
            "http://169.254.169.254/latest/meta-data/",
        ]
        for url in private_ips:
            safe, msg = is_safe_url(url, check_dns=False)
            assert not safe, f"Expected {url} to be blocked by private IP check"

    def test_cloud_metadata_endpoints(self):
        metadata_urls = [
            "http://169.254.169.254/computeMetadata/v1/",
            "http://metadata.google.internal/computeMetadata/v1/",
            "http://instance-data/latest/meta-data/",
        ]
        for url in metadata_urls:
            safe, msg = is_safe_url(url, check_dns=False)
            assert not safe

    def test_advanced_ip_encodings_rejection(self):
        # Octal, decimal, hex integer representations that resolve to loopback/private
        encodings = [
            "http://0177.0.0.1/video.mp4",
            "http://2130706433/video.mp4",
            "http://0x7f000001/video.mp4",
        ]
        for url in encodings:
            safe, msg = is_safe_url(url, check_dns=False)
            assert not safe, f"Expected encoded IP {url} to be blocked"

    def test_ipv4_mapped_ipv6_rejection(self):
        mapped = [
            "http://[::ffff:127.0.0.1]/video.mp4",
            "http://[::ffff:10.0.0.1]/video.mp4",
            "http://[::ffff:169.254.169.254]/latest/",
        ]
        for url in mapped:
            safe, msg = is_safe_url(url, check_dns=False)
            assert not safe, f"Expected mapped IPv6 {url} to be blocked"


class TestFilenameSanitization:
    def test_removes_illegal_characters(self):
        raw = 'My Video: "Episode 1" <HD> | [Special] ? * / \\.mp4'
        clean = sanitize_filename(raw)
        assert ":" not in clean
        assert '"' not in clean
        assert "<" not in clean
        assert ">" not in clean
        assert "|" not in clean
        assert "?" not in clean
        assert "*" not in clean
        assert "/" not in clean
        assert "\\" not in clean
        assert clean.endswith(".mp4")

    def test_windows_reserved_device_names(self):
        reserved = ["CON.mp4", "prn.mp3", "Aux.mkv", "NUL.webm", "com1.mp4", "lpt2.m4a"]
        for name in reserved:
            clean = sanitize_filename(name)
            base = os.path.splitext(clean)[0]
            assert base.startswith("_")

    def test_path_traversal_prevention(self):
        dangerous = "../../../../../etc/passwd"
        clean = sanitize_filename(dangerous)
        assert "/" not in clean
        assert "\\" not in clean
        assert ".." not in clean
        assert clean == "passwd"

    def test_length_truncation(self):
        long_name = "a" * 200 + ".mp4"
        clean = sanitize_filename(long_name, max_length=50)
        assert len(clean) <= 50
        assert clean.endswith(".mp4")


class TestTimeAndSizeParsing:
    def test_parse_time_str(self):
        assert parse_time_str("01:30") == 90
        assert parse_time_str("00:01:30") == 90
        assert parse_time_str("01:15:20") == 4520
        assert parse_time_str("45") == 45
        assert parse_time_str(120) == 120
        assert parse_time_str("") is None
        assert parse_time_str("invalid") is None

    def test_format_time_str(self):
        assert format_time_str(90) == "1:30"
        assert format_time_str(3665) == "1:01:05"
        assert format_time_str(0) == "0:00"
        assert format_time_str(None) == "--:--"

    def test_format_bytes(self):
        assert "1.0 MiB" in format_bytes(1024 * 1024)
        assert "500.0 KiB" in format_bytes(500 * 1024)
        assert "2.0 GiB" in format_bytes(2 * 1024 * 1024 * 1024)
        assert format_bytes(None) == "Unknown size"


class TestMetadataCache:
    def test_cache_put_get(self):
        cache = MetadataCache(ttl=10, max_size=5)
        cache.set("http://test.com/1", {"title": "Video 1"})
        assert cache.get("http://test.com/1") == {"title": "Video 1"}
        assert cache.get("http://test.com/nonexistent") is None

    def test_cache_expiration(self):
        cache = MetadataCache(ttl=0.1, max_size=5)
        cache.set("http://test.com/1", {"title": "Video 1"})
        time.sleep(0.15)
        assert cache.get("http://test.com/1") is None

    def test_cache_capacity_eviction(self):
        cache = MetadataCache(ttl=60, max_size=2)
        cache.set("http://test.com/1", {"title": "Video 1"})
        cache.set("http://test.com/2", {"title": "Video 2"})
        cache.set("http://test.com/3", {"title": "Video 3"})
        # Key 1 should have been evicted
        assert cache.get("http://test.com/1") is None
        assert cache.get("http://test.com/2") == {"title": "Video 2"}
        assert cache.get("http://test.com/3") == {"title": "Video 3"}


class TestFriendlyErrorTranslation:
    def test_translates_known_errors(self):
        assert "not supported" in friendly_error_message("ERROR: Unsupported URL: https://invalid.com")
        assert "unavailable" in friendly_error_message("ERROR: Video unavailable")
        assert "private" in friendly_error_message("ERROR: This video is private")
        assert "HTTP 403" in friendly_error_message("HTTP Error 403: Forbidden")
        assert "Rate limit" in friendly_error_message("HTTP Error 429: Too Many Requests")

    def test_scrubs_internal_paths(self):
        raw = r"ERROR: File C:\Users\Efe\Desktop\reclip-plus\downloads\123.mp4 cannot be opened"
        clean = friendly_error_message(raw)
        assert r"C:\Users" not in clean
        assert "[server-path]" in clean


class TestCleanupDownloads:
    def test_cleanup_stale_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            old_file = os.path.join(tmpdir, "old.mp4")
            with open(old_file, "w") as f:
                f.write("old data")

            # Set mtime to 2 hours ago
            past = time.time() - 7200
            os.utime(old_file, (past, past))

            new_file = os.path.join(tmpdir, "new.mp4")
            with open(new_file, "w") as f:
                f.write("new data")

            removed = cleanup_downloads(download_dir=tmpdir, ttl_seconds=3600)
            assert removed == 1
            assert not os.path.exists(old_file)
            assert os.path.exists(new_file)

    def test_cleanup_spares_active_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            active_file = os.path.join(tmpdir, "active.mp4")
            with open(active_file, "w") as f:
                f.write("active data")

            past = time.time() - 7200
            os.utime(active_file, (past, past))

            removed = cleanup_downloads(
                download_dir=tmpdir,
                ttl_seconds=3600,
                active_files={active_file},
            )
            assert removed == 0
            assert os.path.exists(active_file)
