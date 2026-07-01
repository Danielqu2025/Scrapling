"""HTTP helpers for e2.sthj.sh.gov.cn."""

from __future__ import annotations

from scrapling.fetchers import FetcherSession

LIST_URL = "https://e2.sthj.sh.gov.cn/qygkweb/jsp/view/hjxxgk/jsxmzhq_list.jsp"
DETAIL_URL_TEMPLATE = "https://e2.sthj.sh.gov.cn/qygkweb/jsp/view/jsxmInfo_edit.jsp?id={external_id}"
FILE_BASE = "https://e2.sthj.sh.gov.cn/qygkweb/jsp/view/file/filedown.do"


def response_text(response) -> str:
    body = response.body
    if isinstance(body, bytes):
        return body.decode("utf-8", errors="ignore")
    return body or ""


def fetch_list_page(page_no: int = 1, year: str = "") -> str:
    headers = {"Referer": LIST_URL, "Content-Type": "application/x-www-form-urlencoded"}
    data = {
        "currentPage": str(page_no),
        "nd": year,
        "status": "",
        "type": "",
        "zhqTab": "0",
        "districts": "",
        "districtAll": "",
    }
    with FetcherSession(impersonate="chrome") as session:
        session.get(LIST_URL, stealthy_headers=True)
        if page_no <= 1:
            response = session.get(LIST_URL, stealthy_headers=True)
        else:
            response = session.post(LIST_URL, data=data, stealthy_headers=True, headers=headers)
        return response_text(response)


def fetch_detail_page(external_id: str) -> str:
    url = DETAIL_URL_TEMPLATE.format(external_id=external_id)
    with FetcherSession(impersonate="chrome") as session:
        response = session.get(url, stealthy_headers=True)
        return response_text(response)
