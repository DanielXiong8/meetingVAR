"""Offline license key tool for release engineering (private key stays outside repo)."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from meetingvar.commercial.license import (issue_license, machine_id,  # noqa: E402
                                            verify_license)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    keygen = sub.add_parser("keygen")
    keygen.add_argument("--out", required=True, help="private key PEM path (never commit)")

    issue = sub.add_parser("issue")
    issue.add_argument("--key", required=True)
    issue.add_argument("--to", required=True)
    issue.add_argument("--edition", default="professional")
    issue.add_argument("--seats", type=int, default=1)
    issue.add_argument("--expires", default="")
    issue.add_argument("--machine", default="", help="optional machine fingerprint")
    issue.add_argument("--feature", action="append", default=[])

    check = sub.add_parser("verify")
    check.add_argument("token")
    check.add_argument("--machine", default="")

    machine = sub.add_parser("machine-id")
    args = parser.parse_args()

    if args.command == "keygen":
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        key = Ed25519PrivateKey.generate()
        pem = key.private_bytes(serialization.Encoding.PEM,
                                serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption())
        Path(args.out).write_bytes(pem)
        print("private key written; never commit it")
        print("public key:", key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex())
        return 0
    if args.command == "issue":
        token = issue_license(Path(args.key).read_bytes(), issued_to=args.to,
                              edition=args.edition, seats=args.seats,
                              expires=args.expires, machine=args.machine,
                              features=args.feature)
        print(token)
        return 0
    if args.command == "verify":
        import json

        print(json.dumps(verify_license(args.token, args.machine or None),
                         ensure_ascii=False, indent=2))
        return 0
    print(machine_id())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())