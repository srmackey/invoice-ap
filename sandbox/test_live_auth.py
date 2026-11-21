"""Who may call the hosted reader. A live test against the deployed URL.

Run after every deploy of infra/reader.yaml. It needs the stack
invoice-ap-test-callers and three profiles that assume its roles
(invoice-ap-caller, invoice-ap-url-only, invoice-ap-no-access), plus the
reader's URL in INVOICE_AP_READER_URL. Without them it skips. One run reads
one page through Textract, so it costs one cent at list price.

  set INVOICE_AP_READER_URL=<ReaderUrl output>
  python sandbox/test_live_auth.py

Same-account callers get in through their own IAM policy or the function's
resource policy. The function has no resource policy, so these cases show
that IAM is enforced and that nothing else opened the URL. Cross-account
access is not tested.
"""

from __future__ import annotations

import os
import sys
import unittest
import urllib.error
import urllib.request
from pathlib import Path

CHECKOUT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(CHECKOUT / "host"))

URL = os.environ.get("INVOICE_AP_READER_URL")
PDF = CHECKOUT / "sandbox" / "fixtures" / "01-northline.pdf"
PROFILES = ("invoice-ap-caller", "invoice-ap-url-only", "invoice-ap-no-access")
NEEDS_BOTH = "requires both the lambda:InvokeFunctionUrl and lambda:InvokeFunction permissions"


def missing() -> str | None:
    if not URL:
        return "INVOICE_AP_READER_URL is not set"
    try:
        import boto3
    except ImportError:
        return "boto3 is not installed"
    have = set(boto3.Session().available_profiles)
    absent = [name for name in PROFILES if name not in have]
    return f"profiles not configured: {', '.join(absent)}" if absent else None


SKIP = missing()


@unittest.skipIf(SKIP, SKIP or "")
class WhoMayCall(unittest.TestCase):
    def call_as(self, profile: str) -> tuple[int, dict]:
        from call import call_reader

        return call_reader(URL, PDF, profile)

    def test_the_caller_role_reads_the_invoice(self):
        status, body = self.call_as("invoice-ap-caller")
        self.assertEqual(status, 200, body)
        self.assertEqual(body["invoice_number"], "NL-10442")
        self.assertIsNone(body["error"])

    def test_invoke_function_url_alone_is_refused(self):
        status, body = self.call_as("invoice-ap-url-only")
        self.assertEqual(status, 403, body)
        self.assertIn(NEEDS_BOTH, body.get("Message", ""))

    def test_a_role_with_no_lambda_permissions_is_refused(self):
        status, body = self.call_as("invoice-ap-no-access")
        self.assertEqual(status, 403, body)
        self.assertIn(NEEDS_BOTH, body.get("Message", ""))

    def test_an_unsigned_request_is_refused(self):
        request = urllib.request.Request(
            URL, data=PDF.read_bytes(), headers={"Content-Type": "application/pdf"}, method="POST"
        )
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request, timeout=30)
        self.assertEqual(caught.exception.code, 403)
        self.assertNotIn(NEEDS_BOTH, caught.exception.read().decode("utf-8", "replace"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
