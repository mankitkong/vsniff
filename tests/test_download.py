import os
import subprocess

from vsniff import download


def fake_ffmpeg(monkeypatch, writes=None, returncode=0, stderr=""):
    """Stand in for the ffmpeg subprocess, optionally writing output bytes."""
    seen = {}

    def run(cmd, capture_output=False, text=False):
        seen["cmd"] = cmd
        if writes is not None:
            with open(cmd[-1], "w", encoding="utf-8") as fh:
                fh.write(writes)
        return subprocess.CompletedProcess(cmd, returncode, "", stderr)

    monkeypatch.setattr(download.subprocess, "run", run)
    return seen


def test_save_subtitles_names_the_sidecar_for_jellyfin(monkeypatch, tmp_path):
    seen = fake_ffmpeg(monkeypatch, writes="1\n00:00:01,000 --> 00:00:02,000\nhi\n")
    video = str(tmp_path / "Show - S01E01 - WEBDL - 1080p.mp4")
    out = download.save_subtitles("https://x/index-f1.m3u8", "https://ref/", video, "zh")
    assert out == str(tmp_path / "Show - S01E01 - WEBDL - 1080p.zh.srt")
    assert os.path.exists(out)
    assert "https://x/index-f1.m3u8" in seen["cmd"]


def test_save_subtitles_honours_the_language_code(monkeypatch, tmp_path):
    fake_ffmpeg(monkeypatch, writes="1\n00:00:01,000 --> 00:00:02,000\nhi\n")
    video = str(tmp_path / "Show - S01E01 - WEBDL - 1080p.mp4")
    out = download.save_subtitles("https://x/s.m3u8", "https://ref/", video, "yue")
    assert out.endswith(".yue.srt")


def test_save_subtitles_cleans_up_an_empty_result(monkeypatch, tmp_path, capsys):
    fake_ffmpeg(monkeypatch, writes="", returncode=0)
    video = str(tmp_path / "Show - S01E01 - WEBDL - 1080p.mp4")
    assert download.save_subtitles("https://x/s.m3u8", "https://ref/", video, "zh") is None
    assert list(tmp_path.iterdir()) == []
    assert "subtitles skipped" in capsys.readouterr().out


def test_save_subtitles_survives_an_ffmpeg_failure(monkeypatch, tmp_path, capsys):
    fake_ffmpeg(monkeypatch, writes=None, returncode=1, stderr="Server returned 404")
    video = str(tmp_path / "Show - S01E01 - WEBDL - 1080p.mp4")
    assert download.save_subtitles("https://x/s.m3u8", "https://ref/", video, "zh") is None
    assert "404" in capsys.readouterr().out


def test_subtitle_sidecar_replaces_the_video_extension():
    assert download.subtitle_sidecar("/lib/Show - S01E01 - WEBDL - 1080p.mp4", "zh") == \
        "/lib/Show - S01E01 - WEBDL - 1080p.zh.srt"


def test_subtitle_sidecar_keeps_dots_inside_the_name():
    assert download.subtitle_sidecar("/lib/Dr. Show - S01E01 - WEBDL - 720p.mp4", "en") \
        == "/lib/Dr. Show - S01E01 - WEBDL - 720p.en.srt"


class FakePopen:
    """ffmpeg stand-in that reports progress on stdout and writes the output."""
    lines = ["out_time_us=1500000\n", "out_time_us=3000000\n", "progress=end\n"]

    def __init__(self, cmd, **kw):
        with open(cmd[-1], "wb") as fh:
            fh.write(b"\0" * 60_000)
        self.stdout = iter(self.lines)
        self.stderr = type("E", (), {"read": staticmethod(lambda: "")})()
        self.returncode = 0

    def wait(self):
        return 0


def test_download_stream_reports_to_a_callback_instead_of_a_bar(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(download.subprocess, "Popen", FakePopen)
    seen = []
    out = str(tmp_path / "Show - S01E01 - WEBDL - 720p.mp4")
    size = download.download_stream("https://cdn/m.m3u8", "https://r/", out, 3.0,
                                    on_progress=lambda d, t: seen.append((d, t)))
    assert size == 60_000
    assert seen == [(1.5, 3.0), (3.0, 3.0), (3.0, 3.0)]
    assert capsys.readouterr().out == ""  # no bar, no trailing newline


def test_download_stream_still_draws_the_bar_by_default(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(download.subprocess, "Popen", FakePopen)
    download.download_stream("https://cdn/m.m3u8", "https://r/", str(tmp_path / "a.mp4"), 3.0)
    assert "100.0%" in capsys.readouterr().out
