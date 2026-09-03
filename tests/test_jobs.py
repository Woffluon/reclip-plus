import queue
from unittest.mock import patch

import pytest

from reclip.jobs import Job, JobManager


class TestJobModel:
    def test_job_initial_state(self):
        job = Job(
            job_id="0123456789",
            url="https://example.com/video",
            format_type="video",
            video_quality="best",
            title="My Video",
            uploader="Creator",
        )
        assert job.id == "0123456789"
        assert job.status == "queued"
        assert job.title == "My Video"
        assert job.uploader == "Creator"

    def test_get_safe_filename(self):
        job = Job(
            job_id="0123456789",
            url="https://example.com/video",
            title="Episode 1: The Beginning",
            uploader="ShowName",
            video_quality="1080",
            options={"filename_strategy": "channel_title"},
        )
        filename = job.get_safe_filename(".mp4")
        assert filename == "ShowName - Episode 1 The Beginning.mp4"

        # Title + res strategy
        job.options["filename_strategy"] = "title_res"
        assert job.get_safe_filename(".mp4") == "Episode 1 The Beginning [1080p].mp4"

        # Title + ID strategy
        job.options["filename_strategy"] = "title_id"
        assert job.get_safe_filename(".mp4") == "Episode 1 The Beginning [0123456789].mp4"

    def test_listeners_broadcast(self):
        job = Job(
            job_id="0123456789",
            url="https://example.com/video",
        )
        q = queue.Queue()
        job.add_listener(q)
        job.update_progress({"percent": 50.0, "stage": "downloading"})
        assert job.status == "downloading"

        item = q.get_nowait()
        assert item["id"] == "0123456789"
        assert item["status"] == "downloading"
        assert item["progress"]["percent"] == 50.0

        job.remove_listener(q)

    def test_listener_queue_full_drops_oldest(self):
        job = Job(job_id="0123456789", url="https://example.com/video")
        q = queue.Queue(maxsize=1)
        job.add_listener(q)

        # First broadcast fills queue
        job.update_progress({"percent": 10.0})
        # Second broadcast should drop oldest and put newest
        job.update_progress({"percent": 20.0})

        item = q.get_nowait()
        assert item["progress"]["percent"] == 20.0
        assert q.empty()
        job.remove_listener(q)


class TestJobManager:
    @pytest.fixture
    def manager(self):
        return JobManager(max_concurrent=2)

    @patch("reclip.jobs.is_safe_url", return_value=(True, ""))
    @patch("reclip.jobs.check_disk_space", return_value=(True, ""))
    def test_create_and_queue_job(self, mock_disk, mock_url, manager):
        # Prevent auto worker thread start in test
        with patch.object(manager, "_process_queue"):
            job = manager.create_job(
                url="https://example.com/video",
                format_type="video",
                title="Test",
            )
            assert job.id in manager.jobs
            assert job.id in manager.waiting_queue
            assert job.status == "queued"

    @patch("reclip.jobs.is_safe_url", return_value=(True, ""))
    @patch("reclip.jobs.check_disk_space", return_value=(True, ""))
    def test_resource_guards(self, mock_disk, mock_url, manager):
        with patch("reclip.jobs.MAX_DURATION", 300):
            with pytest.raises(ValueError, match="exceeds maximum allowed duration"):
                manager.create_job(url="https://example.com/video", duration=600)

        with patch("reclip.jobs.MAX_ESTIMATED_FILE_SIZE", 50000):
            with pytest.raises(ValueError, match="exceeds maximum allowed limit"):
                manager.create_job(url="https://example.com/video", filesize=100000)

    @patch("reclip.jobs.is_safe_url", return_value=(True, ""))
    @patch("reclip.jobs.check_disk_space", return_value=(True, ""))
    def test_cancel_queued_job(self, mock_disk, mock_url, manager):
        with patch.object(manager, "_process_queue"):
            job = manager.create_job(url="https://example.com/video")
            assert job.id in manager.waiting_queue

            ok = manager.cancel_job(job.id)
            assert ok is True
            assert job.status == "cancelled"
            assert job.id not in manager.waiting_queue

    @patch("reclip.jobs.is_safe_url", return_value=(True, ""))
    @patch("reclip.jobs.check_disk_space", return_value=(True, ""))
    def test_retry_job(self, mock_disk, mock_url, manager):
        with patch.object(manager, "_process_queue"):
            job = manager.create_job(
                url="https://example.com/video",
                video_quality="custom_fmt_123",
            )
            manager.cancel_job(job.id)
            assert job.status == "cancelled"

            # Attempt 1
            retry_ok = manager.retry_job(job.id)
            assert retry_ok is True
            assert job.status == "queued"
            assert job.retries == 1

            # Simulate failure & Attempt 2 (smart format fallback to 'best')
            job.status = "error"
            retry_ok_2 = manager.retry_job(job.id)
            assert retry_ok_2 is True
            assert job.retries == 2
            assert job.video_quality == "best"

            # Exceed max retries
            job.retries = job.max_retries
            job.status = "error"
            fail_retry = manager.retry_job(job.id)
            assert fail_retry is False
            assert "Maximum retry limit" in job.error

    @patch("reclip.jobs.is_safe_url", return_value=(True, ""))
    @patch("reclip.jobs.check_disk_space", return_value=(True, ""))
    def test_reorder_queue(self, mock_disk, mock_url, manager):
        with patch.object(manager, "_process_queue"):
            j1 = manager.create_job(url="https://example.com/1")
            j2 = manager.create_job(url="https://example.com/2")
            j3 = manager.create_job(url="https://example.com/3")

            assert list(manager.waiting_queue) == [j1.id, j2.id, j3.id]

            # Move j3 to front
            ok = manager.reorder_queue(j3.id, 0)
            assert ok is True
            assert list(manager.waiting_queue) == [j3.id, j1.id, j2.id]

    @patch("reclip.jobs.is_safe_url", return_value=(True, ""))
    @patch("reclip.jobs.check_disk_space", return_value=(True, ""))
    def test_queue_concurrency_and_drain(self, mock_disk, mock_url, manager):
        # max_concurrent = 2
        with patch.object(manager, "_run_download_worker"):
            j1 = manager.create_job(url="https://example.com/1")
            j2 = manager.create_job(url="https://example.com/2")
            j3 = manager.create_job(url="https://example.com/3")

            assert len(manager.active_jobs) == 2
            assert j1.id in manager.active_jobs
            assert j2.id in manager.active_jobs
            assert len(manager.waiting_queue) == 1
            assert manager.waiting_queue[0] == j3.id

            # Simulate j1 completing
            with manager.lock:
                del manager.active_jobs[j1.id]
            manager._process_queue()

            # Now j3 should have moved to active_jobs
            assert len(manager.active_jobs) == 2
            assert j3.id in manager.active_jobs
            assert len(manager.waiting_queue) == 0

    @patch("reclip.jobs.is_safe_url", return_value=(True, ""))
    @patch("reclip.jobs.check_disk_space", return_value=(True, ""))
    def test_remove_job(self, mock_disk, mock_url, manager):
        with patch.object(manager, "_process_queue"):
            job = manager.create_job(url="https://example.com/video")
            assert job.id in manager.jobs

            ok = manager.remove_job(job.id)
            assert ok is True
            assert job.id not in manager.jobs
