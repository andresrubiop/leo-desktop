"""Mail contracts use fixtures; the TLS test connects to an actual local socket.
This does not authenticate Gmail, Microsoft or a user's real mailbox.
"""
import os
from pathlib import Path
import socketserver
import ssl
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

from leo.mail import Mail, imap_read
from leo.usage.storage import read


class MemorySecrets:
    def __init__(self):
        self.values = {}

    def save_password(self, ident, password):
        self.values[ident] = password

    def password(self, ident):
        return self.values[ident]

    def delete(self, ident):
        self.values.pop(ident)


class MailTests(unittest.TestCase):
    def test_opt_in_secret_separation_revoke_and_delete(self):
        with tempfile.TemporaryDirectory() as directory:
            store = MemorySecrets()
            mail = Mail(None, Path(directory), store)
            with patch.object(mail, "discover", return_value=[]):
                mail.add("Prueba", "mail.example.com", 993, "test@example.com", "fake-password")
                account = mail.accounts()["accounts"][0]
                ident = account["id"]
                self.assertFalse(account["enabled"])
                self.assertNotIn("fake-password", mail.path.read_text())
                with self.assertRaises(ValueError):
                    mail.check(ident)
                mail.configure(ident, True, True, 60)
                fake = {"unread": 3, "new": 1, "messages": [], "marker": 3, "uidvalidity": "1"}
                with patch("leo.mail.imap_read", return_value=fake), patch.object(mail, "notify") as notify:
                    mail.check(ident)
                    notify.assert_called_once_with("Prueba", 1)
                with patch("leo.mail.imap_read", side_effect=RuntimeError("secret in server error")):
                    result = mail.check(ident)["status"]
                self.assertNotIn("secret", result["error"])
                self.assertEqual(result["unread"], 3)
                mail.configure(ident, False, False, 60)
                self.assertEqual(mail.summary()["connected"], 0)
                mail.remove(ident)
                self.assertFalse(store.values)
                self.assertNotIn(ident, read(mail.cache_path))

    def test_revocation_during_query_suppresses_notifications(self):
        with tempfile.TemporaryDirectory() as directory:
            mail = Mail(None, Path(directory), MemorySecrets())
            with patch.object(mail, "discover", return_value=[]):
                mail.add("Prueba", "mail.example.com", 993, "u", "fake")
                ident = mail.accounts()["accounts"][0]["id"]
                mail.configure(ident, True, True, 60)
                def query(*_args):
                    mail.configure(ident, False, False, 60)
                    return {"new": 1}
                with patch("leo.mail.imap_read", side_effect=query), patch.object(mail, "notify") as notify:
                    mail.check(ident)
                    notify.assert_not_called()

    def test_imap_tls_readonly_baseline_new_mail_and_dedup(self):
        with tempfile.TemporaryDirectory() as directory:
            cert, key = Path(directory) / "cert.pem", Path(directory) / "key.pem"
            subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                            "-keyout", str(key), "-out", str(cert), "-subj", "/CN=localhost",
                            "-addext", "subjectAltName=DNS:localhost"], check=True, capture_output=True)
            server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            server_context.load_cert_chain(cert, key)
            state = {"uids": [1], "commands": [], "validity": "10"}

            class Handler(socketserver.StreamRequestHandler):
                def handle(self):
                    self.wfile.write(b"* OK IMAP test\r\n")
                    while line := self.rfile.readline():
                        tag, command = line.decode().strip().split(" ", 1)
                        state["commands"].append(command.split(" ", 1)[0])
                        if command == "CAPABILITY":
                            self.wfile.write(b"* CAPABILITY IMAP4rev1\r\n")
                        elif command.startswith("LOGIN"):
                            pass
                        elif command.startswith("EXAMINE"):
                            self.wfile.write(f"* {len(state['uids'])} EXISTS\r\n* OK [UIDVALIDITY {state['validity']}] UIDs\r\n".encode())
                        elif command.startswith("STATUS"):
                            self.wfile.write(f"* STATUS INBOX (UIDNEXT {max(state['uids']) + 1})\r\n".encode())
                        elif command.startswith("UID SEARCH"):
                            self.wfile.write(("* SEARCH " + " ".join(map(str, state["uids"])) + "\r\n").encode())
                        elif command.startswith("UID FETCH"):
                            assert "BODY.PEEK" in command
                            header = b"From: Test <sender@example.test>\r\nSubject: Correo nuevo\r\n\r\n"
                            self.wfile.write(f"* 1 FETCH (BODY[HEADER.FIELDS (FROM SUBJECT DATE)] {{{len(header)}}}\r\n".encode() + header + b")\r\n")
                        elif command == "LOGOUT":
                            self.wfile.write(b"* BYE\r\n" + tag.encode() + b" OK\r\n")
                            break
                        else:
                            raise AssertionError(command)
                        self.wfile.write(tag.encode() + b" OK completed\r\n")

            class Server(socketserver.TCPServer):
                def get_request(self):
                    connection, address = super().get_request()
                    return server_context.wrap_socket(connection, server_side=True), address

            with Server(("127.0.0.1", 0), Handler) as server:
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                account = {"host": "localhost", "port": server.server_address[1], "username": "test"}
                context = ssl.create_default_context(cafile=str(cert))
                try:
                    first = imap_read(account, "test-password", {}, context=context)
                    self.assertEqual(first["new"], 0)
                    self.assertEqual(first["unread"], 1)
                    state["uids"].append(2)
                    second = imap_read(account, "test-password", first, context=context)
                    self.assertEqual(second["new"], 1)
                    self.assertEqual(second["messages"][0]["subject"], "Correo nuevo")
                    third = imap_read(account, "test-password", second, context=context)
                    self.assertEqual(third["new"], 0)
                    state["validity"] = "11"
                    reset = imap_read(account, "test-password", third, context=context)
                    self.assertEqual(reset["new"], 0)
                    self.assertNotIn("SELECT", state["commands"])
                    self.assertNotIn("STORE", state["commands"])
                finally:
                    server.shutdown()
                    thread.join()


if __name__ == "__main__":
    unittest.main()
