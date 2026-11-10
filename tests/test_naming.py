from vsniff import naming


def test_existing_episodes_matches_by_prefix(tmp_path):
    (tmp_path / "Blossoms of Power - S01E03 - WEBDL - 1080p.mp4").write_text("x")
    (tmp_path / "Blossoms of Power - S01E07 - WEBDL - 720p.mp4").write_text("x")
    (tmp_path / "Other Show - S01E01 - WEBDL - 1080p.mp4").write_text("x")
    (tmp_path / "Blossoms of Power - S02E05 - WEBDL - 1080p.mp4").write_text("x")
    found = naming.existing_episodes(str(tmp_path), "Blossoms of Power", 1)
    assert found == {
        3: "Blossoms of Power - S01E03 - WEBDL - 1080p.mp4",
        7: "Blossoms of Power - S01E07 - WEBDL - 720p.mp4",
    }


def test_existing_episodes_keeps_the_real_resolution_tag(tmp_path):
    # a sidecar must be named after the file on disk, not a rebuilt name
    (tmp_path / "Show - S01E01 - WEBDL - 480p.mp4").write_text("x")
    found = naming.existing_episodes(str(tmp_path), "Show", 1)
    assert found[1].endswith("480p.mp4")


def test_existing_episodes_missing_dir_is_empty():
    assert naming.existing_episodes("/no/such/dir", "X", 1) == {}


def test_movie_name_with_and_without_year():
    assert naming.movie_name("Cardcaptor Sakura The Movie", 1999) == \
        "Cardcaptor Sakura The Movie (1999)"
    assert naming.movie_name("Cardcaptor Sakura The Movie", None) == \
        "Cardcaptor Sakura The Movie"


def test_build_movie_filename_has_no_episode_tag():
    assert naming.build_movie_filename("CCS Movie", 1999, "WEBDL", "1080p") == \
        "CCS Movie (1999) - WEBDL - 1080p.mp4"


def test_existing_movie_matches_any_resolution(tmp_path):
    (tmp_path / "CCS Movie (1999) - WEBDL - 480p.mp4").write_text("x")
    (tmp_path / "CCS Movie (1999) - WEBDL - 480p.zh.srt").write_text("x")
    assert naming.existing_movie(str(tmp_path), "CCS Movie", 1999) == \
        "CCS Movie (1999) - WEBDL - 480p.mp4"


def test_existing_movie_absent_or_missing_folder_is_none(tmp_path):
    assert naming.existing_movie(str(tmp_path), "CCS Movie", 1999) is None
    assert naming.existing_movie("/no/such/dir", "CCS Movie", 1999) is None
