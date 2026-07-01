"""Explore e2.sthj.sh.gov.cn post-construction list page."""

from __future__ import annotations

from sources.e2_qygk.client import fetch_list_page
from sources.e2_qygk.parsers import extract_total_pages, extract_total_records, parse_list_records


def main() -> None:
    html = fetch_list_page(1)
    records = parse_list_records(html)
    print(f"HTML length: {len(html)}")
    print(f"Total pages: {extract_total_pages(html)}")
    print(f"Total records: {extract_total_records(html)}")
    print(f"Parsed items: {len(records)}\n")
    for index, item in enumerate(records[:5], 1):
        print(f"{index}. {item['project_name']}")
        print(f"   单位: {item.get('company', '')}  阶段: {item.get('lifecycle_stage', '')}")
        print(f"   详情: {item.get('source_url', '')}")


if __name__ == "__main__":
    main()
