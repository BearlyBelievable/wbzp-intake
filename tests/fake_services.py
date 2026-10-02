import imaplib
import json
import os
import re
import smtplib
import socket
import threading
import urllib.parse
from dataclasses import dataclass

import responses


ZULIP_URL = "https://zulip.test"


TURNSTILE_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"


class FakeZulip:
    def __init__(self, mock_http):
        self.users = {}
        self.posts = []
        self.lookups = 0
        self.post_status = 200
        self.lookup_status = None
        self.lookup_message = "No such user"
        mock_http.add_callback(responses.GET, re.compile(rf"{ZULIP_URL}/api/v1/users/.+"), callback=self._user)
        mock_http.add_callback(responses.POST, f"{ZULIP_URL}/api/v1/messages", callback=self._post)

    def _user(self, request):
        self.lookups += 1
        if self.lookup_status is not None:
            return self.lookup_status, {}, json.dumps({"result": "error", "msg": self.lookup_message})
        email = urllib.parse.unquote(request.url.rsplit("/", 1)[1])
        if email not in self.users:
            return 400, {}, json.dumps({"result": "error", "msg": self.lookup_message})
        return 200, {}, f'{{"user": {{"is_active": {str(self.users[email]).lower()}}}}}'

    def _post(self, request):
        fields = dict(urllib.parse.parse_qsl(request.body))
        if self.post_status == 200:
            self.posts.append(fields)
            return 200, {}, '{"result": "success"}'
        return self.post_status, {}, '{"result": "error"}'


class FakeLookupHelper:
    def __init__(self, path):
        self.path = path
        self.statuses = {}
        self.requests = []
        self.reply = None
        self.hang_up = False
        self._server = None
        self._thread = None

    def start(self):
        self._server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._server.bind(str(self.path))
        self._server.listen(4)
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()
        return self

    def _serve(self):
        while True:
            try:
                connection, _ = self._server.accept()
            except OSError:
                return
            with connection:
                request = connection.makefile("rb").readline().decode().strip()
                self.requests.append(request)
                if self.hang_up:
                    continue
                reply = self.reply if self.reply is not None else self.statuses.get(request.lower(), "none")
                connection.sendall(reply.encode() + b"\n")

    def stop(self):
        try:
            self._server.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self._server.close()
        self._thread.join(timeout=2)
        try:
            os.unlink(self.path)
        except OSError:
            pass


@dataclass
class SentMail:
    message: object
    recipients: list


class FakeSmtpServer:
    def __init__(self):
        self.sent = []
        self.connections = []
        self.logins = []
        self.connect_error = None
        self.login_error = None
        self.refused = {}
        self.ssl_connections = 0
        self.starttls_calls = 0
        self.contexts = []

    def install(self, monkeypatch):
        server = self

        class Connection:
            uses_ssl = False

            def __init__(self, host, port, timeout=None, context=None):
                if context is not None:
                    server.contexts.append(context)
                if server.connect_error:
                    raise server.connect_error
                server.connections.append((host, port, timeout))
                if self.uses_ssl:
                    server.ssl_connections += 1

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def starttls(self, context=None):
                server.starttls_calls += 1
                server.contexts.append(context)

            def login(self, user, password):
                if server.login_error:
                    raise server.login_error
                server.logins.append((user, password))

            def send_message(self, message, to_addrs=None):
                if not server.refused:
                    server.sent.append(SentMail(message, to_addrs))
                return server.refused

        class SslConnection(Connection):
            uses_ssl = True

        monkeypatch.setattr(smtplib, "SMTP", Connection)
        monkeypatch.setattr(smtplib, "SMTP_SSL", SslConnection)
        return self


class FakeMailbox:
    def __init__(self):
        self.messages = {}
        self.seen = []
        self.connections = []
        self.logins = []
        self.login_error = None
        self.search_status = "OK"
        self.unfetchable = set()
        self.contexts = []
        self.selected = []
        self.fetch_parts = []
        self.missing_folders = set()

    def install(self, monkeypatch):
        mailbox = self

        class Connection:
            def __init__(self, host, port, ssl_context=None):
                mailbox.connections.append((host, port))
                mailbox.contexts.append(ssl_context)

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def login(self, user, password):
                if mailbox.login_error:
                    raise mailbox.login_error
                mailbox.logins.append((user, password))

            def select(self, name):
                mailbox.selected.append(name)
                if name.strip('"') in mailbox.missing_folders:
                    return "NO", [b"no such folder"]
                return "OK", [b"1"]

            def search(self, charset, criteria):
                return mailbox.search_status, [b" ".join(mailbox.messages)]

            def fetch(self, message_id, parts):
                mailbox.fetch_parts.append(parts)
                if message_id in mailbox.unfetchable:
                    return "NO", None
                return "OK", [(b"header", mailbox.messages[message_id])]

            def store(self, message_id, command, flags):
                mailbox.seen.append(message_id)

        monkeypatch.setattr(imaplib, "IMAP4_SSL", Connection)
        return self
