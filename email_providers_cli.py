import argparse

from intake import email_providers

DESCRIPTION = "List the email flows and providers in email_providers.json, or look up one value."


def main():
    parser = argparse.ArgumentParser(description=DESCRIPTION)
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("flows")

    providers_parser = subparsers.add_parser("providers")
    providers_parser.add_argument("flow")

    for kind in ("flow", "provider"):
        get_parser = subparsers.add_parser(kind)
        get_parser.add_argument("id")
        get_parser.add_argument("field")

    args = parser.parse_args()
    data = email_providers.load()

    if args.command == "flows":
        for flow_id, label in email_providers.flows(data):
            print(f"{flow_id}\t{label}")
    elif args.command == "providers":
        for provider_id, name in email_providers.providers_in_flow(data, args.flow):
            print(f"{provider_id}\t{name}")
    else:
        try:
            print(email_providers.lookup(data, args.command, args.id, args.field))
        except KeyError as error:
            raise SystemExit(f"No {args.command} named {error.args[0]}") from error


if __name__ == "__main__":
    main()
