import json
from pathlib import Path

PROVIDERS_PATH = Path(__file__).resolve().parent.parent / "email_providers.json"


def load(path=None):
    with open(path or PROVIDERS_PATH, encoding="utf-8") as f:
        return json.load(f)


def flows(data):
    return [(flow["id"], flow["label"]) for flow in data["flows"]]


def providers_in_flow(data, flow_id):
    return [(provider["id"], provider["name"]) for provider in data["providers"] if provider["flow"] == flow_id]


def lookup(data, kind, item_id, field):
    items = data["flows"] if kind == "flow" else data["providers"]
    for item in items:
        if item["id"] == item_id:
            value = item.get(field, "")
            if value is True:
                return "yes"
            if value is False:
                return ""
            return str(value)
    raise KeyError(item_id)
