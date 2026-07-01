"""Shared helpers for Shanghai EIA public disclosure examples and the team app."""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Callable
from urllib.parse import quote, urljoin

from _paths import APP_ROOT, DATA_DIR, DB_PATH, OUTPUT_DIR

if TYPE_CHECKING:
    from playwright.async_api import Page as AsyncPage
    from playwright.sync_api import Page as SyncPage

BASE_HOST = "https://link.sthj.sh.gov.cn"
BASE_URL = f"{BASE_HOST}/shhj/fa/cms/shhj/"
PDF_GATEWAY = f"{BASE_URL}hpgs_pdf_login.jsp"
EXCHANGE_FILE_BASE = f"{BASE_HOST}/file/exchange_file/"

DISCLOSURE_TYPES = {
    "acceptance": {
        "label": "受理信息",
        "list_marker": "hpsl_list_login.jsp",
        "entry_url": f"{BASE_URL}hpgs_gz_login.jsp?applyItem=1&gongshiType=1&approvType=1",
    },
    "proposed_approval": {
        "label": "拟审批公示",
        "list_marker": "hpnsp_list_login.jsp",
        "entry_url": f"{BASE_URL}hpgs_gz_login.jsp?applyItem=1&gongshiType=2&approvType=1",
    },
    "approval_decision": {
        "label": "审批决定公告",
        "list_marker": "hpxm_list_login.jsp",
        "entry_url": f"{BASE_URL}hpgs_gz_login.jsp?applyItem=1&gongshiType=3&approvType=1",
    },
}

LIST_URLS = {key: value["entry_url"] for key, value in DISCLOSURE_TYPES.items()}
LIST_PAGE_MARKERS = tuple(value["list_marker"] for value in DISCLOSURE_TYPES.values())
DEFAULT_LIST_URL = LIST_URLS["approval_decision"]

METADATA_PATH = OUTPUT_DIR / "sh_eia_metadata.jsonl"

ATTACHMENT_SUFFIXES = (".pdf", ".doc", ".docx", ".xls", ".xlsx", ".zip")
OPENPDF_PATTERN = re.compile(r"openPdf\('([^']+)'\)", re.IGNORECASE)


def response_body_text(response) -> str:
    body = response.body
    if isinstance(body, bytes):
        return body.decode("utf-8", errors="ignore")
    return body


def write_response_body(path: Path, response) -> None:
    path.write_text(response_body_text(response), encoding="utf-8")


def is_list_page(url: str) -> bool:
    return any(marker in url for marker in LIST_PAGE_MARKERS)


def build_exchange_file_url(file_name: str) -> str:
    return f"{EXCHANGE_FILE_BASE}{quote(file_name)}"


def build_pdf_gateway_url(file_name: str) -> str:
    """Return the direct attachment URL used by the portal's PDF viewer."""
    return build_exchange_file_url(file_name)


def resolve_download_url(file_url: str, file_name: str = "") -> str:
    """Map legacy gateway URLs to the real file endpoint."""
    if "hpgs_pdf_login.jsp" not in file_url:
        return file_url

    name = file_name
    if not name:
        from urllib.parse import parse_qs, urlparse, unquote

        parsed = parse_qs(urlparse(file_url).query)
        name = unquote(parsed.get("fileName", [""])[0])
    if name:
        return build_exchange_file_url(name)
    return file_url


def normalize_response_body(body) -> bytes:
    if isinstance(body, bytes):
        return body
    return body.encode("utf-8", errors="ignore")


def is_html_response(body: bytes) -> bool:
    head = body[:512].lstrip().lower()
    return head.startswith(b"<!doctype") or head.startswith(b"<html")


def accept_notice(page: SyncPage) -> None:
    """Tick #iAgree and confirm on the notice page."""
    if is_list_page(page.url):
        return

    page.locator("#iAgree").check()
    page.locator("input[value='确认']").click()
    page.wait_for_url(re.compile(r".*_list_login\.jsp"), timeout=60_000)
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(1500)


async def async_accept_notice(page: AsyncPage) -> None:
    if is_list_page(page.url):
        return

    await page.locator("#iAgree").check()
    await page.locator("input[value='确认']").click()
    await page.wait_for_url(re.compile(r".*_list_login\.jsp"), timeout=60_000)
    await page.wait_for_load_state("networkidle")
    await page.wait_for_timeout(1500)


def paginate_action(page_no: int) -> Callable:
    def _paginate(page: SyncPage) -> None:
        accept_notice(page)
        if page_no <= 1:
            return
        page.locator("input[name='pageNo']").evaluate(f"el => el.value = '{page_no}'")
        page.evaluate("document.condForm.submit()")
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(1500)

    return _paginate


def async_paginate_action(page_no: int) -> Callable:
    async def _paginate(page: AsyncPage) -> None:
        await async_accept_notice(page)
        if page_no <= 1:
            return
        await page.locator("input[name='pageNo']").evaluate(f"el => el.value = '{page_no}'")
        await page.evaluate("document.condForm.submit()")
        await page.wait_for_load_state("networkidle")
        await page.wait_for_timeout(1500)

    return _paginate


def _cell_text(cell) -> str:
    return " ".join((cell.css("::text").getall() or [])).strip()


def _extract_openpdf_files(cell, file_type: str) -> list[dict[str, str]]:
    files: list[dict[str, str]] = []
    for onclick in cell.css("a::attr(onclick)").getall():
        match = OPENPDF_PATTERN.search(onclick or "")
        if not match:
            continue
        file_name = match.group(1)
        files.append(
            {
                "file_type": file_type,
                "file_name": file_name,
                "file_url": build_pdf_gateway_url(file_name),
            }
        )
    return files


def _extract_direct_files(cell, file_type: str, base_url: str) -> list[dict[str, str]]:
    files: list[dict[str, str]] = []
    href = (cell.css("a::attr(href)").get() or "").strip()
    if not href or href == "#":
        return files
    lower_href = href.lower()
    if not any(lower_href.endswith(suffix) for suffix in ATTACHMENT_SUFFIXES):
        return files
    file_name = href.rsplit("/", 1)[-1]
    files.append(
        {
            "file_type": file_type,
            "file_name": file_name,
            "file_url": urljoin(base_url, href),
        }
    )
    return files


def parse_acceptance_records(response) -> list[dict]:
    records: list[dict] = []
    for row in response.css("table.tab tr"):
        cells = row.css("td")
        if len(cells) < 4:
            continue
        project_name = _cell_text(cells[0]) or (cells[0].attrib.get("title") or "").strip()
        if not project_name:
            continue
        files = _extract_openpdf_files(cells[0], "notice") + _extract_openpdf_files(cells[3], "public_participation")
        records.append(
            {
                "disclosure_type": "acceptance",
                "project_name": project_name,
                "company": _cell_text(cells[1]) or (cells[1].attrib.get("title") or "").strip(),
                "pub_period": _cell_text(cells[2]) or (cells[2].attrib.get("title") or "").strip(),
                "source_url": response.url,
                "files": files,
            }
        )
    return records


def parse_proposed_records(response) -> list[dict]:
    records: list[dict] = []
    for row in response.css("table.tab tr"):
        cells = row.css("td")
        if len(cells) < 7:
            continue
        project_name = _cell_text(cells[0]) or (cells[0].attrib.get("title") or "").strip()
        if not project_name:
            continue
        files = _extract_openpdf_files(cells[6], "measures")
        records.append(
            {
                "disclosure_type": "proposed_approval",
                "project_name": project_name,
                "location": _cell_text(cells[1]) or (cells[1].attrib.get("title") or "").strip(),
                "company": _cell_text(cells[2]) or (cells[2].attrib.get("title") or "").strip(),
                "agency": _cell_text(cells[3]) or (cells[3].attrib.get("title") or "").strip(),
                "pub_period": _cell_text(cells[4]) or (cells[4].attrib.get("title") or "").strip(),
                "summary": _cell_text(cells[5]) or (cells[5].attrib.get("title") or "").strip(),
                "source_url": response.url,
                "files": files,
            }
        )
    return records


def parse_approval_decision_records(response) -> list[dict]:
    records: list[dict] = []
    for row in response.css("table.tab tr"):
        cells = row.css("td")
        if len(cells) < 5:
            continue
        project_name = (cells[0].css("a::text").get() or _cell_text(cells[0]) or "").strip()
        if not project_name:
            continue
        files = _extract_direct_files(cells[0], "report", response.url) + _extract_direct_files(
            cells[4], "approval", response.url
        )
        records.append(
            {
                "disclosure_type": "approval_decision",
                "project_name": project_name,
                "approval_title": _cell_text(cells[1]) or (cells[1].attrib.get("title") or "").strip(),
                "approval_number": _cell_text(cells[2]) or (cells[2].attrib.get("title") or "").strip(),
                "approval_date": _cell_text(cells[3]) or (cells[3].attrib.get("title") or "").strip(),
                "source_url": response.url,
                "files": files,
            }
        )
    return records


PARSERS = {
    "acceptance": parse_acceptance_records,
    "proposed_approval": parse_proposed_records,
    "approval_decision": parse_approval_decision_records,
}


def parse_list_records(response, disclosure_type: str) -> list[dict]:
    parser = PARSERS.get(disclosure_type)
    if parser is None:
        raise ValueError(f"Unknown disclosure type: {disclosure_type}")
    return parser(response)


def extract_total_pages(response) -> int | None:
    text = response_body_text(response)
    match = re.search(r"共\s*<span[^>]*>\s*(\d+)\s*</span>\s*页", text)
    if match:
        return int(match.group(1))
    return None


def extract_list_records(response) -> list[dict[str, str]]:
    """Backward-compatible helper for approval-decision explore script."""
    return [
        {
            "project_name": item["project_name"],
            "approval_title": item.get("approval_title", ""),
            "approval_number": item.get("approval_number", ""),
            "approval_date": item.get("approval_date", ""),
            "report_url": next((f["file_url"] for f in item["files"] if f["file_type"] == "report"), ""),
            "approval_doc_url": next((f["file_url"] for f in item["files"] if f["file_type"] == "approval"), ""),
            "source_url": item.get("source_url", ""),
        }
        for item in parse_approval_decision_records(response)
    ]


def records_to_download_items(records: list[dict]) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for record in records:
        project_name = record.get("project_name", "")
        for file_info in record.get("files", []):
            suffix = Path(file_info["file_name"]).suffix or ".pdf"
            items.append(
                {
                    "title": project_name,
                    "file_name": f"{project_name}_{file_info['file_type']}{suffix}",
                    "file_url": file_info["file_url"],
                    "approval_number": record.get("approval_number", ""),
                    "source_url": record.get("source_url", ""),
                    "file_type": file_info["file_type"],
                    "disclosure_type": record.get("disclosure_type", ""),
                }
            )
    return items
