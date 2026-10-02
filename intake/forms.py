from . import routing
from .errors import ConfigError

FIELD_TYPES = {"text", "email", "textarea", "number", "select", "boolean", "multiselect"}
MAX_NESTING_DEPTH = 2


EMAIL_FIELD_NAME = "submitter_email"


def validate_fields_schema(fields):
    seen_names = set()
    pending = [(field, 1) for field in fields]
    while pending:
        field, depth = pending.pop()

        if not isinstance(field, dict) or "name" not in field:
            raise ConfigError("missing_field_name", field=field)
        name = field["name"]
        if name in seen_names:
            raise ConfigError("duplicate_field_name", name=name)
        seen_names.add(name)

        if depth > MAX_NESTING_DEPTH:
            raise ConfigError("field_too_deep", name=name, max_depth=MAX_NESTING_DEPTH)

        if field.get("type") not in FIELD_TYPES:
            raise ConfigError("invalid_field_type", name=name)
        if "label" not in field:
            raise ConfigError("missing_field_label", name=name)
        if "required" not in field:
            raise ConfigError("missing_field_required", name=name)

        if "note" not in field:
            raise ConfigError("missing_field_note", name=name)
        note = field["note"]
        if note not in (None, {}):
            if not isinstance(note, dict):
                raise ConfigError("invalid_field_note", name=name)
            if sorted(note.keys()) != ["text", "title", "type"]:
                raise ConfigError("incomplete_field_note", name=name)

        if field["type"] == "boolean":
            options = field.get("options")
            if options is None:
                continue
            if not isinstance(options, dict) or not set(options.keys()) <= {"true", "false"}:
                raise ConfigError("invalid_boolean_options", name=name)
        elif field["type"] in ("select", "multiselect"):
            options = field.get("options")
            if not isinstance(options, dict):
                raise ConfigError("missing_field_options", name=name)
            min_count = 2 if field["type"] == "select" else 1
            if len(options) < min_count:
                raise ConfigError("too_few_field_options", name=name, min_count=min_count)
        else:
            continue

        for nested_field in options.values():
            if not nested_field:
                continue
            if not isinstance(nested_field, dict):
                raise ConfigError("invalid_option_value", name=name)
            pending.append((nested_field, depth + 1))


def allowed_options(field):
    return list(field["options"].keys())


def validate(template, destinations=None):
    if not isinstance(template, dict):
        raise ConfigError("invalid_template")
    if "fields" not in template:
        raise ConfigError("missing_template_fields")
    if not isinstance(template["fields"], list):
        raise ConfigError("invalid_template_fields")
    validate_fields_schema(template["fields"])
    fields_by_name = {field["name"]: field for field in template["fields"]}

    email_field = fields_by_name.get(EMAIL_FIELD_NAME)
    if email_field is None:
        raise ConfigError("missing_email_field", name=EMAIL_FIELD_NAME)
    if email_field["type"] != "email":
        raise ConfigError("invalid_email_field", name=EMAIL_FIELD_NAME)
    if not email_field["required"]:
        raise ConfigError("optional_email_field", name=EMAIL_FIELD_NAME)

    if "routing_rules" not in template:
        raise ConfigError("missing_template_routing_rules")
    routing.validate_rules(template["routing_rules"], fields_by_name)

    if destinations is not None:
        routing.validate_destination_refs(template, destinations)
