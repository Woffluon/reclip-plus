from unittest.mock import patch

import pytest

from reclip.app import app
from reclip.jobs import job_manager


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


class TestAppRoutes:
    def test_index_page(self, client):
        res = client.get("/")
        assert res.status_code == 200
        assert b"ReClip" in res.data
        assert b"Self-Hosted Media Downloader" in res.data

    def test_root_app_entrypoint_compatibility(self):
        import app as root_module
        from app import DOWNLOAD_DIR, Job, JobManager, app as root_app, job_manager, parse_ytdlp_json
        assert root_app is app
        assert job_manager is not None
        assert Job is not None
        assert JobManager is not None
        assert callable(parse_ytdlp_json)
        assert isinstance(DOWNLOAD_DIR, str)
        assert hasattr(root_module, "__all__")

    def test_reclip_package_exports(self):
        import reclip
        assert reclip.app is app
        assert reclip.job_manager is not None
        assert reclip.Job is not None
        assert reclip.JobManager is not None
        assert hasattr(reclip, "__version__")
        assert reclip.__version__ == "1.0.0"

    def test_reclip_main_cli(self, monkeypatch):
        import sys
        from unittest.mock import MagicMock
        from reclip.__main__ import main

        mock_run = MagicMock()
        monkeypatch.setattr(app, "run", mock_run)
        monkeypatch.setattr(sys, "argv", ["reclip", "--port", "9999", "--host", "127.0.0.1"])

        main()
        mock_run.assert_called_once_with(host="127.0.0.1", port=9999)


    def test_health_check(self, client):
        res = client.get("/health")
        # May be 200 (if tools exist) or 503, but must return valid JSON
        assert res.status_code in (200, 503)
        data = res.get_json()
        assert "status" in data
        assert "service" in data
        assert data["service"] == "ReClip Plus"
        assert "storage" in data
        assert "queue" in data

    def test_info_empty_url(self, client):
        res = client.post("/api/info", json={})
        assert res.status_code == 400
        data = res.get_json()
        assert "error" in data

    def test_info_ssrf_blocked(self, client):
        res = client.post("/api/info", json={"url": "http://127.0.0.1/video.mp4"})
        assert res.status_code == 400
        data = res.get_json()
        assert "prohibited" in data["error"] or "forbidden" in data["error"] or "localhost" in data["error"]

    @patch("reclip.jobs.is_safe_url", return_value=(True, ""))
    @patch("reclip.jobs.check_disk_space", return_value=(True, ""))
    @patch.object(job_manager, "_process_queue")
    def test_download_and_status_flow(self, mock_proc, mock_disk, mock_url, client):
        # 1. Start download
        res = client.post("/api/download", json={
            "url": "https://example.com/video.mp4",
            "format": "video",
            "video_quality": "best",
            "title": "Test Flow",
        })
        assert res.status_code == 201
        data = res.get_json()
        assert "job_id" in data
        job_id = data["job_id"]

        # 2. Check status
        s_res = client.get(f"/api/status/{job_id}")
        assert s_res.status_code == 200
        s_data = s_res.get_json()
        assert s_data["id"] == job_id
        assert s_data["status"] == "queued"

        # 3. Cancel job
        c_res = client.post(f"/api/cancel/{job_id}")
        assert c_res.status_code == 200
        c_data = c_res.get_json()
        assert c_data["status"] == "cancelled"

        # 4. Retry job
        r_res = client.post(f"/api/retry/{job_id}")
        assert r_res.status_code == 200
        r_data = r_res.get_json()
        assert r_data["status"] == "queued"

    def test_status_invalid_job_id(self, client):
        res = client.get("/api/status/invalid_id!@#")
        assert res.status_code == 400
        data = res.get_json()
        assert "Invalid job ID" in data["error"]

    def test_status_nonexistent_job(self, client):
        res = client.get("/api/status/0123456789")
        assert res.status_code == 404

    def test_generate_command(self, client):
        res = client.post("/api/generate-command", json={
            "url": "https://example.com/video",
            "format": "video",
            "video_quality": "best",
        })
        assert res.status_code == 200
        data = res.get_json()
        assert "command" in data
        assert "yt-dlp" in data["command"]
        assert "https://example.com/video" in data["command"]

    def test_file_download_not_ready(self, client):
        res = client.get("/api/file/0123456789")
        assert res.status_code == 404

    @patch("reclip.jobs.is_safe_url", return_value=(True, ""))
    @patch("reclip.jobs.check_disk_space", return_value=(True, ""))
    @patch.object(job_manager, "_process_queue")
    def test_sse_event_stream(self, mock_proc, mock_disk, mock_url, client):
        res = client.post("/api/download", json={
            "url": "https://example.com/video.mp4",
        })
        job_id = res.get_json()["job_id"]

        # Immediately mark done so stream yields snapshot and ends
        job = job_manager.get_job(job_id)
        job.status = "done"

        stream_res = client.get(f"/api/events/{job_id}")
        assert stream_res.status_code == 200
        assert stream_res.mimetype == "text/event-stream"
        data = stream_res.get_data(as_text=True)
        assert "data: " in data
        assert job_id in data

    @patch("reclip.jobs.is_safe_url", return_value=(True, ""))
    @patch("reclip.jobs.check_disk_space", return_value=(True, ""))
    @patch.object(job_manager, "_process_queue")
    def test_reorder_endpoint(self, mock_proc, mock_disk, mock_url, client):
        res1 = client.post("/api/download", json={"url": "https://example.com/1"})
        res2 = client.post("/api/download", json={"url": "https://example.com/2"})
        _j1 = res1.get_json()["job_id"]
        j2 = res2.get_json()["job_id"]

        reorder_res = client.post("/api/reorder", json={
            "job_id": j2,
            "new_index": 0,
        })
        assert reorder_res.status_code == 200
        assert reorder_res.get_json()["ok"] is True

    @patch("reclip.jobs.is_safe_url", return_value=(True, ""))
    @patch("reclip.jobs.check_disk_space", return_value=(True, ""))
    @patch.object(job_manager, "_process_queue")
    def test_delete_job_endpoint(self, mock_proc, mock_disk, mock_url, client):
        res = client.post("/api/download", json={"url": "https://example.com/video"})
        job_id = res.get_json()["job_id"]

        del_res = client.delete(f"/api/jobs/{job_id}")
        assert del_res.status_code == 200
        assert del_res.get_json()["ok"] is True
        assert job_manager.get_job(job_id) is None

    @patch("reclip.app.is_safe_url", return_value=(True, ""))
    @patch("reclip.app.get_playlist_info", return_value={"title": "Test Playlist", "count": 1, "items": [], "urls": []})
    def test_playlist_respects_max_limit(self, mock_pl, mock_url, client):
        with patch("reclip.app.MAX_PLAYLIST_ITEMS", 15):
            res = client.post("/api/playlist", json={"url": "https://example.com/playlist", "limit": 999})
            assert res.status_code == 200
            # Ensure get_playlist_info was called with max_items clamped to MAX_PLAYLIST_ITEMS (15)
            mock_pl.assert_called_once_with("https://example.com/playlist", max_items=15)

    @patch("reclip.jobs.is_safe_url", return_value=(True, ""))
    @patch("reclip.jobs.check_disk_space", return_value=(True, ""))
    @patch.object(job_manager, "_process_queue")
    def test_download_subtitles_format(self, mock_proc, mock_disk, mock_url, client):
        res = client.post("/api/download", json={
            "url": "https://example.com/video",
            "format": "subtitles",
            "options": {"sub_lang": "en"},
        })
        assert res.status_code == 201
        data = res.get_json()
        job = job_manager.get_job(data["job_id"])
        assert job.format_type == "subtitles"

    @patch("reclip.jobs.is_safe_url", return_value=(True, ""))
    @patch("reclip.jobs.check_disk_space", return_value=(True, ""))
    @patch.object(job_manager, "_process_queue")
    def test_download_exceeds_max_duration(self, mock_proc, mock_disk, mock_url, client):
        with patch("reclip.jobs.MAX_DURATION", 120):
            res = client.post("/api/download", json={
                "url": "https://example.com/video",
                "duration": 300,
            })
            assert res.status_code == 400
            assert "exceeds maximum allowed duration" in res.get_json()["error"]

    def test_file_delivery_safe_delete(self, client, tmp_path):
        # Create a test file
        test_file = tmp_path / "test_del.mp4"
        test_file.write_bytes(b"sample video bytes")

        from reclip.jobs import Job
        job = Job(job_id="abcdef0123", url="https://example.com/video")
        job.status = "done"
        job.file_path = str(test_file)
        job.filename = "test_del.mp4"
        job_manager.jobs[job.id] = job

        with patch("reclip.app.DOWNLOAD_DIR", str(tmp_path)), patch("reclip.app.DELETE_AFTER_DOWNLOAD", True):
            res = client.get(f"/api/file/{job.id}")
            assert res.status_code == 200
            assert res.data == b"sample video bytes"
            # Once response stream finishes and is closed, callback should have removed file
            res.close()
            assert not test_file.exists()
