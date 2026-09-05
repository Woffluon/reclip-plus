import os
import threading
import time
from unittest.mock import patch
import pytest
from werkzeug.serving import make_server
from playwright.sync_api import sync_playwright

from reclip.app import app
from reclip.jobs import job_manager
from reclip.utils import DOWNLOAD_DIR


@pytest.fixture(scope="function")
def live_server():
    """Run Flask live server on an ephemeral loopback port for Playwright tests."""
    app.config["TESTING"] = True
    server = make_server("127.0.0.1", 0, app, threaded=True)
    port = server.port
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    base_url = f"http://127.0.0.1:{port}"
    yield base_url
    server.shutdown()
    try:
        server.server_close()
    except Exception:
        pass


@pytest.fixture(autouse=True)
def clean_job_manager_state():
    """Ensure job_manager queues are clean before and after each test."""
    with job_manager.lock:
        job_manager.active_jobs.clear()
        job_manager.waiting_queue.clear()
        job_manager.jobs.clear()
    yield
    with job_manager.lock:
        job_manager.active_jobs.clear()
        job_manager.waiting_queue.clear()
        job_manager.jobs.clear()


class TestPlaywrightUI:
    """Playwright E2E browser tests for user experience, console logging, and download flows."""

    def test_page_load_and_console_logging(self, live_server):
        """Verify page loads cleanly, structured [ReClip] logs are emitted, and no unhandled errors occur."""
        console_messages = []
        page_errors = []

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()

            page.on("console", lambda msg: console_messages.append({
                "type": msg.type,
                "text": msg.text,
            }))
            page.on("pageerror", lambda err: page_errors.append(str(err)))

            page.goto(live_server)
            page.wait_for_selector(".brand h1")

            # Check branding and title
            assert "ReClip" in page.title()
            brand_text = page.locator(".brand h1").inner_text()
            assert "ReClip" in brand_text

            # Verify no unhandled JavaScript errors occurred
            assert len(page_errors) == 0, f"Unhandled JS errors detected: {page_errors}"

            # Verify structured [ReClip] logs exist in console
            reclip_logs = [m["text"] for m in console_messages if "[ReClip]" in m["text"]]
            assert len(reclip_logs) > 0, "No [ReClip] console logs found!"
            assert any("Initializing ReClip Plus" in log for log in reclip_logs)
            assert any("initialized successfully" in log for log in reclip_logs)

            # Verify window.ReClipLogger is accessible and stores buffer
            logger_exists = page.evaluate("typeof window.ReClipLogger !== 'undefined'")
            assert logger_exists is True

            log_buffer = page.evaluate("window.ReClipLogger.getBuffer()")
            assert len(log_buffer) > 0
            assert any(entry["level"] == "INFO" for entry in log_buffer)

            # Verify logger methods and format-string safety (e.g. % tokens in message)
            page.evaluate("window.ReClipLogger.info('Format check: 100% complete and %s safe')")
            buffer_after = page.evaluate("window.ReClipLogger.getBuffer()")
            assert any("100% complete" in entry["message"] for entry in buffer_after)

            # Test setLevel and clearBuffer
            page.evaluate("window.ReClipLogger.setLevel('WARN')")
            level = page.evaluate("window.ReClipLogger.getLevel()")
            assert level == "WARN"

            page.evaluate("window.ReClipLogger.clearBuffer()")
            cleared_buffer = page.evaluate("window.ReClipLogger.getBuffer()")
            assert len(cleared_buffer) == 0

            browser.close()

    def test_ui_controls_theme_presets_and_stats(self, live_server):
        """Verify theme toggling, URL input stats, preset selection, ready card syncing, and advanced options."""
        page_errors = []

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.on("pageerror", lambda err: page_errors.append(str(err)))
            page.goto(live_server)

            # 1. Theme toggle
            theme_btn = page.locator("#themeBtn")
            assert "Auto" in theme_btn.inner_text()
            theme_btn.click()
            assert page.locator("html").get_attribute("data-theme") == "dark"
            theme_btn.click()
            assert page.locator("html").get_attribute("data-theme") is None

            # 2. URL input stats
            urls_input = page.locator("#urls")
            urls_input.fill("https://example.com/watch?v=123")
            stats_text = page.locator("#urlStats").inner_text()
            assert "1 URL detected" in stats_text

            # Multiple URLs with duplicate and non-URL tokens
            urls_input.fill("https://example.com/watch?v=123\nhttps://example.com/watch?v=123\nhttps://example.com/watch?v=456\ninvalid-line")
            stats_text = page.locator("#urlStats").inner_text()
            assert "2 URLs detected" in stats_text
            assert "1 duplicate removed" in stats_text
            assert "1 non-URL item ignored" in stats_text

            # 3. Presets and ready card format syncing
            info_mock_data = {
                "title": "Preset Sync Test Video",
                "uploader": "Test Channel",
                "duration": 60,
                "thumbnail": "https://example.com/thumb.jpg",
                "formats": [{"id": "best", "label": "1080p MP4"}],
                "chapters": [],
                "subtitles": [],
            }
            with patch("reclip.app.get_media_info", return_value=info_mock_data):
                urls_input.fill("https://example.com/watch?v=test_preset_sync")
                page.locator("#fetchBtn").click()
                page.wait_for_selector("#card-0 .thumb-badge.thumb-type", timeout=10000)
                badge_text = page.locator("#card-0 .thumb-badge.thumb-type").inner_text()
                assert "VIDEO" in badge_text

                # Switch to Audio preset; ready card should reactively update to AUDIO
                preset_audio = page.locator('.preset-chip[data-preset="mp3-320"]')
                preset_audio.click()
                audio_row = page.locator("#audioStrategyRow")
                assert audio_row.is_visible()
                active_audio = page.locator('#audioStrategyRow .strat-btn.active')
                assert "320" in active_audio.get_attribute("data-quality")
                updated_badge = page.locator("#card-0 .thumb-badge.thumb-type").inner_text()
                assert "AUDIO" in updated_badge

            # 4. Advanced panel accordion
            adv_toggle = page.locator("#advToggle")
            adv_panel = page.locator("#advPanel")
            assert not adv_panel.is_visible() or "open" not in (adv_panel.get_attribute("class") or "")
            adv_toggle.click()
            assert "open" in adv_panel.get_attribute("class")

            # 5. Clear button
            clear_btn = page.locator("#clearBtn")
            clear_btn.click()
            assert urls_input.input_value() == ""
            assert page.locator("#urlStats").inner_text() == ""
            assert page.locator("#cards").inner_text() == ""

            assert len(page_errors) == 0
            browser.close()

    def test_clean_download_on_first_try(self, live_server, tmp_path):
        """Verify fetching metadata, starting download, real-time SSE progress, and automatic download on first try."""
        page_errors = []
        console_messages = []

        test_video_content = b"RIFF....WAVEfmt ....fake-media-content-for-testing-first-try-download"
        os.makedirs(DOWNLOAD_DIR, exist_ok=True)

        def mock_worker(job):
            """Simulate background download worker that emits SSE progress and creates test file."""
            out_file = os.path.join(DOWNLOAD_DIR, f"{job.id}.mp4")
            with open(out_file, "wb") as f:
                f.write(test_video_content)

            try:
                job.status = "downloading"
                job.progress["percent"] = 45.0
                job.progress["speed_str"] = "4.2MiB/s"
                job.progress["eta_str"] = "00:02"
                job.progress["status_msg"] = "Downloading video stream..."
                job.broadcast()

                time.sleep(0.3)

                job.file_path = out_file
                job.filesize = len(test_video_content)
                job.filename = "Blender Open Movie - Sintel.mp4"
                job.status = "done"
                job.progress["percent"] = 100.0
                job.progress["stage"] = "done"
                job.progress["status_msg"] = "Download complete."
                job.broadcast()
            finally:
                with job_manager.lock:
                    if job.id in job_manager.active_jobs:
                        del job_manager.active_jobs[job.id]

        info_mock_data = {
            "title": "Blender Open Movie - Sintel",
            "uploader": "Blender Foundation",
            "duration": 52,
            "thumbnail": "https://example.com/sintel.jpg",
            "formats": [
                {"id": "best", "label": "1080p MP4"},
                {"id": "720", "label": "720p MP4"},
            ],
            "chapters": [],
            "subtitles": [],
        }

        with patch("reclip.app.get_media_info", return_value=info_mock_data), \
             patch.object(job_manager, "_run_download_worker", side_effect=mock_worker):

            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                context = browser.new_context(accept_downloads=True)
                page = context.new_page()

                page.on("console", lambda msg: console_messages.append(msg.text))
                page.on("pageerror", lambda err: page_errors.append(str(err)))

                try:
                    page.goto(live_server)

                    # 1. Fill URL and fetch info
                    urls_input = page.locator("#urls")
                    urls_input.fill("https://www.youtube.com/watch?v=mock_sintel_test")

                    fetch_btn = page.locator("#fetchBtn")
                    fetch_btn.click()

                    # 2. Wait for card to be rendered with metadata
                    card_title = page.locator("#card-0 .card-title")
                    card_title.wait_for(state="visible", timeout=10000)
                    assert card_title.inner_text() == "Blender Open Movie - Sintel"

                    card_meta = page.locator("#card-0 .card-meta")
                    assert "BLENDER FOUNDATION" in card_meta.inner_text().upper()

                    # 3. Trigger Download and assert download event fires cleanly on first try
                    download_btn = page.locator('#card-0 button:has-text("Download")')
                    download_btn.wait_for(state="visible", timeout=5000)

                    # Expect browser download event triggered automatically upon job completion
                    with page.expect_download(timeout=15000) as download_info:
                        download_btn.click()

                    # 4. Verify downloaded file properties
                    download = download_info.value
                    suggested_filename = download.suggested_filename
                    assert suggested_filename == "Blender Open Movie - Sintel.mp4", f"Expected filename mismatch: {suggested_filename}"

                    # Save downloaded file and verify contents
                    downloaded_target = tmp_path / suggested_filename
                    download.save_as(str(downloaded_target))
                    assert os.path.exists(downloaded_target)
                    assert os.path.getsize(downloaded_target) == len(test_video_content)
                    assert downloaded_target.read_bytes() == test_video_content

                    # 5. Verify UI state: Completed progress bar, filename status, Save File button
                    page.wait_for_selector('#card-0 .progress-bar-fill.done', timeout=5000)
                    save_btn = page.locator('#card-0 button:has-text("Save File")')
                    assert save_btn.is_visible()

                    # 6. Verify manual re-download on clicking "Save File"
                    with page.expect_download(timeout=10000) as second_download_info:
                        save_btn.click()

                    second_download = second_download_info.value
                    assert second_download.suggested_filename == "Blender Open Movie - Sintel.mp4"

                    # 7. Verify History entry
                    page.wait_for_selector(".history-card", timeout=5000)
                    history_name = page.locator(".history-name").first.inner_text()
                    assert "Blender Open Movie - Sintel" in history_name

                    # 8. Check console logs for full audit trail
                    reclip_logs = [log for log in console_messages if "[ReClip]" in log]
                    assert any("download job" in entry.lower() for entry in reclip_logs)
                    assert any("completed successfully" in entry.lower() for entry in reclip_logs)
                    assert any("triggering" in entry.lower() and "browser" in entry.lower() for entry in reclip_logs)

                    assert len(page_errors) == 0, f"Unexpected page errors: {page_errors}"
                finally:
                    context.close()
                    browser.close()

    def test_download_error_handling_and_retry(self, live_server):
        """Verify error state when download worker fails, and clean recovery via Retry button."""
        page_errors = []
        console_messages = []
        test_video_content = b"RECLIP-TEST-RETRY-STREAM-DATA"
        os.makedirs(DOWNLOAD_DIR, exist_ok=True)

        def mock_failing_worker(job):
            try:
                if job.retries == 0:
                    job.status = "downloading"
                    job.progress["percent"] = 15.0
                    job.broadcast()
                    time.sleep(0.2)
                    job.status = "error"
                    job.error = "Simulated upstream CDN connection timeout"
                    job.broadcast()
                else:
                    out_file = os.path.join(DOWNLOAD_DIR, f"{job.id}.mp4")
                    with open(out_file, "wb") as f:
                        f.write(test_video_content)
                    job.file_path = out_file
                    job.filesize = len(test_video_content)
                    job.filename = "Retry Recovery Video.mp4"
                    job.status = "done"
                    job.progress["percent"] = 100.0
                    job.progress["stage"] = "done"
                    job.broadcast()
            finally:
                with job_manager.lock:
                    if job.id in job_manager.active_jobs:
                        del job_manager.active_jobs[job.id]

        info_mock_data = {
            "title": "Retry Test Video",
            "uploader": "Test Channel",
            "duration": 42,
            "thumbnail": "",
            "formats": [{"id": "best", "label": "1080p MP4"}],
            "chapters": [],
            "subtitles": [],
        }

        with patch("reclip.app.get_media_info", return_value=info_mock_data), \
             patch.object(job_manager, "_run_download_worker", side_effect=mock_failing_worker):

            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                context = browser.new_context(accept_downloads=True)
                page = context.new_page()
                page.on("console", lambda msg: console_messages.append(msg.text))
                page.on("pageerror", lambda err: page_errors.append(str(err)))

                page.goto(live_server)
                urls_input = page.locator("#urls")
                urls_input.fill("https://example.com/watch?v=failing_retry_video")
                page.locator("#fetchBtn").click()

                card_title = page.locator("#card-0 .card-title")
                card_title.wait_for(state="visible", timeout=10000)

                # Start first attempt
                download_btn = page.locator('#card-0 button:has-text("Download")')
                download_btn.click()

                # Card should transition to error state
                retry_btn = page.locator('#card-0 button:has-text("Retry")')
                retry_btn.wait_for(state="visible", timeout=10000)
                error_text = page.locator("#card-0 .card-status-text.error").inner_text()
                assert "Simulated upstream CDN connection timeout" in error_text

                # Verify [ERROR] log was emitted
                reclip_logs = [m for m in console_messages if "[ReClip]" in m]
                assert any("[ERROR]" in m and "failure" in m.lower() for m in reclip_logs)

                # Click Retry and expect download to succeed on retry
                with page.expect_download(timeout=15000) as download_info:
                    retry_btn.click()

                download = download_info.value
                assert download.suggested_filename == "Retry Recovery Video.mp4"

                # Verify Save File button appears
                save_btn = page.locator('#card-0 button:has-text("Save File")')
                assert save_btn.is_visible()

                assert len(page_errors) == 0
                context.close()
                browser.close()

    def test_polling_fallback_download(self, live_server):
        """Verify automatic download works cleanly via HTTP polling fallback when EventSource is unavailable."""
        page_errors = []
        console_messages = []
        test_video_content = b"RECLIP-FALLBACK-POLLING-MEDIA-STREAM"
        os.makedirs(DOWNLOAD_DIR, exist_ok=True)

        def mock_polling_worker(job):
            out_file = os.path.join(DOWNLOAD_DIR, f"{job.id}.mp4")
            with open(out_file, "wb") as f:
                f.write(test_video_content)
            try:
                job.status = "downloading"
                job.progress["percent"] = 50.0
                time.sleep(0.3)
                job.file_path = out_file
                job.filesize = len(test_video_content)
                job.filename = "Polling Stream Media.mp4"
                job.status = "done"
                job.progress["percent"] = 100.0
            finally:
                with job_manager.lock:
                    if job.id in job_manager.active_jobs:
                        del job_manager.active_jobs[job.id]

        info_mock_data = {
            "title": "Polling Fallback Media",
            "uploader": "Test Channel",
            "duration": 30,
            "thumbnail": "",
            "formats": [{"id": "best", "label": "1080p MP4"}],
            "chapters": [],
            "subtitles": [],
        }

        with patch("reclip.app.get_media_info", return_value=info_mock_data), \
             patch.object(job_manager, "_run_download_worker", side_effect=mock_polling_worker):

            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                context = browser.new_context(accept_downloads=True)
                # Remove EventSource to force HTTP polling fallback
                context.add_init_script("delete window.EventSource;")
                page = context.new_page()
                page.on("console", lambda msg: console_messages.append(msg.text))
                page.on("pageerror", lambda err: page_errors.append(str(err)))

                page.goto(live_server)
                urls_input = page.locator("#urls")
                urls_input.fill("https://example.com/watch?v=polling_fallback_video")
                page.locator("#fetchBtn").click()

                card_title = page.locator("#card-0 .card-title")
                card_title.wait_for(state="visible", timeout=10000)

                download_btn = page.locator('#card-0 button:has-text("Download")')
                download_btn.wait_for(state="visible", timeout=5000)

                with page.expect_download(timeout=15000) as download_info:
                    download_btn.click()

                download = download_info.value
                assert download.suggested_filename == "Polling Stream Media.mp4"

                # Check that fallback polling was logged
                reclip_logs = [m for m in console_messages if "[ReClip]" in m]
                assert any("[Polling]" in m for m in reclip_logs)

                assert len(page_errors) == 0
                context.close()
                browser.close()

    def test_clear_cleans_up_connections_and_state(self, live_server):
        """Verify that Clear button properly resets inputs, removes cards, and stops connections."""
        page_errors = []
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.on("pageerror", lambda err: page_errors.append(str(err)))

            info_mock_data = {
                "title": "Clear Test Video",
                "uploader": "Channel",
                "duration": 10,
                "thumbnail": "",
                "formats": [{"id": "best", "label": "1080p MP4"}],
                "chapters": [],
                "subtitles": [],
            }

            with patch("reclip.app.get_media_info", return_value=info_mock_data):
                page.goto(live_server)
                urls_input = page.locator("#urls")
                urls_input.fill("https://example.com/watch?v=video_to_clear")
                page.locator("#fetchBtn").click()

                page.wait_for_selector("#card-0", timeout=10000)
                assert page.locator("#card-0").is_visible()

                # Click clear
                page.locator("#clearBtn").click()
                assert urls_input.input_value() == ""
                assert page.locator("#cards").inner_text() == ""

                # Check log buffer in window
                buffer = page.evaluate("window.ReClipLogger.getBuffer()")
                assert any("Clear button clicked" in entry["message"] for entry in buffer)

            assert len(page_errors) == 0
            browser.close()
