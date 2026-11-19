# Sandbox

This is where invoice-ap runs for a test or an evaluation. It is not a customer environment.

Docling is the runner for the live read and the hard set. The fixture set, the exception rule, and the three numbers are in `../AGENTS.md`. A number that run did not produce is not a result.

`make_fixtures.py` writes `fixtures/*.pdf` and `labels.json`. It needs `reportlab`.

`make_hard.py` writes `hard/`. It does not rewrite that generated set. Scans and phone photos are those ten invoices rendered as images. Four more invoices use a second layout. Labels are `hard/labels.json`. Run it with `sandbox/.venv`, which has `reportlab`, `pymupdf`, and `pillow`.

`score.py --runner grok` or `score.py --runner claude` scores `labels.json`. `score.py --runner ollama` scores the same set with a local vision model. The default model is `qwen3-vl:8b`. That runner renders the page and sends the image to Ollama, so it needs `sandbox/.venv`. `score.py --runner docling` sends the file to Docling Serve at `127.0.0.1:5001` and reads the seven fields through `../read_invoice.py`. Add `--labels hard/labels.json` to score the harder set. Add `--gate` to exit 1 when any field is wrong or the exception does not match the label. The run file is still written. The files named in `../AGENTS.md` stay the claim. A run is written under `runs/`. An Ollama run on the generated set is named with the model. A Docling run on that set is named `YYYY-MM-DD-docling.json`. The call sees only that invoice.
