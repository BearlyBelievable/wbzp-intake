import logging
import operator
import re

from . import destinations
from .errors import ConfigError

logger = logging.getLogger(__name__)

COMPARISONS = {
    "<": operator.lt,
    "<=": operator.le,
    ">": operator.gt,
    ">=": operator.ge,
    "=": operator.eq,
    "!=": operator.ne,
}

COMPARISON_PATTERN = re.compile(r"(<=|>=|!=|<|>|=)\s*(-?\d+)")


def parse_comparison(condition):
    match = COMPARISON_PATTERN.fullmatch(condition.strip()) if isinstance(condition, str) else None
    if match is None:
        return None
    return COMPARISONS[match.group(1)], int(match.group(2))


def _validate_condition(rule, field):
    name = rule["trigger_field_name"]
    condition = rule["condition"]
    if field["type"] == "number":
        if parse_comparison(condition) is None:
            raise ConfigError("invalid_number_condition", field=name)
        return
    if not isinstance(condition, list) or not condition:
        raise ConfigError("invalid_choice_condition", field=name)
    answer_type = bool if field["type"] == "boolean" else str
    if not all(isinstance(answer, answer_type) for answer in condition):
        raise ConfigError("invalid_choice_condition", field=name)
    if field["type"] in ("select", "multiselect"):
        for answer in condition:
            if answer not in field["options"]:
                raise ConfigError("unknown_condition_answer", field=name, answer=answer)


def _is_conditional(rule):
    return "trigger_field_name" in rule or "condition" in rule


def validate_rules(routing_rules, fields_by_name):
    if not isinstance(routing_rules, list) or not routing_rules:
        raise ConfigError("invalid_routing_rules")
    for rule in routing_rules:
        if not isinstance(rule, dict) or "destination" not in rule:
            raise ConfigError("invalid_routing_rule")
        if _is_conditional(rule):
            if not {"trigger_field_name", "condition"} <= rule.keys():
                raise ConfigError("invalid_routing_rule")
            field = fields_by_name.get(rule["trigger_field_name"])
            if field is None:
                raise ConfigError("unknown_routing_field", field=rule["trigger_field_name"])
            _validate_condition(rule, field)
    fallbacks = [index for index, rule in enumerate(routing_rules) if not _is_conditional(rule)]
    if not fallbacks:
        raise ConfigError("missing_fallback_rule")
    for rule in routing_rules[fallbacks[0] + 1 :]:
        logger.warning(
            "The routing rule for '%s' comes after a rule that matches every submission, so it never runs.",
            rule["destination"],
        )


def validate_destination_refs(template, configured):
    referenced = {rule["destination"] for rule in template["routing_rules"]}
    unknown = referenced - configured.keys()
    if unknown:
        raise ConfigError("unknown_template_destination", names=", ".join(sorted(unknown)))


def _matches(field, condition, value):
    if field["type"] == "number":
        compare, target = parse_comparison(condition)
        return compare(value, target)
    if isinstance(value, list):
        return any(answer in condition for answer in value)
    return value in condition


def resolve(template, values):
    fields_by_name = {field["name"]: field for field in template["fields"]}
    for rule in template["routing_rules"]:
        if not _is_conditional(rule):
            return destinations.get_destination(rule["destination"])
        value = values.get(rule["trigger_field_name"])
        if value is not None and _matches(fields_by_name[rule["trigger_field_name"]], rule["condition"], value):
            return destinations.get_destination(rule["destination"])
