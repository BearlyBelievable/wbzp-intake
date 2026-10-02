import logging

from flask import current_app

from . import config, destinations, forms
from .errors import ConfigError

logger = logging.getLogger(__name__)


def _load(path):
    template = config.load_yaml(path)
    forms.validate(template, destinations.get_destinations())
    return template


def load_all(forms_dir):
    cache = {}
    for path in sorted(forms_dir.glob("*.yaml")):
        cache[path.stem] = (path.stat().st_mtime_ns, _load(path))
    return cache


def _refresh(forms_dir, cache):
    fresh = {}
    for path in sorted(forms_dir.glob("*.yaml")):
        try:
            mtime = path.stat().st_mtime_ns
        except OSError:
            continue
        cached = cache.get(path.stem)
        if cached is not None and cached[0] == mtime:
            fresh[path.stem] = cached
            continue
        try:
            fresh[path.stem] = (mtime, _load(path))
        except (ConfigError, OSError) as error:
            logger.error("The %s form was changed but isn't valid, so its last valid version is still in use: %s", path.stem, error)
            fresh[path.stem] = (mtime, cached[1] if cached is not None else None)
    return fresh


def get(name):
    cache = _refresh(current_app.config["FORMS_DIR"], current_app.config["FORM_CACHE"])
    current_app.config["FORM_CACHE"] = cache
    entry = cache.get(name)
    return entry[1] if entry is not None else None
