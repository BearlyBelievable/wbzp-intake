import re

from . import config, forms
from .errors import ValidationError


def flatten_active(form, fields):
    active_fields = []
    for field in fields:
        active_fields.append(field)
        if field["type"] not in ("boolean", "select", "multiselect"):
            continue
        options = field.get("options")
        if not isinstance(options, dict):
            continue

        if field["type"] == "multiselect":
            selected_options = form.getlist(field["name"])
        else:
            selected_options = [form.get(field["name"], "")]

        for option, nested_field in options.items():
            if nested_field and option in selected_options:
                active_fields.extend(flatten_active(form, [nested_field]))

    return active_fields


def normalize_field_value(form, field):
    ftype = field["type"]
    match ftype:
        case "text" | "email" | "textarea":
            value = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", form.get(field["name"], "")).strip()
            if ftype != "textarea":
                value = " ".join(value.split())
            return value
        case "number":
            return form.get(field["name"], "").strip()
        case "select" | "boolean":
            return form.get(field["name"], "")
        case "multiselect":
            return [v for v in form.getlist(field["name"]) if v]


def validate_text_value(value, field):
    ftype = field["type"]
    if ftype == "textarea":
        default_max = config.read_config("max_textarea_length", default=1000, cast=int)
    else:
        default_max = config.read_config("max_text_length", default=250, cast=int)
    length = field.get("length", {})
    max_len = length.get("max", default_max)
    min_len = length.get("min")
    if len(value) > max_len:
        raise ValidationError("too_long", field=field)
    if min_len is not None and len(value) < min_len:
        raise ValidationError("too_short", field=field)
    if ftype == "email" and not re.match(r"^[^\s@]+@[^\s@]+\.[^\s@]+$", value):
        raise ValidationError("invalid_email", field=field)


def validate_number_value(value, field):
    if not re.fullmatch(r"-?\d+", value):
        raise ValidationError("not_a_number", field=field)
    try:
        num = int(value)
    except ValueError as error:
        raise ValidationError("not_a_number", field=field) from error
    value_range = field.get("range", {})
    if "min" in value_range and num < value_range["min"]:
        raise ValidationError("below_minimum", field=field)
    if "max" in value_range and num > value_range["max"]:
        raise ValidationError("above_maximum", field=field)


def has_invalid_option(values, field):
    return any(v not in forms.allowed_options(field) for v in values)


def validate_select_value(value, field):
    if has_invalid_option([value], field):
        raise ValidationError("invalid_value", field=field)


def validate_multiselect_value(value, field):
    if has_invalid_option(value, field):
        raise ValidationError("invalid_selection", field=field)


def validate_boolean_value(value, field):
    if value not in ("true", "false"):
        raise ValidationError("invalid_value", field=field)


def validate_field_value(value, field):
    ftype = field["type"]
    match ftype:
        case "text" | "email" | "textarea":
            validate_text_value(value, field)
        case "number":
            validate_number_value(value, field)
        case "select":
            validate_select_value(value, field)
        case "multiselect":
            validate_multiselect_value(value, field)
        case "boolean":
            validate_boolean_value(value, field)


def validate(form, active_fields):
    values, errors = {}, []
    for field in active_fields:
        value = normalize_field_value(form, field)
        if not value:
            if field["required"]:
                errors.append("field_required")
            continue
        try:
            validate_field_value(value, field)
        except ValidationError as error:
            errors.append(error.code)
            continue

        match field["type"]:
            case "number":
                values[field["name"]] = int(value)
            case "boolean":
                values[field["name"]] = value == "true"
            case _:
                values[field["name"]] = value
    return values, errors
