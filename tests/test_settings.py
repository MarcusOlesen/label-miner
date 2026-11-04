"""Settings kept between runs."""

from label_miner.settings import DEFAULTS, load_settings, save_settings, settings_path


def test_defaults_without_a_file():
    assert load_settings() == DEFAULTS
    assert load_settings()["stk_from_antal"] is False


def test_saved_settings_come_back():
    save_settings({**DEFAULTS, "stk_from_antal": True})
    assert settings_path().exists()
    assert load_settings()["stk_from_antal"] is True


def test_a_damaged_file_means_the_defaults(tmp_path):
    path = tmp_path / "settings.json"
    for content in ("{not json", '["a list"]', '{"stk_from_antal": "yes"}'):
        path.write_text(content, encoding="utf-8")
        assert load_settings(path) == DEFAULTS


def test_unknown_keys_are_ignored(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text('{"stk_from_antal": true, "something_old": 1}', encoding="utf-8")
    assert load_settings(path) == {"stk_from_antal": True}


def test_a_file_that_cant_be_written_is_no_error(tmp_path):
    blocked = tmp_path / "a file, not a folder"
    blocked.write_text("", encoding="utf-8")
    save_settings(DEFAULTS, blocked / "settings.json")  # must not raise
