
from reclip.downloader import (
    build_download_args,
    build_ytdlp_cli_command,
    parse_progress_line,
    parse_ytdlp_json,
)


class TestDownloadArgBuilding:
    def test_video_best_quality(self):
        cmd, out_template = build_download_args(
            job_id="test123456",
            url="https://example.com/video",
            format_type="video",
            video_quality="best",
        )
        assert "yt-dlp" in cmd
        assert "--no-playlist" in cmd
        assert "-f" in cmd
        f_idx = cmd.index("-f")
        assert cmd[f_idx + 1] == "bestvideo+bestaudio/best"
        assert "--merge-output-format" in cmd
        merge_idx = cmd.index("--merge-output-format")
        assert cmd[merge_idx + 1] == "mp4"
        assert cmd[-1] == "https://example.com/video"

    def test_video_balanced_quality(self):
        cmd, _ = build_download_args(
            job_id="test123456",
            url="https://example.com/video",
            format_type="video",
            video_quality="balanced",
        )
        assert "-f" in cmd
        f_idx = cmd.index("-f")
        assert "avc1" in cmd[f_idx + 1]

    def test_video_fastest_single_stream(self):
        cmd, _ = build_download_args(
            job_id="test123456",
            url="https://example.com/video",
            format_type="video",
            video_quality="fastest",
        )
        assert "-f" in cmd
        f_idx = cmd.index("-f")
        assert "b[ext=mp4]/b/best" in cmd[f_idx + 1]

    def test_video_custom_format(self):
        cmd, _ = build_download_args(
            job_id="test123456",
            url="https://example.com/video",
            format_type="video",
            video_quality="137",
        )
        assert "-f" in cmd
        f_idx = cmd.index("-f")
        assert "137+bestaudio/best" == cmd[f_idx + 1]

    def test_video_resolution_presets(self):
        cmd_1080, _ = build_download_args(
            job_id="test123456",
            url="https://example.com/video",
            format_type="video",
            video_quality="1080",
        )
        f_idx = cmd_1080.index("-f")
        assert "bv*[height<=1080]+ba/b[height<=1080]/best" in cmd_1080[f_idx + 1]

        cmd_720, _ = build_download_args(
            job_id="test123456",
            url="https://example.com/video",
            format_type="video",
            video_quality="720p",
        )
        f_idx = cmd_720.index("-f")
        assert "bv*[height<=720]+ba/b[height<=720]/best" in cmd_720[f_idx + 1]

    def test_audio_downloads_only_audio_stream(self):
        cmd, _ = build_download_args(
            job_id="test123456",
            url="https://example.com/audio",
            format_type="audio",
            audio_quality="original",
        )
        assert "-f" in cmd
        f_idx = cmd.index("-f")
        assert "ba/bestaudio/best" in cmd[f_idx + 1]
        assert "-x" in cmd
        assert "--audio-format" not in cmd  # Stream copy

    def test_subtitles_only_download(self):
        cmd, _ = build_download_args(
            job_id="test123456",
            url="https://example.com/video",
            format_type="subtitles",
            options={"sub_lang": "es", "auto_subs": True},
        )
        assert "--skip-download" in cmd
        assert "--write-subs" in cmd
        assert "--sub-langs" in cmd
        s_idx = cmd.index("--sub-langs")
        assert cmd[s_idx + 1] == "es"
        assert "--convert-subs" in cmd
        c_idx = cmd.index("--convert-subs")
        assert cmd[c_idx + 1] == "srt"
        assert "--write-auto-subs" in cmd

    def test_invalid_time_range_raises_error(self):
        import pytest
        with pytest.raises(ValueError, match="must be greater than start time"):
            build_download_args(
                job_id="test123456",
                url="https://example.com/video",
                options={"start_time": "02:30", "end_time": "01:00"},
            )

    def test_split_chapters_flag(self):
        cmd, _ = build_download_args(
            job_id="test123456",
            url="https://example.com/video",
            options={"split_chapters": True},
        )
        assert "--split-chapters" in cmd

    def test_audio_mp3_bitrate(self):
        cmd, _ = build_download_args(
            job_id="test123456",
            url="https://example.com/audio",
            format_type="audio",
            audio_quality="320",
        )
        assert "-x" in cmd
        assert "--audio-format" in cmd
        fmt_idx = cmd.index("--audio-format")
        assert cmd[fmt_idx + 1] == "mp3"
        assert "--audio-quality" in cmd
        q_idx = cmd.index("--audio-quality")
        assert cmd[q_idx + 1] == "320K"

    def test_time_sections_clipping(self):
        cmd, _ = build_download_args(
            job_id="test123456",
            url="https://example.com/video",
            options={"start_time": "01:00", "end_time": "02:30"},
        )
        assert "--download-sections" in cmd
        sec_idx = cmd.index("--download-sections")
        assert cmd[sec_idx + 1] == "*1:00-2:30"
        assert "--force-keyframes-at-cuts" in cmd

    def test_subtitles_and_embeds(self):
        cmd, _ = build_download_args(
            job_id="test123456",
            url="https://example.com/video",
            options={
                "sub_lang": "en",
                "embed_subs": True,
                "auto_subs": True,
                "embed_metadata": True,
                "embed_thumbnail": True,
                "embed_chapters": True,
            },
        )
        assert "--write-subs" in cmd
        assert "--sub-langs" in cmd
        assert "--embed-subs" in cmd
        assert "--write-auto-subs" in cmd
        assert "--embed-metadata" in cmd
        assert "--embed-thumbnail" in cmd
        assert "--embed-chapters" in cmd


class TestCliCommandGenerator:
    def test_build_ytdlp_cli_command(self):
        cli = build_ytdlp_cli_command(
            url="https://example.com/test video",
            format_type="video",
            video_quality="best",
            options={"start_time": "00:30", "end_time": "01:00"},
            filename_strategy="channel_title",
        )
        assert cli.startswith("yt-dlp")
        assert "-o" in cli
        assert "%(uploader)s - %(title)s.%(ext)s" in cli
        assert "--download-sections" in cli
        assert "https://example.com/test video" in cli
        # Confirm no internal server paths leak
        assert "/app/" not in cli
        assert "C:\\" not in cli


class TestProgressParsing:
    def test_parse_custom_progress_template(self):
        line = "download:[RECLIP_PROGRESS] 45.2%|12.5MiB|28.0MiB|3.4MiB/s|00:05"
        res = parse_progress_line(line)
        assert res is not None
        assert res["stage"] == "downloading"
        assert res["percent"] == 45.2
        assert res["downloaded_str"] == "12.5MiB"
        assert res["total_str"] == "28.0MiB"
        assert res["speed_str"] == "3.4MiB/s"
        assert res["eta_str"] == "00:05"

    def test_parse_standard_ytdlp_line(self):
        line = "[download]  65.0% of ~  20.00MiB at    2.50MiB/s ETA 00:03"
        res = parse_progress_line(line)
        assert res is not None
        assert res["stage"] == "downloading"
        assert res["percent"] == 65.0
        assert res["total_str"] == "20.00MiB"
        assert res["speed_str"] == "2.50MiB/s"
        assert res["eta_str"] == "00:03"

    def test_parse_merger_and_postprocess(self):
        line1 = "[Merger] Merging formats into 'output.mp4'"
        res1 = parse_progress_line(line1)
        assert res1 is not None
        assert res1["stage"] == "merging"

        line2 = "[ExtractAudio] Destination: output.mp3"
        res2 = parse_progress_line(line2)
        assert res2 is not None
        assert res2["stage"] == "extracting_audio"


class TestParseYtdlpJson:
    def test_single_json(self):
        stdout = '{"id": "abc", "title": "Test Title"}\n'
        data = parse_ytdlp_json(stdout)
        assert data["id"] == "abc"
        assert data["title"] == "Test Title"

    def test_multiline_json(self):
        stdout = '{"id": "first", "title": "First"}\n{"id": "second", "title": "Second"}\n'
        data = parse_ytdlp_json(stdout)
        assert data["id"] == "first"
