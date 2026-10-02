CONFIG = "[config]\n# a comment\nsite_kind = Pelican\nzulip_bot_email =\n"


def test_config_cli_edits(run_cli, tmp_path):
    path = tmp_path / "config.conf"
    path.write_text(CONFIG)

    assert run_cli("config_cli.py", "get", path, "config", "site_kind") == (0, "Pelican\n", "")
    assert run_cli("config_cli.py", "get", path, "config", "missing")[1] == "\n"

    assert run_cli("config_cli.py", "set", path, "site_kind", "Other")[0] == 0
    assert run_cli("config_cli.py", "set", path, "zulip_bot_email", r"bot\1@example.com")[0] == 0
    assert path.read_text() == "[config]\n# a comment\nsite_kind = Other\nzulip_bot_email = bot\\1@example.com\n"

    assert run_cli("config_cli.py", "remove", path, "site_kind")[0] == 0
    after_remove = path.read_text()
    assert after_remove == "[config]\n# a comment\nzulip_bot_email = bot\\1@example.com\n"
    assert run_cli("config_cli.py", "remove", path, "nonexistent")[0] == 0
    assert path.read_text() == after_remove


def test_config_cli_errors(run_cli, tmp_path):
    path = tmp_path / "config.conf"
    path.write_text(CONFIG)

    assert run_cli("config_cli.py", "set", path, "nonexistent", "value")[0] == f"Error: 'nonexistent' not found in {path}"
    assert path.read_text() == CONFIG
    assert run_cli("config_cli.py")[0] == 2
