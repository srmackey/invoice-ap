# invoice-ap

A synthetic invoice reader. How it works is `DESIGN.md`. How a score is measured is `AGENTS.md`.

## Scores

The invoices are synthetic. Each score is the sandbox run named with it.

- Generated set (`sandbox/fixtures/`, ten invoices in one layout): the Claude run in `sandbox/runs/2026-10-22-claude.json`. Field accuracy 70/70, straight-through 7/10, invoice accuracy 10/10, 8.3 seconds per invoice. The Grok run is a second pass and is not this score.
- Harder set (`sandbox/hard/`, ten scans, ten phone photos, and four invoices in a second layout): the Docling run in `sandbox/runs/2026-10-23-hard-docling.json`. Field accuracy 168/168, straight-through 15/24, invoice accuracy 24/24, 9.7 seconds per invoice.

## Install

Python 3.10 or newer. The reader has two runners. Amazon Textract AnalyzeExpense is the default. A local Docling container is the backup, for reading without AWS. The sandbox tools that draw fixtures are separate, and they are not this install.

### Textract (default)

Install boto3 and sign in to an AWS profile that may call `textract:AnalyzeExpense`:

```
pip install boto3 "botocore[crt]"
aws login --profile <profile>
```

`botocore[crt]` is needed for profiles signed in with `aws login`. Each read is one call to Textract, billed per page (one cent a page at list price in us-east-1). The region is `AWS_REGION` when it is set, and us-east-1 otherwise.

One PDF through the command:

```
python read_invoice.py sandbox/fixtures/01-northline.pdf --profile <profile>
```

Without `--profile`, boto3 uses its usual credential chain.

### Docling (backup)

Docker, and nothing beyond the Python standard library. From this folder, start Docling:

```
docker compose -f services/docling/compose.yaml up -d
```

The first start pulls the image. Docling listens at `127.0.0.1:5001`. Then:

```
python read_invoice.py sandbox/fixtures/01-northline.pdf --runner docling
```

### The command and the page

The command prints the seven fields and the exception, and appends one record to `reads/reads.jsonl`. The record names the runner that read it. `sandbox/score.py` uses that same reader.

The page:

```
python page.py --profile <profile>
python page.py --runner docling
```

Open `http://127.0.0.1:8765`. The page lists those records, opens one, and shows the share that raised no exception and the time per invoice. It accepts a PDF and reads it with the same reader and runner as the command.

## Host

The hosted reader is one AWS Lambda function behind a function URL that requires IAM credentials. It runs `read_invoice.py` with Textract, so it returns what the command returns, plus `pages` and `seconds`. There is no open endpoint, and nothing runs between calls. The function's role may call `textract:AnalyzeExpense` and write its own logs, nothing else. Each read writes one log line with its timing, page count, exception reasons, and error, and no field values.

Deploy with the AWS CLI and a profile that can create IAM roles, Lambda functions, and S3 buckets. From this folder:

```
aws cloudformation deploy --template-file infra/artifacts.yaml --stack-name invoice-ap-artifacts --profile <profile> --region us-east-1
python host/build.py
aws cloudformation package --template-file infra/reader.yaml --s3-bucket invoice-ap-artifacts-<account id> --s3-prefix reader --output-template-file build/reader.packaged.yaml --profile <profile> --region us-east-1
aws cloudformation deploy --template-file build/reader.packaged.yaml --stack-name invoice-ap-reader --capabilities CAPABILITY_NAMED_IAM --profile <profile> --region us-east-1
```

The first stack is a private bucket for packaged code. The URL is the `ReaderUrl` output of the second stack.

To send one invoice, install what `host/requirements.txt` lists (boto3, and `botocore[crt]` for profiles signed in with `aws login` or roles assumed from one), and use a profile that has `lambda:InvokeFunctionUrl` and `lambda:InvokeFunction` on the function:

```
pip install -r host/requirements.txt
python host/call.py <reader url> sandbox/fixtures/01-northline.pdf --profile <profile>
```

`call.py` runs on its own, without the rest of this checkout. `sandbox/check_clean_install.py <reader url>` proves that: it installs `host/requirements.txt` into a throwaway venv, sends one invoice with `call.py` alone, and deletes the venv.

### Who may call it

`infra/test-callers.yaml` is three roles for testing access. They hold no keys; a principal in the account assumes one for a short session. `invoice-ap-test-caller` has the two invoke permissions on this function, `invoice-ap-test-url-only` has `InvokeFunctionUrl` alone, and `invoice-ap-test-no-access` has none.

```
aws cloudformation deploy --template-file infra/test-callers.yaml --stack-name invoice-ap-test-callers --capabilities CAPABILITY_NAMED_IAM --profile <profile> --region us-east-1
```

Give each role a profile in `~/.aws/config` named `invoice-ap-caller`, `invoice-ap-url-only`, and `invoice-ap-no-access`, with `role_arn` set to the role and `source_profile` set to your own profile. Then, after every deploy of the reader:

```
set INVOICE_AP_READER_URL=<reader url>
python sandbox/test_live_auth.py
```

The caller role gets the seven fields. The other two get a 403 saying both permissions are required, and an unsigned request gets a plain 403. The function has no resource policy, so this shows IAM is enforced and nothing else opened the URL. Cross-account access is not tested.

To score the function that is deployed, run `sandbox/score.py --runner hosted --url <reader url> --profile <profile> --labels <set>`. How that run is recorded is in `sandbox/README.md`.

An exception is still a 200: it is the answer. A body that is not a PDF is a 400. A Textract failure is a 502 with the reason. Textract reads a PDF of one page in a single call, so a longer PDF is refused with `UnsupportedDocumentException`. One page is a known limit, and the scored sets are one-page invoices.

## Sandbox

`sandbox/` holds the labeled fixture set and whatever runner the work needs. No runner is chosen. It is not a customer environment.

## Release

No release is cut. The install above is how another computer runs the page and one PDF. A clean start on this machine brought Docling down and back up, read one PDF, and the page showed that read.
