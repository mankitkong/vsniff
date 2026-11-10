from vsniff import cli
from vsniff.download import subtitle_sidecar


def test_filter_from_none_returns_all():
    assert cli.filter_from([1, 2, 3], None) == [1, 2, 3]


def test_filter_from_is_inclusive():
    assert cli.filter_from([1, 2, 3, 4, 5], 3) == [3, 4, 5]


def test_filter_from_above_max_is_empty():
    assert cli.filter_from([1, 2, 3], 99) == []


def test_all_requires_out(capsys):
    rc = cli.main([
        "https://chinaq.net/video/68261-20.html", "--series", "X", "--all"])
    assert rc == 1
    assert "requires --out" in capsys.readouterr().err


def test_all_rejects_a_site_without_a_catalog(capsys):
    rc = cli.main([
        "https://example.com/watch/123",
        "--series", "X", "--all", "--out", "."])
    assert rc == 1
    assert "only supported for chinaq.net, hkanime.com and dramasq.io" in capsys.readouterr().err


def fake_movie_run(monkeypatch, subtitles="https://cdn/subs.m3u8"):
    """Stub the network edges of a movie download; record what was asked for."""
    calls = {"discover": 0, "download": [], "subs": []}

    def discover(url, source, show=False, keep_ads=False):
        calls["discover"] += 1
        return (None, "hkanime", "https://cdn/m.m3u8", "https://ref/",
                "1080p", 60.0, subtitles)

    def download(m3u8, referer, out_path, duration=None):
        calls["download"].append(out_path)
        with open(out_path, "w") as fh:
            fh.write("x" * 60_000)
        return 60_000

    def subs(sub_url, referer, video_path, lang):
        calls["subs"].append(subtitle_sidecar(video_path, lang))

    monkeypatch.setattr(cli, "discover_stream", discover)
    monkeypatch.setattr(cli, "download_stream", download)
    monkeypatch.setattr(cli, "save_subtitles", subs)
    return calls


MOVIE_URL = "https://example.com/watch/ccs-movie"


def test_movie_lands_in_its_own_folder(monkeypatch, tmp_path):
    calls = fake_movie_run(monkeypatch)
    rc = cli.main([MOVIE_URL, "--title", "CCS Movie", "--year", "1999",
                      "--movie", "--out", str(tmp_path)])
    assert rc == 0
    folder = tmp_path / "CCS Movie (1999)"
    assert calls["download"] == [str(folder / "CCS Movie (1999) - WEBDL - 1080p.mp4")]
    assert calls["subs"] == [str(folder / "CCS Movie (1999) - WEBDL - 1080p.zh.srt")]


def test_movie_all_skips_a_complete_copy_without_touching_the_network(
        monkeypatch, tmp_path):
    calls = fake_movie_run(monkeypatch)
    folder = tmp_path / "CCS Movie (1999)"
    folder.mkdir()
    (folder / "CCS Movie (1999) - WEBDL - 720p.mp4").write_text("x")
    (folder / "CCS Movie (1999) - WEBDL - 720p.zh.srt").write_text("x")
    rc = cli.main([MOVIE_URL, "--title", "CCS Movie", "--year", "1999",
                      "--movie", "--all", "--out", str(tmp_path)])
    assert rc == 0
    assert calls["discover"] == 0 and calls["download"] == []


def test_movie_all_backfills_only_the_sidecar(monkeypatch, tmp_path):
    calls = fake_movie_run(monkeypatch)
    folder = tmp_path / "CCS Movie (1999)"
    folder.mkdir()
    (folder / "CCS Movie (1999) - WEBDL - 720p.mp4").write_text("x")
    rc = cli.main([MOVIE_URL, "--title", "CCS Movie", "--year", "1999",
                      "--movie", "--all", "--out", str(tmp_path)])
    assert rc == 0
    assert calls["download"] == []
    assert calls["subs"] == [str(folder / "CCS Movie (1999) - WEBDL - 720p.zh.srt")]


def test_movie_all_requires_out(monkeypatch, capsys):
    fake_movie_run(monkeypatch)
    rc = cli.main([MOVIE_URL, "--title", "X", "--movie", "--all"])
    assert rc == 1
    assert "requires --out" in capsys.readouterr().err


def test_search_command_lists_results(monkeypatch, capsys):
    from vsniff import api
    from vsniff.sites import SearchResult
    seen = {}

    def search(query, sites=None):
        seen.update(query=query, sites=sites)
        return [SearchResult("dramasq", "長相思 第二季", "https://dramasq.io/detail/1.html",
                             "第23集", 23)]

    monkeypatch.setattr(api, "search", search)
    assert cli.main(["search", "長相思", "--site", "dramasq"]) == 0
    out = capsys.readouterr().out
    assert "長相思 第二季  [第23集]" in out and "https://dramasq.io/detail/1.html" in out
    assert "1 result(s)" in out
    assert seen == {"query": "長相思", "sites": ["dramasq"]}
