"""
Example 2: Spider - crawl Shanghai EIA approval-decision disclosures.

The list page already contains direct links to:
  - the EIA report PDF (项目名称 column)
  - the approval document PDF (批文内容 column)

Pagination uses a form submit with pageNo.

Best for: batch collection of report/approval file URLs.

Outputs:
  - output/sh_eia_metadata.jsonl
"""

from scrapling.fetchers import AsyncDynamicSession
from scrapling.spiders import Request, Response, Spider

from _common import (
    DEFAULT_LIST_URL,
    METADATA_PATH,
    OUTPUT_DIR,
    async_paginate_action,
    extract_list_records,
    is_list_page,
    records_to_download_items,
)


class ShanghaiEIASpider(Spider):
    name = "sh_eia"
    start_urls = [DEFAULT_LIST_URL]
    allowed_domains = {"link.sthj.sh.gov.cn", "sthj.sh.gov.cn"}
    concurrent_requests = 1
    download_delay = 2
    blocked_codes = {401, 403, 407, 429, 444, 500, 502, 503, 504}

    # Demo default: only crawl the first list page. Increase as needed.
    max_pages = 1

    def configure_sessions(self, manager):
        manager.add(
            "browser",
            AsyncDynamicSession(
                headless=True,
                google_search=False,
                network_idle=True,
                timeout=60_000,
            ),
            default=True,
        )

    async def start_requests(self):
        for page_no in range(1, self.max_pages + 1):
            yield Request(
                DEFAULT_LIST_URL,
                sid="browser",
                callback=self.parse,
                page_action=async_paginate_action(page_no),
                wait_selector="table.tab",
                wait_selector_state="visible",
                wait=2000,
                meta={"page_no": page_no},
                dont_filter=True,
            )

    async def parse(self, response: Response):
        if not is_list_page(response.url):
            self.logger.warning("Expected list page, got %s", response.url)
            return

        records = extract_list_records(response)
        self.logger.info("Page %s: found %s projects", response.meta.get("page_no"), len(records))

        for item in records_to_download_items(records):
            yield item


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    result = ShanghaiEIASpider(crawldir=OUTPUT_DIR / "crawl_checkpoint").start()
    result.items.to_jsonl(METADATA_PATH)

    print(f"\n{'=' * 50}")
    print(f"Items   : {len(result.items)}")
    print(f"Requests: {result.stats.requests_count}")
    print(f"Time    : {result.stats.elapsed_seconds:.2f}s")
    print(f"Saved   : {METADATA_PATH}")
    print(f"{'=' * 50}\n")


if __name__ == "__main__":
    main()
