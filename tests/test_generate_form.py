import generate_form
import pytest
import yaml
from app_harness import REPO_ROOT
from generate_form import generate

TEMPLATE_PATH = REPO_ROOT / "forms" / "application.yaml"
ROUTING_RULES = [{"destination": "default"}]
AUTO_EMAIL_FIELD = {"name": "submitter_email", "type": "email", "required": True, "label": "Email", "note": {}}
RENDER_OPTIONS = {
    "action": "/apply",
    "css_url": "/x.css",
    "js_url": "/x.js",
    "max_text_length": 250,
    "max_textarea_length": 1000,
    "turnstile_site_key": "",
}
NOTE = {"type": "info", "title": "Heads up", "text": "check this"}
FIELD_TYPES = {
    "text": {},
    "email": {},
    "textarea": {},
    "number": {},
    "boolean": {},
    "select": {"options": {"A": {}, "B": {}}},
    "multiselect": {"options": {"A": {}}},
}


def render(fields, **overrides):
    if not any(field["name"] == "submitter_email" for field in fields):
        fields = [*fields, AUTO_EMAIL_FIELD]
    template = {"routing_rules": ROUTING_RULES, "fields": fields}
    return generate(template, **{**RENDER_OPTIONS, **overrides})


def test_shipped_fields_render():
    fields = yaml.safe_load(TEMPLATE_PATH.read_text(encoding="utf-8"))["fields"]

    html = render(fields)

    assert "<form" in html
    assert "role_interest_details" in html


@pytest.mark.parametrize("field_type", FIELD_TYPES)
def test_note_renders(field_type):
    field = {"name": "n", "required": True, "label": "L", "note": NOTE, "type": field_type, **FIELD_TYPES[field_type]}

    assert "Heads up" in render([field])


def test_stray_options():
    nested = {"name": "hidden", "type": "text", "required": True, "label": "L2", "note": {}}
    field = {"name": "plain", "type": "text", "required": True, "label": "L", "note": {}, "options": {"Other": nested}}

    assert "data-wbzp-intake-companion-of" not in render([field])


def test_single_checkbox():
    field = {
        "name": "agree",
        "type": "multiselect",
        "required": True,
        "label": "Rules",
        "note": {},
        "options": {"I agree to follow the rules": {}},
    }

    html = render([field])

    assert html.count('type="checkbox"') == 1
    assert 'data-wbzp-intake-multiselect-required="true"' in html


@pytest.mark.parametrize("argument", ["action", "css_url", "js_url"])
def test_invalid_url(argument):
    with pytest.raises(ValueError, match=argument):
        render([], **{argument: "C:/Program Files/Git/extra/css/application-form.css"})


def test_labels_are_html_escaped():
    field = {"name": "n", "type": "text", "required": True, "label": "<script>alert(1)</script>", "note": {}}

    html = render([field])

    assert "<script>alert(1)" not in html
    assert "&lt;script&gt;alert(1)" in html


@pytest.fixture
def templates(tmp_path):
    directory = tmp_path / "templates"
    directory.mkdir()
    (tmp_path / "out").mkdir()
    return directory


def run_main(templates, *options):
    output = templates.parent / "out"
    generate_form.main([str(templates), str(output), *options])
    return output


def test_command_line_options(templates):
    bio = {"name": "bio", "type": "textarea", "required": False, "label": "Bio", "note": {}}
    template = {"routing_rules": ROUTING_RULES, "fields": [AUTO_EMAIL_FIELD, bio]}
    (templates / "page.yaml").write_text(yaml.safe_dump(template))

    defaults = (run_main(templates) / "page-form.html").read_text()
    assert 'maxlength="250"' in defaults
    assert 'maxlength="1000"' in defaults
    assert "cf-turnstile" not in defaults
    assert 'href="/wbzp-intake-form.css"' in defaults
    assert 'src="/wbzp-intake-form.js"' in defaults

    options = (
        ["--max-text-length", "100", "--max-textarea-length", "200", "--turnstile-site-key", "KEY"]
        + ["--css-url", "/a.css", "--js-url", "/b.js"]
    )
    custom = (run_main(templates, *options) / "page-form.html").read_text()
    assert 'maxlength="100"' in custom
    assert 'maxlength="200"' in custom
    assert 'data-sitekey="KEY"' in custom
    assert 'href="/a.css"' in custom
    assert 'src="/b.js"' in custom


def test_one_file_per_template(templates):
    template = {"routing_rules": ROUTING_RULES, "fields": [AUTO_EMAIL_FIELD]}
    (templates / "alpha.yaml").write_text(yaml.safe_dump(template))
    (templates / "beta.yaml").write_text(yaml.safe_dump(template))
    (templates / "not-a-template.txt").write_text("ignore me")

    output = run_main(templates)

    assert {path.name for path in output.iterdir()} == {"alpha-form.html", "beta-form.html"}
    assert 'action="/intake/alpha"' in (output / "alpha-form.html").read_text()
    assert 'action="/intake/beta"' in (output / "beta-form.html").read_text()


@pytest.mark.parametrize(
    "name, text, message",
    [
        ("broken.yaml", "fields: [unclosed\n", "broken.yaml is not valid YAML"),
        ("bad.yaml", yaml.safe_dump({"destination": "default"}), "missing required key"),
    ],
    ids=["invalid-yaml", "invalid-schema"],
)
def test_invalid_template_exits(templates, name, text, message):
    (templates / name).write_text(text)

    with pytest.raises(SystemExit) as caught:
        run_main(templates)

    assert message in str(caught.value)
