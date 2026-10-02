import configparser

import yaml
from flask import current_app

from .errors import ConfigError


def load_ini_section(path, section):
    parser = configparser.ConfigParser()
    try:
        parser.read(path)
    except configparser.Error as error:
        raise ConfigError("invalid_config_file", path=path, error=error) from error
    if not parser.has_section(section):
        return {}
    return dict(parser.items(section))


def load_yaml(path):
    try:
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f)
    except yaml.YAMLError as error:
        raise ConfigError("invalid_yaml", path=path, error=error) from error


def _setting(name, default):
    value = current_app.config.get(name, "")
    if value != "":
        return value
    if default is not None:
        return default
    raise ConfigError("missing_setting", name=name)


def read_secret(name, default=None):
    return _setting(name, default)


def read_config(name, default=None, cast=str):
    value = _setting(name, default)
    try:
        return cast(value)
    except ValueError as error:
        raise ConfigError("invalid_setting", name=name, value=value) from error
