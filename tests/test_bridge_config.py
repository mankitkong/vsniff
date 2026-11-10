"""Bridge settings: secrets from files, Sonarr's config.xml, generated key."""
import os
import stat

from vsniff.bridge import config


def test_secret_from_a_plain_file(tmp_path, monkeypatch):
    (tmp_path / "k").write_text("abc123\n")
    monkeypatch.setenv("SOME_KEY_FILE", str(tmp_path / "k"))
    assert config.secret("SOME_KEY") == "abc123"


def test_secret_from_sonarrs_config_xml(tmp_path, monkeypatch):
    (tmp_path / "config.xml").write_text(
        "<Config>\n  <Port>8989</Port>\n  <ApiKey>0123456789abcdef0123456789abcdef</ApiKey>\n</Config>")
    monkeypatch.setenv("SONARR_API_KEY_FILE", str(tmp_path / "config.xml"))
    assert config.secret("SONARR_API_KEY") == "0123456789abcdef0123456789abcdef"


def test_bridge_key_is_generated_once_and_kept_private(tmp_path, monkeypatch):
    monkeypatch.delenv("VSNIFF_BRIDGE_API_KEY", raising=False)
    monkeypatch.delenv("VSNIFF_BRIDGE_API_KEY_FILE", raising=False)
    first = config.bridge_api_key(str(tmp_path))
    assert len(first) == 32 and config.bridge_api_key(str(tmp_path)) == first
    mode = stat.S_IMODE(os.stat(tmp_path / "api-key").st_mode)
    assert mode == 0o600


def test_a_configured_bridge_key_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("VSNIFF_BRIDGE_API_KEY", "given")
    assert config.bridge_api_key(str(tmp_path)) == "given"
    assert not (tmp_path / "api-key").exists()
