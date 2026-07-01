"""
Example 3: Download attachments collected by 02_crawl_spider.py.

Reads output/sh_eia_metadata.jsonl and saves PDF/DOC files locally.

Best for: turning metadata into local copies of reports and approval documents.

Outputs:
  - output/downloads/<filename>
"""

import json
import re

from scrapling.fetchers import FetcherSession

from _common import (
    METADATA_PATH,
    OUTPUT_DIR,
    is_html_response,
    normalize_response_body,
    resolve_download_url,
)

DOWNLOAD_DIR = OUTPUT_DIR / "downloads"


def safe_filename(name: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|]', "_", name).strip()
    return cleaned or "unnamed_attachment"


def main() -> None:
    if not METADATA_PATH.exists():
        raise FileNotFoundError(
            f"Metadata file not found: {METADATA_PATH}\nRun 02_crawl_spider.py first."
        )

    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

    with METADATA_PATH.open(encoding="utf-8") as handle:
        items = [json.loads(line) for line in handle if line.strip()]

    if not items:
        print("No attachment records found in metadata file.")
        return

    downloaded = 0
    skipped = 0
    failed = 0

    with FetcherSession(impersonate="chrome") as session:
        for index, item in enumerate(items, 1):
            url = resolve_download_url(item["file_url"], item.get("file_name", ""))
            filename = safe_filename(item.get("file_name") or f"attachment_{index}")
            target = DOWNLOAD_DIR / filename

            if target.exists():
                skipped += 1
                continue

            response = session.get(url, stealthy_headers=True)
            body = normalize_response_body(response.body)
            if is_html_response(body):
                failed += 1
                print(f"[{index}/{len(items)}] SKIP (not a file): {filename}")
                continue

            target.write_bytes(body)
            downloaded += 1
            print(f"[{index}/{len(items)}] {filename}")

    print(f"\nDownloaded: {downloaded}")
    print(f"Skipped   : {skipped}")
    print(f"Failed    : {failed}")
    print(f"Directory : {DOWNLOAD_DIR}")


if __name__ == "__main__":
    main()
