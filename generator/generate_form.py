import argparse
import sys
from pathlib import Path

import markdown as markdown_lib
from jinja2 import Environment, FileSystemLoader
from markupsafe import Markup

GENERATOR_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(GENERATOR_DIR.parent))

from intake.config import load_yaml  # noqa: E402
from intake.errors import ConfigError
from intake.forms import validate  # noqa: E402


def _validate_url(name, value):
    if not (value.startswith("/") or value.startswith("http://") or value.startswith("https://")):
        raise ValueError(
            f"{name} must be a root-relative path or an http(s) URL, got {value!r}. "
            "If you're invoking this from Git Bash on Windows, MSYS path conversion may have "
            "rewritten a leading '/' argument into an absolute Windows path. Prefix the "
            "command with MSYS_NO_PATHCONV=1."
        )


def generate(template, action, css_url, js_url, max_text_length, max_textarea_length, turnstile_site_key):
    validate(template)
    _validate_url("action", action)
    _validate_url("css_url", css_url)
    _validate_url("js_url", js_url)
    env = Environment(loader=FileSystemLoader(GENERATOR_DIR), autoescape=True)
    env.filters["markdown"] = lambda text: Markup(markdown_lib.markdown(text))
    jinja_template = env.get_template("template.jinja")
    return jinja_template.render(
        application_fields=template["fields"],
        action=action,
        css_url=css_url,
        js_url=js_url,
        max_text_length=max_text_length,
        max_textarea_length=max_textarea_length,
        turnstile_site_key=turnstile_site_key,
    )


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("templates_dir")
    parser.add_argument("output_dir")
    parser.add_argument("--css-url", default="/wbzp-intake-form.css")
    parser.add_argument("--js-url", default="/wbzp-intake-form.js")
    parser.add_argument("--max-text-length", type=int, default=250)
    parser.add_argument("--max-textarea-length", type=int, default=1000)
    parser.add_argument("--turnstile-site-key", default="")
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    templates_dir = Path(args.templates_dir)
    output_dir = Path(args.output_dir)
    try:
        for template_path in sorted(templates_dir.glob("*.yaml")):
            name = template_path.stem
            template = load_yaml(template_path)

            html = generate(
                template,
                action=f"/intake/{name}",
                css_url=args.css_url,
                js_url=args.js_url,
                max_text_length=args.max_text_length,
                max_textarea_length=args.max_textarea_length,
                turnstile_site_key=args.turnstile_site_key,
            )

            (output_dir / f"{name}-form.html").write_text(html)
    except (ConfigError, ValueError) as error:
        raise SystemExit(str(error)) from error


if __name__ == "__main__":
    main()
