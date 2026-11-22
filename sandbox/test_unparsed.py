"""A date or amount that is printed but does not parse raises an exception.

The rule is DESIGN.md's: the reason is unparsed_<field>, kept apart from
missing_<field>, because the field is on the page and needs a person to read
it. Before this rule an unparsed field went straight through as a silent
error. An unparsed invoice date also stops terms from setting a due date, so
that invoice carries both unparsed_invoice_date and missing_due_date.

Beside every unparsed field, the result carries the printed text as
printed: {field: text}. The read record, the host reply, the page, and every
scoring run keep it; the host's log line does not, because it is a field
value.

No network call. Textract gets a stand-in client, Docling a stand-in reply.
Nothing here reads a holdout.

Run from the checkout:
  python sandbox/test_unparsed.py
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
CHECKOUT = HERE.parent
for path in (CHECKOUT, HERE):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import page  # noqa: E402
import read_invoice  # noqa: E402
from read_invoice import exception_reasons, parsed, read_invoice as read  # noqa: E402
from test_runners import Base, FakeTextract, docling_reply, expense_field, expense_response  # noqa: E402

CLEAN = {
    "supplier": "Harbor Paper Co.",
    "invoice_number": "HP-2207",
    "invoice_date": "03/14/2026",
    "due_date": "04/13/2026",
    "subtotal": "500.00",
    "tax": "40.00",
    "total": "540.00",
}


def reasons(**raw) -> list[str]:
    return exception_reasons(parsed({**CLEAN, **raw}))


class Rule(unittest.TestCase):
    def test_a_clean_read_raises_nothing(self):
        self.assertEqual(reasons(), [])

    def test_each_date_that_does_not_parse(self):
        self.assertEqual(reasons(invoice_date="the fourteenth"), ["unparsed_invoice_date"])
        self.assertEqual(reasons(due_date="TBD"), ["unparsed_due_date"])

    def test_each_amount_that_does_not_parse(self):
        self.assertEqual(reasons(subtotal="five hundred"), ["unparsed_subtotal"])
        self.assertEqual(reasons(tax="pending"), ["unparsed_tax"])
        self.assertEqual(reasons(total="see attached"), ["unparsed_total"])

    def test_unparsed_is_not_missing(self):
        self.assertNotIn("missing_tax", reasons(tax="pending"))
        self.assertEqual(reasons(tax=None), ["missing_tax"])

    def test_unparsed_invoice_date_leaves_terms_no_date(self):
        got = reasons(invoice_date="the fourteenth", due_date=None, terms="Net 30")
        self.assertEqual(got, ["missing_due_date", "unparsed_invoice_date"])

    def test_an_unparsed_amount_does_not_also_claim_a_mismatch(self):
        self.assertEqual(reasons(total="see attached"), ["unparsed_total"])

    def test_text_fields_are_never_unparsed(self):
        got = reasons(supplier="???", invoice_number="#")
        self.assertFalse([r for r in got if r.startswith("unparsed_")])


class Readers(Base):
    def test_textract_unparsed_date_goes_to_a_person(self):
        response = expense_response([
            expense_field("VENDOR_NAME", "Harbor Paper Co."),
            expense_field("INVOICE_RECEIPT_ID", "HP-2207"),
            expense_field("INVOICE_RECEIPT_DATE", "the fourteenth"),
            expense_field("DUE_DATE", "04/13/2026"),
            expense_field("SUBTOTAL", "$500.00"),
            expense_field("TAX", "$40.00"),
            expense_field("TOTAL", "$540.00"),
        ])
        out = read(self.pdf, client=FakeTextract(response))
        self.assertEqual(out["invoice_date"], "unparsed")
        self.assertIs(out["exception"], True)
        self.assertEqual(out["exception_reasons"], ["unparsed_invoice_date"])

    def test_docling_unparsed_date_goes_to_a_person(self):
        doc = {"texts": [
            {"text": "Harbor Paper Co.", "label": "section_header"},
            {"text": "Invoice Number: HP-2207", "label": "text"},
            {"text": "Invoice Date: the fourteenth", "label": "text"},
            {"text": "Due Date: 04/13/2026", "label": "text"},
            {"text": "Subtotal $500.00", "label": "text"},
            {"text": "Tax $40.00", "label": "text"},
            {"text": "Total $540.00", "label": "text"},
        ], "tables": []}
        with mock.patch.object(read_invoice.urllib.request, "urlopen", return_value=docling_reply(doc)):
            out = read(self.pdf, "docling")
        self.assertEqual(out["invoice_date"], "unparsed")
        self.assertEqual(out["exception_reasons"], ["unparsed_invoice_date"])
        self.assertEqual(out["printed"], {"invoice_date": "the fourteenth"})


class Printed(unittest.TestCase):
    """The printed text rides beside every unparsed date or amount."""

    def printed(self, **raw) -> dict:
        merged = {**CLEAN, **raw}
        return read_invoice.printed_text(merged, parsed(merged))

    def test_nothing_failed_nothing_printed(self):
        self.assertEqual(self.printed(), {})
        self.assertEqual(read_invoice.printed_text(None, {}), {})

    def test_each_unparsed_field_keeps_its_text(self):
        self.assertEqual(self.printed(due_date="TBD"), {"due_date": "TBD"})
        self.assertEqual(self.printed(tax="pending", total="see  attached"), {"tax": "pending", "total": "see attached"})

    def test_a_missing_field_is_not_printed(self):
        self.assertEqual(self.printed(tax=None), {})

    def test_a_total_from_the_amount_due_shows_that_text(self):
        self.assertEqual(self.printed(total=None, amount_due="call us"), {"total": "call us"})

    def test_the_read_record_keeps_it(self):
        result = {name: None for name in read_invoice.FIELDS}
        result.update(invoice_date="unparsed", exception=True, exception_reasons=["unparsed_invoice_date"],
                      printed={"invoice_date": "the fourteenth"}, error=None, runner="textract")
        record = read_invoice.make_record(Path("x.pdf"), result, 1.0)
        self.assertEqual(record["printed"], {"invoice_date": "the fourteenth"})


class Host(unittest.TestCase):
    def test_reply_carries_it_and_the_log_line_does_not(self):
        import base64
        import json
        from types import SimpleNamespace

        sys.path.insert(0, str(CHECKOUT / "host"))
        import handler

        result = {name: None for name in read_invoice.FIELDS}
        result.update(invoice_date="unparsed", exception=True, exception_reasons=["unparsed_invoice_date"],
                      printed={"invoice_date": "the fourteenth"}, error=None, runner="textract", pages=1)
        event = {"requestContext": {"http": {"method": "POST"}}, "isBase64Encoded": True,
                 "body": base64.b64encode(b"%PDF-1.4 stand-in").decode("ascii")}
        with mock.patch.object(handler, "read_invoice", return_value=result), mock.patch("builtins.print") as printed:
            out = handler.handler(event, SimpleNamespace(aws_request_id="req-1"))
        self.assertEqual(json.loads(out["body"])["printed"], {"invoice_date": "the fourteenth"})
        self.assertNotIn("fourteenth", printed.call_args[0][0])


class Scorer(unittest.TestCase):
    ROW = {"id": "x", "file": "unused.pdf", **{name: None for name in read_invoice.FIELDS}, "exception": True}

    def test_a_local_run_records_it(self):
        import score

        item = score.judged(self.ROW, {**CLEAN, "invoice_date": "the fourteenth"}, "textract", 1.0, None)
        self.assertEqual(item["printed"], {"invoice_date": "the fourteenth"})

    def test_a_hosted_run_takes_the_hosts(self):
        import score

        body = {name: CLEAN[name] for name in read_invoice.FIELDS}
        body.update(invoice_date="unparsed", exception=True, exception_reasons=["unparsed_invoice_date"],
                    printed={"invoice_date": "the fourteenth"}, error=None, runner="textract-analyze-expense", pages=1)
        host = ("https://abc123.lambda-url.us-east-1.on.aws/", object())
        with mock.patch.object(score.host_call, "call_reader", return_value=(200, body)):
            item = score.score_invoice("hosted", self.ROW, Path("unused"), host=host)
        self.assertEqual(item["printed"], {"invoice_date": "the fourteenth"})
        self.assertNotIn("host_disagrees", item)


class Page(unittest.TestCase):
    def test_every_unparsed_reason_has_words(self):
        for name in read_invoice.DATE_FIELDS + read_invoice.MONEY_FIELDS:
            text = page.reason_text(f"unparsed_{name}")
            self.assertIn("could not be read", text, name)

    def test_the_page_shows_what_was_printed(self):
        row = {name: CLEAN[name] for name in read_invoice.FIELDS}
        row.update(id="a" * 32, file_name="x.pdf", read_at="2026-11-22T00:00:00+00:00", seconds=1.0,
                   invoice_date="unparsed", exception=True, exception_reasons=["unparsed_invoice_date"],
                   printed={"invoice_date": "<the fourteenth>"}, error=None)
        body = page.render_one(row, [row])
        self.assertIn("Could not read: &lt;the fourteenth&gt;", body)


if __name__ == "__main__":
    unittest.main()
