# invoice-ap

A rapport-band demo on the SMB line. The offer, the fields, the exception rule, and the benchmark are in `AGENTS.md`.

The scores for the labeled sets are in `AGENTS.md`.

## Install

Docker, and Python 3.10 or newer. No other packages. Reading an invoice and serving the page use the Python standard library. The sandbox tools that draw fixtures are separate, and they are not this install.

From this folder, start Docling:

```
docker compose -f services/docling/compose.yaml up -d
```

The first start pulls the image. Docling listens at `127.0.0.1:5001`. The command and the page need it answering there.

One PDF through the command:

```
python read_invoice.py sandbox/fixtures/01-northline.pdf
```

The command prints the seven fields and the exception, and appends one record to `reads/reads.jsonl`. `sandbox/score.py` uses that same reader.

The page:

```
python page.py
```

Open `http://127.0.0.1:8765`. The page lists those records, opens one, and shows the share that raised no exception and the time per invoice. It accepts a PDF and reads it with the same reader as the command.

## Sandbox

`sandbox/` holds the labeled fixture set and whatever runner the work needs. No runner is chosen. It is not a customer environment.

## Release

No release is cut. The install above is how another computer runs the page and one PDF. A clean start, Docling down and back up, is still open.
