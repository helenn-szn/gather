"""Clean up OPENROUTER_GATEWAY / PINECONE_GATEWAY in .env and drop junk lines.

    python3 -m scripts.fix_gateways

Keeps every other setting (dashboard key, gate secret...) as it is.
For Pinecone it tries each Pinecone connector and keeps the one that can
see the index in PINECONE_INDEX.
"""
import re
from pathlib import Path

import requests

from gather import gateway

ROOT = Path(__file__).resolve().parent.parent
ENV = ROOT / ".env"
URL = re.compile(r"https://api\.sprites\.dev/v1/gateway/[A-Za-z0-9_\-]+/[A-Za-z0-9_\-]+")


def _strings(obj, out):
    if isinstance(obj, dict):
        for v in obj.values():
            _strings(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _strings(v, out)
    elif isinstance(obj, str):
        out.extend(URL.findall(obj))


def main() -> None:
    lines = [l.strip() for l in ENV.read_text(errors="replace").splitlines()]
    values = {}
    for l in lines:
        m = re.match(r"^([A-Z][A-Z0-9_]*)=(.*)$", l)
        if m and m.group(1) not in values:
            values[m.group(1)] = m.group(2).strip()

    found = []
    _strings(gateway.list_connectors(), found)
    found = list(dict.fromkeys(found))

    openrouter = [u for u in found if "/gateway/openrouter/" in u]
    if openrouter:
        values["OPENROUTER_GATEWAY"] = openrouter[0]

    index = values.get("PINECONE_INDEX", "gather-guests")
    chosen = ""
    for u in [u for u in found if "/gateway/pinecone/" in u]:
        try:
            r = requests.get(u + "/indexes", timeout=20,
                             headers={"X-Pinecone-API-Version": "2025-04"})
            ok = r.ok and index in r.text
            print(f"  {u} -> {r.status_code}{' (has ' + index + ')' if ok else ''}")
            if ok and not chosen:
                chosen = u
        except Exception as exc:
            print(f"  {u} -> error {exc}")
    values["PINECONE_GATEWAY"] = chosen

    ENV.write_text("".join(f"{k}={v}\n" for k, v in values.items()))
    print("\nOPENROUTER_GATEWAY=" + values.get("OPENROUTER_GATEWAY", ""))
    print("PINECONE_GATEWAY=" + (chosen or "(none found - memory will be off; tell Claude)"))
    print(f"{len(values)} settings saved, junk lines removed.")


if __name__ == "__main__":
    main()
