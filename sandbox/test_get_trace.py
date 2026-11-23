"""get_trace.py's handling of span records, without AWS.

Run from the checkout:
  python sandbox/test_get_trace.py
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

SANDBOX = Path(__file__).resolve().parent
if str(SANDBOX) not in sys.path:
    sys.path.insert(0, str(SANDBOX))

import get_trace  # noqa: E402

TRACE = "1-6b03bc70-22469f6173b462ec0c57d302"


def span(span_id, name, start, end, parent=None, **attributes):
    return {
        "spanId": span_id,
        "parentSpanId": parent,
        "name": name,
        "kind": "INTERNAL",
        "startTimeUnixNano": start,
        "endTimeUnixNano": end,
        "attributes": attributes,
    }


class Ids(unittest.TestCase):
    def test_an_xray_id_becomes_the_otel_id(self):
        self.assertEqual(get_trace.otel_id(TRACE), "6b03bc7022469f6173b462ec0c57d302")

    def test_the_start_comes_from_the_id(self):
        self.assertEqual(get_trace.started(TRACE), 0x6B03BC70)

    def test_a_malformed_id_is_refused(self):
        for bad in ("6b03bc7022469f6173b462ec0c57d302", "1-6b03bc70", "2-6b03bc70-22469f6173b462ec0c57d302"):
            with self.assertRaises(ValueError):
                get_trace.otel_id(bad)


class Spans(unittest.TestCase):
    def test_the_finished_copy_of_a_span_is_kept(self):
        records = [span("a", "service", 1, None), span("a", "service", 1, 9), span("b", "handler", 2, 8, "a")]
        kept = get_trace.finished_spans(records)
        self.assertEqual([s["spanId"] for s in kept], ["a", "b"])
        self.assertEqual(kept[0]["endTimeUnixNano"], 9)

    def test_an_access_key_id_is_redacted(self):
        call = span("c", "Textract.AnalyzeExpense", 3, 7, "b", **{"aws.auth.account.access_key": "ASIAEXAMPLE", "rpc.method": "AnalyzeExpense"})
        out = get_trace.redacted(call)
        self.assertEqual(out["attributes"]["aws.auth.account.access_key"], "<redacted>")
        self.assertEqual(out["attributes"]["rpc.method"], "AnalyzeExpense")

    def test_the_account_and_the_url_id_are_scrubbed(self):
        text = '{"arn": "arn:aws:lambda:us-east-1:123456789012:function:x", "host": "abcdefghij0123456789abcdefghij01.lambda-url.us-east-1.on.aws"}'
        out = get_trace.scrubbed(text, "123456789012")
        self.assertNotIn("123456789012", out)
        self.assertNotIn("abcdefghij0123456789abcdefghij01", out)
        self.assertIn("<url-id>.lambda-url.us-east-1.on.aws", out)

    def test_the_outline_nests_children_under_parents(self):
        spans = [
            span("a", "service", 0, 3_000_000),
            span("b", "runner", 0, 2_000_000, "a"),
            span("c", "Textract.AnalyzeExpense", 0, 1_500_000, "b"),
            span("d", "mapping", 2_000_000, 2_500_000, "a"),
        ]
        self.assertEqual(
            get_trace.outline(spans),
            [
                "service (INTERNAL, 3.0 ms)",
                "  runner (INTERNAL, 2.0 ms)",
                "    Textract.AnalyzeExpense (INTERNAL, 1.5 ms)",
                "  mapping (INTERNAL, 0.5 ms)",
            ],
        )


if __name__ == "__main__":
    unittest.main()
