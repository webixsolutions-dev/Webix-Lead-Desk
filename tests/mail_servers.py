"""Loopback-only fake SMTP and IMAP servers for protocol acceptance tests."""

import socketserver
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import cast


class MailServer(socketserver.ThreadingTCPServer):
    """Disposable local server recording only synthetic protocol traffic."""

    allow_reuse_address = True
    daemon_threads = True

    def __init__(
        self, handler: type[socketserver.BaseRequestHandler], *, drop: bool = False
    ) -> None:
        """Bind an ephemeral loopback port; never use a real mailbox."""
        super().__init__(("127.0.0.1", 0), handler)
        self.messages: list[bytes] = []
        self.requests: list[str] = []
        self.drop = drop
        self.search_ids = b"1"
        self.uidvalidity = b"19"


class SMTPHandler(socketserver.StreamRequestHandler):
    """Implement the subset used by smtplib with optional post-DATA disconnect."""

    def handle(self) -> None:
        """Accept synthetic mail and optionally lose the final acknowledgement."""
        server = cast(MailServer, self.server)
        self.wfile.write(b"220 offline.test SMTP\r\n")
        while raw := self.rfile.readline():
            command = raw.split(b" ", 1)[0].strip().upper()
            server.requests.append(command.decode())
            if command == b"EHLO":
                self.wfile.write(b"250-offline.test\r\n250 AUTH PLAIN\r\n")
            elif command == b"AUTH":
                self.wfile.write(b"235 authenticated\r\n")
            elif command == b"DATA":
                self.wfile.write(b"354 send data\r\n")
                content = bytearray()
                while (line := self.rfile.readline()) and line != b".\r\n":
                    content.extend(line)
                server.messages.append(bytes(content))
                if server.drop:
                    return
                self.wfile.write(b"250 accepted\r\n")
            else:
                self.wfile.write(b"250 ok\r\n")


class IMAPHandler(socketserver.StreamRequestHandler):
    """Provide a read-only UID mailbox with one inert synthetic message."""

    def handle(self) -> None:
        """Support capability, authentication, select, UID search/fetch and logout."""
        server = cast(MailServer, self.server)
        self.wfile.write(b"* OK offline IMAP\r\n")
        while raw := self.rfile.readline():
            tag, _, rest = raw.partition(b" ")
            command = rest.split(b" ", 1)[0].strip().upper()
            server.requests.append(rest.decode().strip())
            if command == b"CAPABILITY":
                self.wfile.write(b"* CAPABILITY IMAP4rev1\r\n")
            elif command in {b"SELECT", b"EXAMINE"}:
                self.wfile.write(
                    b"* 1 EXISTS\r\n* OK [UIDVALIDITY " + server.uidvalidity + b"] stable\r\n"
                )
            elif command == b"UID" and b"SEARCH" in rest.upper():
                self.wfile.write(b"* SEARCH " + server.search_ids + b"\r\n")
            elif command == b"UID" and b"FETCH" in rest.upper():
                body = server.messages[0]
                self.wfile.write(b"* 1 FETCH (UID 1 BODY[] {" + str(len(body)).encode() + b"}\r\n")
                self.wfile.write(body + b")\r\n")
            elif command == b"LOGOUT":
                self.wfile.write(b"* BYE logging out\r\n" + tag + b" OK logout\r\n")
                return
            self.wfile.write(tag + b" OK completed\r\n")


@contextmanager
def fake_server(
    handler: type[socketserver.BaseRequestHandler], *, drop: bool = False
) -> Iterator[MailServer]:
    """Start and stop a local provider fake within a test's lifetime."""
    server = MailServer(handler, drop=drop)
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
    )
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
