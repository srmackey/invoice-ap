# invoice-ap

Reads one supplier invoice and returns the seven fields an accounts-payable clerk would key, with an exception when the page needs a person. How it works is `DESIGN.md`.

The generated invoices are synthetic, and INV-CDIP is real invoices from a public archive. None of it is a customer's books, and nothing here runs a business.

## Benchmark

On a labeled set:

- Field accuracy. Each field the set labels is right or wrong, and a missing field counts as wrong. The score is correct fields divided by labeled fields.
- Straight-through rate. The share of invoices the run finished without raising an exception.
- Invoice accuracy. The share of invoices with every labeled field right. A clerk works one invoice at a time, so this is how many would need no correction. Each run file also lists silent errors: invoices that raised no exception and still read a field wrong.
- Time per invoice. The scoring run's elapsed time divided by the number of invoices.

How a field is matched is in `DESIGN.md`.

Each set is written and labeled by its own script in `sandbox/`, and the PDFs and labels are never edited by hand. A change to a labeling rule regenerates the affected sets and re-scores them before a score cites them. A holdout is frozen before any runner reads it, scored once per runner, and never used to change the reader. A miss on a holdout is reported, not fixed against it, and a later fix is scored on a new holdout.

## Scores

Each score is the sandbox run named with it. The hosted runs read each invoice through the deployed function, which calls Textract. The generated holdout and INV-CDIP runs are the reader as it was on 2026-11-21; the two later holdouts were scored after the reader learned more date forms, payment terms in six languages, and foreign currency.

- Generated set (`sandbox/fixtures/`, ten invoices in one layout): the Claude run in `sandbox/runs/2026-10-22-claude.json`. Field accuracy 70/70, straight-through 7/10, invoice accuracy 10/10, 8.3 seconds per invoice. The Grok run is a second pass and is not this score.
- Harder set (`sandbox/hard/`, ten scans, ten phone photos, and four invoices in a second layout): the hosted run in `sandbox/runs/2026-11-21-hard-hosted.json`. Field accuracy 168/168, straight-through 15/24, invoice accuracy 24/24, no silent errors, 2.9 seconds and about one cent per invoice.
- Generated holdout (`sandbox/holdout/`, 100 one-page invoices in twelve layouts, clean, scanned, and photographed, frozen before any runner read it): the hosted run in `sandbox/runs/2026-11-21-holdout-hosted.json`. Field accuracy 627/700, straight-through 62/100, invoice accuracy 60/100, 2.8 seconds per invoice. 18 invoices are silent errors: a date printed in a format the reader does not parse raised no exception. Local Docling read 256/700 on the same set (`2026-11-21-holdout-docling.json`).
- INV-CDIP (276 real one-page invoices from a public archive, mostly scans, labeled for invoice number, invoice date, due date, tax, and total): the hosted run of 2026-11-21. Field accuracy 761/896, invoice accuracy 178/276, 3 silent errors, 3.0 seconds per invoice. The set does not label supplier or subtotal, so its straight-through rate (15/276) is recorded and not compared. Local Docling read 136/896. The labels and these run files carry INV-CDIP's annotations, so they are not in this repository. `sandbox/get_inv_cdip.py` fetches the documents, rebuilds the labels, and checks them against the frozen set's hash, and `sandbox/score.py --labels inv-cdip.json` reruns the score.
- Second blind holdout (`sandbox/holdout-2/`, 100 one-page invoices from 33 suppliers in 13 layouts, built by an outside agent that never saw this project, frozen before any runner read it): the hosted run in `sandbox/runs/2026-11-22-holdout-2-hosted.json`. Field accuracy 659/700, straight-through 74/100, invoice accuracy 59/100, 3.0 seconds and about one cent per invoice. 34 invoices are silent errors, every one a supplier name that does not match the letterhead text exactly. Local Docling read 343/700 (`2026-11-22-holdout-2-docling.json`).
- Cross-border holdout (`sandbox/holdout-3/`, 120 one-page invoices from nine countries in USD, CAD, GBP, and EUR, built blind, frozen before any runner read it): the hosted run in `sandbox/runs/2026-11-22-holdout-3-hosted.json`. Field accuracy 815/840, invoice accuracy 101/120, 4 silent errors, 2.9 seconds per invoice. Straight-through is 42/120 because the 58 invoices in a currency other than the US dollar go to a person by design. Local Docling read 477/840 (`2026-11-22-holdout-3-docling.json`).

## Install

Python 3.10 or newer. The reader has two runners. Amazon Textract AnalyzeExpense is the default. A local Docling container is the backup, for reading without AWS. The sandbox tools that draw fixtures are separate, and they are not this install.

### Textract (default)

Install boto3 and sign in to an AWS profile that may call `textract:AnalyzeExpense`:

```
pip install boto3 "botocore[crt]" python-dateutil
aws login --profile <profile>
```

`botocore[crt]` is needed for profiles signed in with `aws login`. `python-dateutil` reads printed dates; it comes with boto3 already, and is named because the reader uses it directly, whichever runner reads. Each read is one call to Textract, billed per page (one cent a page at list price in us-east-1). The region is `AWS_REGION` when it is set, and us-east-1 otherwise.

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

The hosted reader is one AWS Lambda function behind a function URL that requires IAM credentials. It runs `read_invoice.py` with Textract, so it returns what the command returns, plus `pages` and `seconds`. There is no open endpoint, and nothing runs between calls. The function's role may call `textract:AnalyzeExpense`, write its own logs, and send traces to X-Ray, nothing else. Each read writes one log line with its timing, page count, exception reasons, and error, and no field values.

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

Reads are traced to AWS X-Ray through AWS's OpenTelemetry Lambda layer, which the stack adds. X-Ray's default sampling keeps the first read each second and 5 percent of the rest, which at this traffic is nearly every read. The function's log line for a read carries its trace id. To keep a trace, run `python sandbox/get_trace.py <trace id> --profile <profile> --name <what was read>`. `sandbox/traces/2026-11-22-hard-scan-08-westwind.json` is one: the function URL's invocation, the handler, Textract's `AnalyzeExpense` call (2.4 seconds), and the mapping to the seven fields (under a millisecond). The trace carries no field values.

An exception is still a 200: it is the answer. A body that is not a PDF is a 400. A Textract failure is a 502 with the reason. Textract reads a PDF of one page in a single call, so a longer PDF is refused with `UnsupportedDocumentException`. One page is a known limit, and the scored sets are one-page invoices.

## Sandbox

`sandbox/` holds the labeled sets, the scripts that wrote them, the scorer, and every run file. It is not a customer environment. How a run is recorded is `sandbox/README.md`.

## Release

No release is cut. The install above is how another computer runs the page and one PDF. A clean start on this machine brought Docling down and back up, read one PDF, and the page showed that read.

## License

Copyright 2026 Scott Mackey. [MIT License](LICENSE).

One score uses INV-CDIP (Mingfei Gao, Zeyuan Chen, Nikhil Naik, Kazuma Hashimoto, Caiming Xiong, Ran Xu, "Field Extraction from Forms with Unlabeled Data", ACL Spa-NLP Workshop 2022, [salesforce/inv-cdip](https://github.com/salesforce/inv-cdip)), whose annotations are licensed [CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/). None of it is in this repository: the documents, the annotations, and the labels and run files made from them stay local. `sandbox/get_inv_cdip.py` fetches them.
