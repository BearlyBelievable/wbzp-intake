import argparse

from intake import zulip_setup
from intake.errors import DeliveryError

DESCRIPTION = "Print the outgoing email server from Zulip's settings.py as host|port|security."


def main():
    parser = argparse.ArgumentParser(description=DESCRIPTION)
    parser.add_argument("settings_path", nargs="?")
    args = parser.parse_args()

    try:
        host, port, security = zulip_setup.read_email_server(args.settings_path)
    except DeliveryError as error:
        raise SystemExit(str(error)) from error
    print(f"{host}|{port}|{security}")


if __name__ == "__main__":
    main()
