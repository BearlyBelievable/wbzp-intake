import pytest
import yaml

from intake import destinations


def parse(path):
    return yaml.safe_load(path.read_text())


def test_default_file_subject():
    default = yaml.safe_load(destinations.default_file_text())["default"]

    assert default["subject"] == "New wbzp-intake submission"


def test_destinations_cli_edits(run_cli, tmp_path):
    path = tmp_path / "destinations.yaml"
    path.write_text(destinations.default_file_text())
    assert run_cli("destinations_cli.py", "is-placeholder", path)[1] == "yes\n"
    assert run_cli("destinations_cli.py", "names", path)[1] == "default\n"

    assert run_cli("destinations_cli.py", "set", path, "default", "zulip", "channel_id", "12", "topic", "Hello")[0] == 0
    assert parse(path) == {"default": {"type": "zulip", "channel_id": 12, "topic": "Hello"}}
    assert "default:\n    type: zulip\n    channel_id: 12\n    topic: Hello\n" in path.read_text()
    assert run_cli("destinations_cli.py", "set", path, "office", "email", "address", "office@example.com")[0] == 0
    assert run_cli("destinations_cli.py", "set", path, "office", "email", "subject", "Office request")[0] == 0
    assert parse(path)["office"] == {"type": "email", "address": ["office@example.com"], "subject": "Office request"}
    assert path.read_text().startswith(destinations.default_file_text().split("\n")[0])

    assert run_cli("destinations_cli.py", "names", path)[1] == "default\noffice\n"
    assert run_cli("destinations_cli.py", "has-type", path, "zulip")[1] == "yes\n"
    assert run_cli("destinations_cli.py", "has-type", path, "fax")[1] == ""
    assert run_cli("destinations_cli.py", "is-placeholder", path)[1] == ""

    assert run_cli("destinations_cli.py", "remove", path, "office")[0] == 0
    assert run_cli("destinations_cli.py", "remove", path, "nonexistent")[0] == 0
    assert list(parse(path)) == ["default"]


def test_destinations_cli_new_file(run_cli, tmp_path):
    missing = tmp_path / "new.yaml"
    assert run_cli("destinations_cli.py", "set", missing, "default", "zulip", "channel_id", "3", "topic", "Hi")[0] == 0
    assert parse(missing) == {"default": {"type": "zulip", "channel_id": 3, "topic": "Hi"}}

    commented = tmp_path / "commented.yaml"
    commented.write_text("# my notes" + chr(10))
    assert run_cli("destinations_cli.py", "set", commented, "default", "zulip", "channel_id", "4", "topic", "Hi")[0] == 0
    assert commented.read_text().startswith("# my notes")
    assert parse(commented) == {"default": {"type": "zulip", "channel_id": 4, "topic": "Hi"}}


@pytest.mark.parametrize(
    "arguments, fragment",
    [
        (("x", "zulip", "channel_id", "5"), "is missing key(s): topic"),
        (("x", "zulip", "topic", "Hi"), "is missing key(s): channel_id"),
        (("x", "zulip", "channel_id", "abc", "topic", "Hi"), "invalid literal for int()"),
        (("x", "fax", "key", "value"), "invalid or missing 'type'"),
    ],
    ids=["missing-topic", "missing-channel", "channel-not-a-number", "unknown-type"],
)
def test_destinations_cli_bad_edit(run_cli, tmp_path, arguments, fragment):
    path = tmp_path / "destinations.yaml"
    path.write_text(destinations.default_file_text())

    code, out, err = run_cli("destinations_cli.py", "set", path, *arguments)

    assert fragment in code
    assert path.read_text() == destinations.default_file_text()


def test_placeholder_check(run_cli, tmp_path):
    path = tmp_path / "destinations.yaml"

    for command in ("names", "has-type", "is-placeholder"):
        extra = ("zulip",) if command == "has-type" else ()
        assert run_cli("destinations_cli.py", command, path, *extra) == (0, "", "")

    path.write_text(destinations.default_file_text())
    run_cli("destinations_cli.py", "set", path, "second", "email", "address", "b@example.com")
    assert run_cli("destinations_cli.py", "is-placeholder", path)[1] == ""

    path.write_text(destinations.default_file_text())
    run_cli("destinations_cli.py", "set", path, "default", "email", "address", "real@example.com")
    assert run_cli("destinations_cli.py", "is-placeholder", path)[1] == ""


def test_destinations_cli_usage(run_cli, tmp_path):
    assert run_cli("destinations_cli.py")[0] == 2
    assert run_cli("destinations_cli.py", "set", tmp_path / "d.yaml", "name", "zulip", "channel_id")[0] == 2


def test_set_on_missing_file_writes_default_header(run_cli, tmp_path):
    path = tmp_path / "destinations.yaml"

    assert run_cli("destinations_cli.py", "set", path, "default", "zulip", "channel_id", "5", "topic", "Hi")[0] == 0

    text = path.read_text()
    assert text.startswith(destinations.default_file_text().split("\n")[0])
    assert parse(path) == {"default": {"type": "zulip", "channel_id": 5, "topic": "Hi"}}


def write_config(tmp_path, **settings):
    path = tmp_path / "config.conf"
    path.write_text("[config]" + chr(10) + "".join(f"{key} = {value}" + chr(10) for key, value in settings.items()))
    return path


@pytest.mark.parametrize(
    "settings, address",
    [
        ({"contact_email": "me@example.com", "bounce_source": "imap"}, "me@example.com"),
        ({"contact_email": "me@example.com", "bounce_source": "external"}, "me@example.com"),
        ({"contact_email": "me@example.com", "bounce_source": ""}, "example@domain.com"),
        ({"bounce_source": "imap"}, "example@domain.com"),
    ],
    ids=["imap", "external", "no-bounce-source", "no-contact-email"],
)
def test_init_creates_the_default_file(run_cli, tmp_path, settings, address):
    path = tmp_path / "destinations.yaml"

    code, out, _ = run_cli("destinations_cli.py", "init", path, write_config(tmp_path, **settings))

    assert code == 0
    assert parse(path)["default"]["address"] == [address]
    assert path.read_text().startswith(destinations.default_file_text().split(chr(10))[0])


def test_init_leaves_an_existing_file_alone(run_cli, tmp_path):
    path = tmp_path / "destinations.yaml"
    path.write_text("office: {type: zulip, channel_id: 1, topic: Hi}" + chr(10))

    assert run_cli("destinations_cli.py", "init", path, write_config(tmp_path))[0] == 0

    assert list(parse(path)) == ["office"]


def test_init_recreates_an_empty_file(run_cli, tmp_path):
    path = tmp_path / "destinations.yaml"
    path.write_text("{}" + chr(10))

    run_cli("destinations_cli.py", "init", path, write_config(tmp_path))

    assert list(parse(path)) == ["default"]


def test_default_subject(run_cli):
    assert run_cli("destinations_cli.py", "default-subject") == (0, destinations.DEFAULT_SUBJECT + chr(10), "")


def test_has_email(run_cli, tmp_path):
    path = tmp_path / "destinations.yaml"
    path.write_text(destinations.default_file_text())
    assert run_cli("destinations_cli.py", "has-email", path)[1] == ""

    assert run_cli("destinations_cli.py", "set", path, "default", "zulip", "channel_id", "5", "topic", "Hi")[0] == 0
    assert run_cli("destinations_cli.py", "has-email", path)[1] == ""

    assert run_cli("destinations_cli.py", "set", path, "office", "email", "address", "office@example.com")[0] == 0
    assert run_cli("destinations_cli.py", "has-email", path)[1] == "yes\n"


def test_set_email_address_list(run_cli, tmp_path):
    path = tmp_path / "destinations.yaml"

    assert run_cli("destinations_cli.py", "set", path, "office", "email", "address", "to@example.com, bcc@example.com")[0] == 0

    assert parse(path)["office"]["address"] == ["to@example.com", "bcc@example.com"]
