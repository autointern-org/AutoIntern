from __future__ import annotations

import os

from core.kv import CloudflareKV, wipe_scan_state


def main() -> None:
    kv = CloudflareKV(
        account_id=os.getenv("CF_ACCOUNT_ID"),
        namespace_id=os.getenv("CF_KV_NAMESPACE_ID"),
        api_token=os.getenv("CF_API_TOKEN"),
    )
    if not kv.enabled:
        raise RuntimeError("CF_ACCOUNT_ID, CF_KV_NAMESPACE_ID, and CF_API_TOKEN are required")
    deleted = wipe_scan_state(kv)
    print(f"reset complete: deleted={deleted} keys")


if __name__ == "__main__":
    main()
