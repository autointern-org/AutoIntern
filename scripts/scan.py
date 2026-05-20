from __future__ import annotations

import argparse

from core.pipeline import run_scan


def main() -> None:
    parser = argparse.ArgumentParser(description="Scan whitelisted internship boards.")
    parser.add_argument("--config", default="config/whitelist.yaml", help="Path to whitelist YAML")
    parser.add_argument("--dry-run", action="store_true", help="Print intended Discord posts instead of posting")
    parser.add_argument("--skip-claude", action="store_true", help="Use deterministic placeholder resume configs")
    args = parser.parse_args()

    result = run_scan(
        whitelist_path=args.config,
        dry_run=args.dry_run,
        skip_claude=args.skip_claude,
    )
    print(
        "scan complete: "
        f"fetched={result.fetched} matched={result.matched} notified={result.notified} "
        f"dismissed={result.dismissed} skipped_seen={result.skipped_seen}"
    )


if __name__ == "__main__":
    main()
