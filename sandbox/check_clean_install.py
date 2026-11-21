"""Can someone call the hosted reader with only what the README says to install?

Makes a throwaway venv in a temp folder, installs host/requirements.txt and
nothing else, copies host/call.py there alone (no checkout beside it), sends
one invoice, and deletes the venv. Exit 0 means the caller install is
complete as written.

  python sandbox/check_clean_install.py <reader url> --profile invoice-ap-caller

--requirements points at a different list, to show what fails without a
package.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import venv
from pathlib import Path

CHECKOUT = Path(__file__).resolve().parent.parent
PDF = CHECKOUT / "sandbox" / "fixtures" / "01-northline.pdf"


def venv_python(root: Path) -> Path:
    windows = root / "Scripts" / "python.exe"
    return windows if windows.exists() else root / "bin" / "python"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("url")
    parser.add_argument("--profile", default="invoice-ap-caller")
    parser.add_argument("--requirements", type=Path, default=CHECKOUT / "host" / "requirements.txt")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="iap-clean-") as tmp:
        work = Path(tmp)
        env_dir = work / "venv"
        venv.EnvBuilder(with_pip=True).create(env_dir)
        python = venv_python(env_dir)
        subprocess.run(
            [str(python), "-m", "pip", "install", "--quiet", "--disable-pip-version-check", "-r", str(args.requirements)],
            check=True,
        )
        installed = subprocess.run(
            [str(python), "-m", "pip", "freeze", "--disable-pip-version-check"],
            check=True, capture_output=True, text=True,
        ).stdout.split()
        shutil.copy2(CHECKOUT / "host" / "call.py", work / "call.py")
        shutil.copy2(PDF, work / PDF.name)
        done = subprocess.run(
            [str(python), "call.py", args.url, PDF.name, "--profile", args.profile],
            cwd=work, capture_output=True, text=True,
        )
    print(f"python {sys.version.split()[0]}, installed: {', '.join(installed)}")
    if done.returncode != 0:
        print("FAIL")
        print((done.stdout + done.stderr).strip()[-1500:])
        raise SystemExit(1)
    body = json.loads(done.stdout)
    print(f"PASS status {body['status']} invoice {body['invoice_number']} exception {body['exception']}")


if __name__ == "__main__":
    main()
