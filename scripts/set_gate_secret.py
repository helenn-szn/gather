"""On the GATE Sprite: take the secret from a file and clean up .env.

    python3 -m scripts.set_gate_secret ~/gate_secret.txt

Works even if the file came through Windows PowerShell (UTF-16, CRLF).
Drops broken lines from .env and writes exactly one GATE_SECRET line.
"""
import hashlib
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV = ROOT / ".env"
KEEP = ("AGENTMAIL_GATEWAY", "TELEGRAM_GATEWAY", "AGENTMAIL_INBOX", "DASHBOARD_PORT")


def read_any(path: Path) -> str:
    raw = path.read_bytes()
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16")
    if b"\x00" in raw:
        return raw.decode("utf-16-le", errors="ignore")
    return raw.decode("utf-8-sig", errors="ignore")


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        return
    found = re.findall(r"\b[0-9a-f]{48}\b", read_any(Path(sys.argv[1]).expanduser()))
    if not found:
        print("No secret found in that file.")
        return
    secret = found[-1]
    lines = []
    if ENV.exists():
        for line in ENV.read_text(errors="replace").splitlines():
            m = re.match(r"^([A-Z_]+)=", line.strip())
            if m and m.group(1) in KEEP:
                lines.append(line.strip())
    if not any(l.startswith("DASHBOARD_PORT=") for l in lines):
        lines.append("DASHBOARD_PORT=8080")
    lines.append(f"GATE_SECRET={secret}")
    ENV.write_text("\n".join(lines) + "\n")
    print("Gate .env now has:")
    for l in lines:
        print("  " + (l.split("=")[0] + "=(set)" if l.startswith("GATE_SECRET") else l))
    print("Gate secret fingerprint:", hashlib.sha256(secret.encode()).hexdigest()[:8])


if __name__ == "__main__":
    main()
