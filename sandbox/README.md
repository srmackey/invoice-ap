# Sandbox

This is where invoice-ap runs for a test or an evaluation. It is not a customer environment.

Textract AnalyzeExpense is the default runner for the live read. Docling is the local backup. Both live in `../read_invoice.py`, which the command, the page, and this scorer share. The fixture set, the exception rule, and the three numbers are in `../AGENTS.md`. A number that run did not produce is not a result.

`make_fixtures.py` writes `fixtures/*.pdf` and `labels.json`. It needs `reportlab`.

`make_hard.py` writes `hard/`. It does not rewrite that generated set. Scans and phone photos are those ten invoices rendered as images. Four more invoices use a second layout. Labels are `hard/labels.json`. Run it with `sandbox/.venv`, which has `reportlab`, `pymupdf`, and `pillow`.

`make_holdout.py` wrote `holdout/` once, on 2026-11-21, and refuses to run again. It is the generated holdout: 100 one-page invoices in twelve layouts the other two scripts do not produce, half clean PDFs and a quarter each scans and phone photos, with 25 that need an exception. A due date implied by printed terms but not printed is labeled missing. The script was written without looking at any runner's output. The set is frozen: it is scored once per runner and never used to change a mapper, and a miss on it is reported, not fixed against it. `test_holdout.py` checks every file against the sha256 in `holdout/labels.json`.

`score.py --runner grok` or `score.py --runner claude` scores `labels.json`. `score.py --runner ollama` scores the same set with a local vision model. The default model is `qwen3-vl:8b`. That runner renders the page and sends the image to Ollama, so it needs `sandbox/.venv`. `score.py --runner docling` sends the file to Docling Serve at `127.0.0.1:5001` and reads the seven fields through `../read_invoice.py`. `score.py --runner textract --profile <aws profile>` sends each invoice to Amazon Textract AnalyzeExpense and maps its summary fields to the seven. It needs `sandbox/.venv`, which has `boto3`, and a signed-in AWS profile. The run file records pages and list-price cost per invoice, and a textract run is never overwritten. Add `--labels hard/labels.json` to score the harder set. Add `--gate` to exit 1 when any field is wrong or the exception does not match the label. The run file is still written. The files named in `../AGENTS.md` stay the claim. A run is written under `runs/`. An Ollama run on the generated set is named with the model. A Docling run on that set is named `YYYY-MM-DD-docling.json`. The call sees only that invoice.
