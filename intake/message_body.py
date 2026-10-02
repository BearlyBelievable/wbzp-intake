import re


INLINE_SYNTAX = re.compile(r"([\\`*_\[\]])")
BLOCK_START = re.compile(r"^([>#|=~])")
BULLET_START = re.compile(r"^([-+])(?=[ -]|$)")
LIST_START = re.compile(r"^(\d+)([.)])")


def _escape_line(line):
    line = INLINE_SYNTAX.sub(r"\\\1", line.lstrip(" \t"))
    line = BLOCK_START.sub(r"\\\1", line)
    line = BULLET_START.sub(r"\\\1", line)
    return LIST_START.sub(r"\1\\\2", line)


def _escape_markdown(value):
    return "\n".join(_escape_line(line) for line in value.replace("\r\n", "\n").split("\n"))


def _format_zulip_value(value):
    match value:
        case bool():
            return "Yes" if value else "No"
        case list():
            if len(value) == 1:
                return _escape_markdown(value[0])
            return "\n".join(f"- {_escape_markdown(item)}" for item in value)
        case _:
            return _escape_markdown(str(value))


def render_zulip_body(form_name, active_fields, values):
    body_lines = [f"**Form:** {form_name}"]
    for field in active_fields:
        if field["name"] in values:
            rendered = _format_zulip_value(values[field["name"]])
        else:
            rendered = "_(user did not specify)_"
        body_lines.append(f"**{field['label']}**\n{rendered}")
    return "\n\n".join(body_lines)


def _format_email_value(value):
    match value:
        case bool():
            return "Yes" if value else "No"
        case list():
            return "\n".join(f"- {item}" for item in value)
        case _:
            return str(value)


def render_email_body(active_fields, values):
    body_lines = []
    for field in active_fields:
        if field["name"] in values:
            rendered = _format_email_value(values[field["name"]])
        else:
            rendered = "(not answered)"
        body_lines.append(f"{field['label']}\n{rendered}")
    return "\n\n".join(body_lines)


def render(destination, form_name, active_fields, values):
    match destination.type:
        case "zulip":
            return render_zulip_body(form_name, active_fields, values)
        case "email":
            return render_email_body(active_fields, values)
