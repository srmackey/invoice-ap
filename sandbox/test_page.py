"""The local page lists reads, opens one, and shows the two numbers.

Run from the checkout:
  python sandbox/test_page.py
"""

from __future__ import annotations

import http.client
import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

CHECKOUT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(CHECKOUT))

import page  # noqa: E402


def record(ident: str, **over) -> dict:
    row = {
        "file_name": "01-northline.pdf",
        "id": ident,
        "read_at": "2026-10-23T20:13:33+00:00",
        "seconds": 6.2,
        "supplier": "Northline Supply Co.",
        "invoice_number": "NL-10442",
        "invoice_date": "2026-02-03",
        "due_date": "2026-03-05",
        "subtotal": "1260.00",
        "tax": "100.80",
        "total": "1360.80",
        "exception": False,
        "exception_reasons": [],
        "error": None,
    }
    row.update(over)
    return row


class PageTests(unittest.TestCase):
    def write(self, root: Path, rows: list[dict], extra: str = "") -> Path:
        path = root / "reads.jsonl"
        lines = [json.dumps(row) for row in rows]
        if extra:
            lines.append(extra)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    def test_list_shows_the_share_the_time_and_each_read(self):
        clear = record("a" * 32)
        flagged = record(
            "b" * 32,
            file_name="08-westwind.pdf",
            seconds=4.0,
            due_date=None,
            exception=True,
            exception_reasons=["missing_due_date"],
        )
        got = page.summary([clear, flagged])
        self.assertEqual(got["clear"], 1)
        self.assertEqual(got["count"], 2)
        self.assertEqual(got["seconds"], 5.1)
        with self.subTest("html"):
            body = page.render_list([clear, flagged], 0)
            self.assertIn("1 of 2 raised no exception", body)
            self.assertIn("5.1 seconds per invoice", body)
            self.assertLess(body.index("08-westwind.pdf"), body.index("01-northline.pdf"))
            self.assertIn(f'/read/{"b" * 32}', body)

    def test_open_one_shows_the_fields_and_the_same_numbers(self):
        row = record("c" * 32, supplier="<Northline & Co>")
        body = page.render_one(row, [row])
        self.assertIn("1 of 1 raised no exception", body)
        self.assertIn("6.2 seconds per invoice", body)
        self.assertIn("&lt;Northline &amp; Co&gt;", body)
        self.assertIn("NL-10442", body)
        self.assertIn("1360.80", body)
        self.assertIn("<dt>Exception</dt><dd>No</dd>", body)
        self.assertIn('href="/"', body)

    def test_an_exception_is_the_reason(self):
        row = record(
            "d" * 32,
            exception=True,
            exception_reasons=["due_before_invoice", "total_mismatch"],
            error=None,
        )
        body = page.render_one(row, [row])
        self.assertIn("Due date is before the invoice date", body)
        self.assertIn("Subtotal plus tax does not match the total", body)
        self.assertIn("0 of 1 raised no exception", body)

    def test_a_tool_failure_is_the_error(self):
        row = record("e" * 32, exception=True, error="docling request failed: refused")
        body = page.render_one(row, [row])
        self.assertIn("docling request failed: refused", body)
        self.assertIn("· Error ·", page.render_list([row], 0))

    def test_empty_and_a_bad_line(self):
        self.assertIsNone(page.summary([]))
        self.assertIn("No reads yet.", page.render_list([], 0))
        self.assertIn("1 line could not be read.", page.render_list([], 1))
        self.assertIn("2 lines could not be read.", page.render_list([], 2))
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write(Path(tmp), [record("abc123")], extra="not json")
            reads, skipped = page.load_reads(path)
            self.assertEqual(len(reads), 1)
            self.assertEqual(skipped, 1)
            status, _kind, body = page.respond("/", path)
            self.assertEqual(status, 200)
            self.assertIn("1 line could not be read.", body)

    def test_missing_read(self):
        status, _kind, body = page.respond("/read/" + "f" * 32, Path("does-not-exist.jsonl"))
        self.assertEqual(status, 404)
        self.assertIn("That read is not here.", body)

    def test_http_list_then_open(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write(Path(tmp), [record("abc123")])
            with mock.patch.object(page, "READS_PATH", path):
                server = ThreadingHTTPServer(("127.0.0.1", 0), page.Handler)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                port = server.server_address[1]
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/") as resp:
                        listing = resp.read().decode("utf-8")
                        self.assertEqual(resp.status, 200)
                    self.assertIn("1 of 1 raised no exception", listing)
                    self.assertIn("6.2 seconds per invoice", listing)
                    self.assertIn('href="/read/abc123"', listing)
                    self.assertIn('action="/read"', listing)
                    self.assertIn('name="pdf"', listing)
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/read/abc123") as resp:
                        one = resp.read().decode("utf-8")
                    self.assertIn("Northline Supply Co.", one)
                    self.assertIn("1 of 1 raised no exception", one)
                    with self.assertRaises(urllib.error.HTTPError) as caught:
                        urllib.request.urlopen(f"http://127.0.0.1:{port}/read/missing")
                    self.assertEqual(caught.exception.code, 404)
                    missed = caught.exception.read().decode("utf-8")
                    caught.exception.close()
                    self.assertIn("That read is not here.", missed)
                finally:
                    server.shutdown()
                    server.server_close()


READ = {
    "supplier": "Northline Supply Co.",
    "invoice_number": "NL-10442",
    "invoice_date": "2026-02-03",
    "due_date": "2026-03-05",
    "subtotal": "1260.00",
    "tax": "100.80",
    "total": "1360.80",
    "exception": False,
    "exception_reasons": [],
    "error": None,
}


def multipart(filename: str, data: bytes, boundary: str = "----iap") -> tuple[str, bytes]:
    head = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="pdf"; filename="{filename}"\r\n'
        f"Content-Type: application/pdf\r\n\r\n"
    ).encode()
    return boundary, head + data + f"\r\n--{boundary}--\r\n".encode()


class UploadTests(unittest.TestCase):
    def test_parser_keeps_the_pdf_bytes(self):
        raw = b"%PDF-1.4\r\n%%EOF\r\n"
        boundary, body = multipart(r"..\secret\01-northline.pdf", raw)
        parsed = page.parse_pdf_upload(f"multipart/form-data; boundary={boundary}", body)
        self.assertIsNotNone(parsed)
        filename, data = parsed
        self.assertEqual(page.safe_pdf_name(filename), "01-northline.pdf")
        self.assertEqual(data, raw)
        self.assertIsNone(page.safe_pdf_name("notes.txt"))

    def test_post_keeps_the_record_and_shows_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "reads.jsonl"
            boundary, body = multipart("01-northline.pdf", b"%PDF-1.4")
            with mock.patch.object(page, "READS_PATH", path), mock.patch(
                "read_invoice.READS_PATH", path
            ), mock.patch.object(page, "read_invoice", return_value=dict(READ)):
                server = ThreadingHTTPServer(("127.0.0.1", 0), page.Handler)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                port = server.server_address[1]
                try:
                    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
                    conn.request(
                        "POST",
                        "/read",
                        body=body,
                        headers={
                            "Content-Type": f"multipart/form-data; boundary={boundary}",
                            "Content-Length": str(len(body)),
                        },
                    )
                    posted = conn.getresponse()
                    location = posted.getheader("Location")
                    posted.read()
                    self.assertEqual(posted.status, 303)
                    saved = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
                    self.assertEqual(len(saved), 1)
                    row = saved[0]
                    self.assertEqual(row["file_name"], "01-northline.pdf")
                    for key, value in READ.items():
                        self.assertEqual(row[key], value)
                    self.assertEqual(len(row["id"]), 32)
                    self.assertTrue(str(row["read_at"]).endswith("+00:00"))
                    self.assertEqual(row["seconds"], round(row["seconds"], 1))
                    conn.request("GET", location)
                    opened = conn.getresponse()
                    shown = opened.read().decode("utf-8")
                    self.assertEqual(opened.status, 200)
                    self.assertIn("Northline Supply Co.", shown)
                    self.assertIn("NL-10442", shown)
                    self.assertIn("1360.80", shown)
                    self.assertIn("<dt>Exception</dt><dd>No</dd>", shown)
                    self.assertIn("1 of 1 raised no exception", shown)
                finally:
                    server.shutdown()
                    server.server_close()

    def test_a_non_pdf_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "reads.jsonl"
            boundary, body = multipart("notes.txt", b"hello")
            with mock.patch.object(page, "READS_PATH", path), mock.patch(
                "read_invoice.READS_PATH", path
            ), mock.patch.object(page, "read_invoice") as reader:
                server = ThreadingHTTPServer(("127.0.0.1", 0), page.Handler)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                port = server.server_address[1]
                try:
                    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
                    conn.request(
                        "POST",
                        "/read",
                        body=body,
                        headers={
                            "Content-Type": f"multipart/form-data; boundary={boundary}",
                            "Content-Length": str(len(body)),
                        },
                    )
                    posted = conn.getresponse()
                    text = posted.read().decode("utf-8")
                    self.assertEqual(posted.status, 400)
                    self.assertIn("Choose a PDF.", text)
                    self.assertFalse(path.exists())
                    reader.assert_not_called()
                finally:
                    server.shutdown()
                    server.server_close()


if __name__ == "__main__":
    unittest.main()
