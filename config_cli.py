import argparse
import configparser
import re

DESCRIPTION = "Get or set a value in config.conf/secrets.conf without disturbing comments or other lines."
KEY_NOT_FOUND_MESSAGE = "Error: '{key}' not found in {path}"


def get_value(path, section, key):
    parser = configparser.ConfigParser()
    parser.read(path)
    return parser.get(section, key, fallback="")


def set_value(path, key, value):
    with open(path, encoding="utf-8") as f:
        text = f.read()

    pattern = re.compile(rf"^{re.escape(key)}[ \t]*=.*$", re.MULTILINE)
    replacement = f"{key} = {value}"
    new_text, count = pattern.subn(lambda match: replacement, text, count=1)
    if count == 0:
        raise SystemExit(KEY_NOT_FOUND_MESSAGE.format(key=key, path=path))

    with open(path, "w", encoding="utf-8") as f:
        f.write(new_text)


def remove_key(path, key):
    with open(path, encoding="utf-8") as f:
        text = f.read()

    pattern = re.compile(rf"^{re.escape(key)}[ \t]*=.*\n?", re.MULTILINE)
    new_text, count = pattern.subn("", text, count=1)
    if count == 0:
        return

    with open(path, "w", encoding="utf-8") as f:
        f.write(new_text)


def main():
    parser = argparse.ArgumentParser(description=DESCRIPTION)
    subparsers = parser.add_subparsers(dest="command", required=True)

    get_parser = subparsers.add_parser("get")
    get_parser.add_argument("path")
    get_parser.add_argument("section")
    get_parser.add_argument("key")

    set_parser = subparsers.add_parser("set")
    set_parser.add_argument("path")
    set_parser.add_argument("key")
    set_parser.add_argument("value")

    remove_parser = subparsers.add_parser("remove")
    remove_parser.add_argument("path")
    remove_parser.add_argument("key")

    args = parser.parse_args()

    if args.command == "get":
        print(get_value(args.path, args.section, args.key))
    elif args.command == "set":
        set_value(args.path, args.key, args.value)
    elif args.command == "remove":
        remove_key(args.path, args.key)


if __name__ == "__main__":
    main()
