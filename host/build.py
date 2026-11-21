"""Assemble the hosted reader's Lambda package in build/reader.

The handler needs read_invoice.py beside it. infra/reader.yaml points Code
at build/reader. Run from the checkout before packaging the stack:

    python host/build.py
"""

from __future__ import annotations

import shutil
from pathlib import Path

CHECKOUT = Path(__file__).resolve().parents[1]
OUT = CHECKOUT / "build" / "reader"
FILES = (
    CHECKOUT / "host" / "handler.py",
    CHECKOUT / "read_invoice.py",
)


def main() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    for source in FILES:
        shutil.copy2(source, OUT / source.name)
    print(f"built {OUT.relative_to(CHECKOUT)}: {', '.join(path.name for path in FILES)}")


if __name__ == "__main__":
    main()
