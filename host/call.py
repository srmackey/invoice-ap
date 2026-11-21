"""Send one invoice to the hosted reader and print what comes back.

The function URL takes IAM credentials, so the request is signed with SigV4
for the lambda service. The caller needs lambda:InvokeFunctionUrl and
lambda:InvokeFunction on the function. Needs boto3.

  python host/call.py <function url> path/to/invoice.pdf --profile <profile>
"""

from __future__ import annotations

import argparse
import json
import re
import urllib.error
import urllib.request
from pathlib import Path

URL_REGION = re.compile(r"\.lambda-url\.([a-z0-9-]+)\.on\.aws")


def region_of(url: str) -> str:
    found = URL_REGION.search(url)
    if not found:
        raise ValueError(f"not a Lambda function URL: {url}")
    return found.group(1)


def signed_request(url: str, data: bytes, credentials, region: str) -> urllib.request.Request:
    from botocore.auth import SigV4Auth
    from botocore.awsrequest import AWSRequest

    aws = AWSRequest(method="POST", url=url, data=data, headers={"Content-Type": "application/pdf"})
    SigV4Auth(credentials, "lambda", region).add_auth(aws)
    return urllib.request.Request(url, data=data, headers=dict(aws.headers.items()), method="POST")


def session_credentials(url: str, profile: str | None = None):
    """Credentials for signing calls to this URL. Refreshable ones refresh on
    each get_frozen_credentials(), so a long scoring run can reuse them."""
    import boto3

    credentials = boto3.Session(profile_name=profile, region_name=region_of(url)).get_credentials()
    if credentials is None:
        raise RuntimeError("no AWS credentials found")
    return credentials


def call_reader(
    url: str, pdf: Path, profile: str | None = None, timeout: int = 60, credentials=None
) -> tuple[int, dict]:
    region = region_of(url)
    if credentials is None:
        credentials = session_credentials(url, profile)
    request = signed_request(url, pdf.read_bytes(), credentials.get_frozen_credentials(), region)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8", "replace")
        try:
            return exc.code, json.loads(text)
        except json.JSONDecodeError:
            return exc.code, {"error": text or exc.reason}


def main() -> None:
    parser = argparse.ArgumentParser(description="Send one invoice to the hosted reader.")
    parser.add_argument("url")
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--profile", help="AWS profile to sign with.")
    args = parser.parse_args()
    if not args.pdf.is_file():
        raise SystemExit(f"no file {args.pdf}")
    status, body = call_reader(args.url, args.pdf, args.profile)
    print(json.dumps({"status": status, **body}, indent=2))
    if status != 200:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
