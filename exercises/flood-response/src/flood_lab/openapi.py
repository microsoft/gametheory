from __future__ import annotations

import argparse
import json
from pathlib import Path

from flood_lab.api import create_app
from flood_lab.config import Settings

ROOT = Path(__file__).resolve().parents[2]


def artifact() -> bytes:
    schema = create_app(Settings()).openapi()
    schema["info"]["x-contract-version"] = "flood-lab/v1"
    return (json.dumps(schema, indent=2, ensure_ascii=False, sort_keys=True) + "\n").encode()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export the independent flood-lab/v1 OpenAPI")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    path = ROOT / "openapi.json"
    expected = artifact()
    if args.check:
        if not path.exists() or path.read_bytes() != expected:
            print("Independent lab OpenAPI drift.")
            return 1
        print("Independent flood-lab/v1 OpenAPI is current.")
    else:
        path.write_bytes(expected)
        print("Generated independent flood-lab/v1 OpenAPI.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
