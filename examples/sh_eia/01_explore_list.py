"""
Example 1: Explore the Shanghai EIA approval-decision list page.

The portal first shows a notice page. Tick the #iAgree checkbox and click
"确认" to reach the project list.

Best for: verifying selectors before running the full spider.

Outputs:
  - output/list_after_accept.html
"""

from scrapling.fetchers import DynamicFetcher

from _common import (
    DEFAULT_LIST_URL,
    OUTPUT_DIR,
    accept_notice,
    extract_list_records,
    write_response_body,
)


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    page = DynamicFetcher.fetch(
        DEFAULT_LIST_URL,
        headless=False,
        google_search=False,
        network_idle=True,
        timeout=60_000,
        page_action=accept_notice,
        wait_selector="table.tab",
        wait_selector_state="visible",
        wait=3000,
    )

    html_path = OUTPUT_DIR / "list_after_accept.html"
    write_response_body(html_path, page)

    records = extract_list_records(page)
    print(f"Saved HTML to: {html_path}")
    print(f"Final URL : {page.url}")
    print(f"Status    : {page.status}")
    print(f"Projects  : {len(records)}\n")

    for index, item in enumerate(records[:10], 1):
        print(f"{index:>3}. {item['project_name']}")
        print(f"      批文: {item['approval_number']}  日期: {item['approval_date']}")
        if item["report_url"]:
            print(f"      报告: {item['report_url']}")
        if item["approval_doc_url"]:
            print(f"      批文: {item['approval_doc_url']}")

    if len(records) > 10:
        print(f"\n... and {len(records) - 10} more")


if __name__ == "__main__":
    main()
