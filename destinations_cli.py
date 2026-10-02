import argparse

from intake import config, destinations
from intake.errors import ConfigError

DESCRIPTION = "Get or set destinations in destinations.yaml."


def load(path):
    try:
        return config.load_yaml(path) or {}
    except FileNotFoundError:
        return {}


def cmd_names(path):
    for name in load(path):
        print(name)


def cmd_set(path, name, dest_type, pairs):
    paths = load(path)
    destination = paths.get(name, {})
    if destination.get("type") != dest_type:
        destination = {}
    destination["type"] = dest_type
    for key, value in zip(pairs[::2], pairs[1::2]):
        if key == "channel_id":
            destination[key] = int(value)
        elif key == "address":
            destination[key] = [address.strip() for address in value.split(",")]
        else:
            destination[key] = value
    paths[name] = destination
    destinations.validate(paths, path)
    destinations.save(path, paths)


def cmd_remove(path, name):
    paths = load(path)
    paths.pop(name, None)
    destinations.save(path, paths)


def cmd_has_type(path, dest_type):
    paths = load(path)
    if any(destination.get("type") == dest_type for destination in paths.values()):
        print("yes")


def cmd_init(path, config_path):
    if load(path):
        return
    settings = config.load_ini_section(config_path, "config")
    address = destinations.default_address(settings.get("bounce_source", ""), settings.get("contact_email", ""))
    with open(path, "w") as f:
        f.write(destinations.default_file_text(address))
    print(f"{path} is ready.")


def cmd_default_subject():
    print(destinations.DEFAULT_SUBJECT)


def cmd_has_email(path):
    if destinations.email_destination_names(load(path)):
        print("yes")


def cmd_is_placeholder(path):
    if destinations.is_placeholder(load(path)):
        print("yes")


def main():
    parser = argparse.ArgumentParser(description=DESCRIPTION)
    subparsers = parser.add_subparsers(dest="command", required=True)

    names_parser = subparsers.add_parser("names")
    names_parser.add_argument("path")

    set_parser = subparsers.add_parser("set")
    set_parser.add_argument("path")
    set_parser.add_argument("name")
    set_parser.add_argument("type")
    set_parser.add_argument("pairs", nargs="+", metavar="KEY VALUE")

    remove_parser = subparsers.add_parser("remove")
    remove_parser.add_argument("path")
    remove_parser.add_argument("name")

    has_type_parser = subparsers.add_parser("has-type")
    has_type_parser.add_argument("path")
    has_type_parser.add_argument("type")

    init_parser = subparsers.add_parser("init")
    init_parser.add_argument("path")
    init_parser.add_argument("config_path")

    subparsers.add_parser("default-subject")

    has_email_parser = subparsers.add_parser("has-email")
    has_email_parser.add_argument("path")

    is_placeholder_parser = subparsers.add_parser("is-placeholder")
    is_placeholder_parser.add_argument("path")

    args = parser.parse_args()
    if args.command == "set" and len(args.pairs) % 2:
        parser.error("set needs KEY VALUE pairs")

    try:
        if args.command == "names":
            cmd_names(args.path)
        elif args.command == "set":
            cmd_set(args.path, args.name, args.type, args.pairs)
        elif args.command == "remove":
            cmd_remove(args.path, args.name)
        elif args.command == "has-type":
            cmd_has_type(args.path, args.type)
        elif args.command == "init":
            cmd_init(args.path, args.config_path)
        elif args.command == "default-subject":
            cmd_default_subject()
        elif args.command == "has-email":
            cmd_has_email(args.path)
        elif args.command == "is-placeholder":
            cmd_is_placeholder(args.path)
    except (ConfigError, ValueError) as error:
        raise SystemExit(str(error)) from error


if __name__ == "__main__":
    main()
