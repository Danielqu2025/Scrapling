"""Parse e2.sthj.sh.gov.cn post-construction list and detail pages."""

from __future__ import annotations

import re
from html import unescape
from typing import Any

from sources.e2_qygk.client import DETAIL_URL_TEMPLATE
from sources.types import SOURCE_E2_QYGK

OPEN_INFO_PATTERN = re.compile(
    r"openInfo\s*\(\s*['\"]([^'\"]+)['\"]\s*,\s*['\"](\d+)['\"]\s*\)",
    re.IGNORECASE,
)
UUID_LIKE = re.compile(r"^[A-F0-9]{32}$", re.IGNORECASE)
TOTAL_PAGES_PATTERN = re.compile(r"共\s*(\d+)\s*页")
TOTAL_RECORDS_PATTERN = re.compile(r"(\d+)\s*条记录")
APPROVAL_NUMBER_PATTERN = re.compile(r"环评批文文号[^<]*</[^>]+>\s*<[^>]+>\s*([^<]+)", re.IGNORECASE)
APPROVAL_DATE_PATTERN = re.compile(r"环评批文日期[^<]*</[^>]+>\s*<[^>]+>\s*([^<]+)", re.IGNORECASE)
ST_EIA_ID_PATTERN = re.compile(r"stEiaId\s*[=:]\s*['\"]([^'\"]+)['\"]", re.IGNORECASE)
LEFTBOX_PATTERN = re.compile(r'<div class="leftBox[^"]*"[^>]*>', re.IGNORECASE)
FILEDOWN_PATTERN = re.compile(
    r'(?<!function\s)filedown\s*\(\s*[\'"]([^\'"]+)[\'"]\s*\)',
    re.IGNORECASE,
)
FILEDOWN2_PATTERN = re.compile(
    r'filedown2\s*\(\s*[\'"]([^\'"]+)[\'"]\s*\)',
    re.IGNORECASE,
)
FILE_ROW_BLOCK_PATTERN = re.compile(
    r'<div class="col-xs-12 col-sm-12 col-md-12 col-lg-12">\s*'
    r'<div class="name[^"]*"[^>]*>([^<]+)</div>\s*'
    r'<div class="value[^"]*"[^>]*>.*?'
    r'(?:filedown\s*\(\s*[\'"]([^\'"]+)[\'"]\s*\)|filedown2\s*\(\s*[\'"]([^\'"]+)[\'"]\s*\))',
    re.IGNORECASE | re.DOTALL,
)

STAGE_NAME_TO_KEY = {
    "报批前公示": "pre",
    "项目基本信息": "basic",
    "建设期": "construction",
    "竣工及调试期": "debug",
    "竣工环保验收": "acceptance",
}

FILEDOWN2_TYPE_MAP = {
    "BL_XM_TABLE": ("pre_approval_notice", "拟报批的环境影响报告表全文.pdf"),
    "BL_GSWTS": ("pre_approval_entrust", "拟报批的环境影响报告表公众参与情况说明.pdf"),
}


def _clean(text: str) -> str:
    text = unescape(re.sub(r"<[^>]+>", " ", text or ""))
    return re.sub(r"\s+", " ", text).strip()


def _strip_html_comments(html: str) -> str:
    return re.sub(r"<!--.*?-->", "", html, flags=re.DOTALL)


def extract_total_pages(html: str) -> int | None:
    match = TOTAL_PAGES_PATTERN.search(html)
    return int(match.group(1)) if match else None


def extract_total_records(html: str) -> int | None:
    match = TOTAL_RECORDS_PATTERN.search(html)
    return int(match.group(1)) if match else None


def _extract_field(block: str, label: str) -> str:
    pattern = re.compile(
        rf"{re.escape(label)}[^<]*</[^>]+>\s*(?:<[^>]+>\s*)*([^<]+)",
        re.IGNORECASE,
    )
    match = pattern.search(block)
    return _clean(match.group(1)) if match else ""


def parse_list_records(html: str, source_url: str = "") -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for match in OPEN_INFO_PATTERN.finditer(html):
        external_id = match.group(1)
        start = max(0, match.start() - 1200)
        end = min(len(html), match.end() + 1200)
        block = html[start:end]

        project_name = _extract_field(block, "项目名称") or _first_class_text(block, "name")
        if not project_name or project_name == external_id or UUID_LIKE.match(project_name):
            project_name = _guess_project_name(block, external_id)
        location = _extract_field(block, "建设地点") or _first_class_text(block, "js")
        company = _extract_field(block, "建设单位") or _extract_js_pair(block, 1)
        district = _extract_field(block, "所属区域") or _first_class_text(block, "i-qy")
        lifecycle_stage = _first_class_text(block, "i-type")
        pub_date = _first_class_text(block, "id-item")

        records.append(
            {
                "source": SOURCE_E2_QYGK,
                "disclosure_type": "post_construction",
                "external_id": external_id,
                "project_name": project_name or external_id,
                "company": company,
                "location": location,
                "district": district,
                "lifecycle_stage": lifecycle_stage,
                "event_date": pub_date,
                "title": project_name or external_id,
                "source_url": DETAIL_URL_TEMPLATE.format(external_id=external_id),
                "summary_json": {
                    "nm_type": match.group(2),
                    "list_source_url": source_url,
                },
                "files": [],
            }
        )
    return records


def _guess_project_name(block: str, external_id: str) -> str:
    for candidate in re.findall(r">([^<>]{10,160})<", block):
        text = _clean(candidate)
        if not text or text == external_id or UUID_LIKE.match(text):
            continue
        if any(keyword in text for keyword in ("首页", "查询", "下一页", "上一页", "跳转")):
            continue
        return text
    return external_id


def _first_class_text(block: str, class_name: str) -> str:
    pattern = re.compile(rf'class="[^"]*\b{re.escape(class_name)}\b[^"]*"[^>]*>(.*?)</', re.IGNORECASE | re.DOTALL)
    match = pattern.search(block)
    return _clean(match.group(1)) if match else ""


def _extract_js_pair(block: str, index: int) -> str:
    matches = re.findall(r'class="[^"]*\bjs\b[^"]*"[^>]*>(.*?)</', block, re.IGNORECASE | re.DOTALL)
    if len(matches) > index:
        return _clean(matches[index])
    return ""


def _split_stage_blocks(html: str) -> list[tuple[str, str]]:
    """Split detail HTML into (stage_key, block_html) pairs following leftBox tabs."""
    html = _strip_html_comments(html)
    markers = list(LEFTBOX_PATTERN.finditer(html))
    blocks: list[tuple[str, str]] = []
    for index, marker in enumerate(markers):
        end = markers[index + 1].start() if index + 1 < len(markers) else len(html)
        block = html[marker.start() : end]
        stage_match = re.search(
            r'<div class="leftBox[^"]*"[^>]*>\s*([^<\n]+)',
            block,
            re.IGNORECASE,
        )
        stage_name = _clean(stage_match.group(1)) if stage_match else ""
        stage_key = STAGE_NAME_TO_KEY.get(stage_name, "")
        if stage_key:
            blocks.append((stage_key, block))
    return blocks


def _infer_file_type(stage_key: str, row_label: str, file_name: str) -> str:
    combined = f"{row_label} {file_name}"
    if "拟报批" in combined and "公众参与" in combined:
        return "pre_approval_entrust"
    if "拟报批" in combined:
        return "pre_approval_notice"
    if "施工期环保措施" in combined:
        return "construction_measures"
    if "施工期环境监" in combined or "施工期监测" in combined:
        return "construction_monitoring"
    if "非重大调整" in combined:
        return "adjustment_report"
    if stage_key == "debug" and ("环保措施" in combined or "调试" in file_name):
        return "debug_measures"
    if "验收监测" in combined or "验收调查" in combined:
        return "acceptance_report"
    if "验收意见" in combined:
        return "acceptance_opinion"
    if "其他需要说明" in combined:
        return "acceptance_other"
    if stage_key == "construction":
        return "construction_measures"
    if stage_key == "acceptance":
        return "acceptance_other"
    if stage_key == "pre":
        return "pre_approval_notice"
    return f"e2_{stage_key}" if stage_key else "attachment"


def _file_entry(
    *,
    file_type: str,
    file_name: str,
    file_external_id: str,
    download_scheme: str = "filedown",
) -> dict[str, str]:
    return {
        "file_type": file_type,
        "file_name": file_name,
        "file_url": f"{download_scheme}://{file_external_id}",
        "file_external_id": file_external_id,
        "download_status": "captcha_required",
    }


def _extract_files_from_block(stage_key: str, block: str) -> list[dict[str, str]]:
    files: list[dict[str, str]] = []
    seen: set[str] = set()

    for match in FILE_ROW_BLOCK_PATTERN.finditer(block):
        row_label = _clean(match.group(1))
        file_id = match.group(2) or match.group(3)
        if not file_id or file_id in seen:
            continue
        seen.add(file_id)

        context = block[match.start() : match.end()]
        file_name = ""
        name_match = re.search(
            r'<td[^>]*width="60%"[^>]*>([^<]+\.pdf[^<]*)</td>',
            context,
            re.IGNORECASE,
        )
        if name_match:
            file_name = _clean(name_match.group(1))
        elif file_id in FILEDOWN2_TYPE_MAP:
            file_name = FILEDOWN2_TYPE_MAP[file_id][1]
        else:
            file_name = f"{row_label or file_id}.pdf"

        scheme = "filedown2" if match.group(3) else "filedown"
        if file_id in FILEDOWN2_TYPE_MAP:
            file_type = FILEDOWN2_TYPE_MAP[file_id][0]
        else:
            file_type = _infer_file_type(stage_key, row_label, file_name)

        files.append(
            _file_entry(
                file_type=file_type,
                file_name=file_name,
                file_external_id=file_id,
                download_scheme=scheme,
            )
        )

    return files


def _extract_files(html: str) -> list[dict[str, str]]:
    html = _strip_html_comments(html)
    files: list[dict[str, str]] = []
    seen_urls: set[str] = set()
    for stage_key, block in _split_stage_blocks(html):
        for file_item in _extract_files_from_block(stage_key, block):
            file_url = file_item["file_url"]
            if file_url in seen_urls:
                continue
            seen_urls.add(file_url)
            files.append(file_item)

    if files:
        return files

    # Fallback for unexpected markup: keep legacy attachment typing
    for file_id in FILEDOWN_PATTERN.findall(html):
        file_url = f"filedown://{file_id}"
        if file_url in seen_urls:
            continue
        seen_urls.add(file_url)
        files.append(
            _file_entry(
                file_type="attachment",
                file_name=f"{file_id}.pdf",
                file_external_id=file_id,
            )
        )
    return files


def _stage_block(html: str, stage_key: str) -> str:
    for key, block in _split_stage_blocks(html):
        if key == stage_key:
            return block
    return ""


def parse_detail_page(html: str, list_record: dict[str, Any]) -> dict[str, Any]:
    record = dict(list_record)
    approval_number = ""
    match = APPROVAL_NUMBER_PATTERN.search(html)
    if match:
        approval_number = _clean(match.group(1))
    if not approval_number:
        approval_number = _extract_field(html, "环评批文文号")

    approval_date = ""
    match = APPROVAL_DATE_PATTERN.search(html)
    if match:
        approval_date = _clean(match.group(1))
    if not approval_date:
        approval_date = _extract_field(html, "环评批文日期")

    st_eia_id = ""
    match = ST_EIA_ID_PATTERN.search(html)
    if match:
        st_eia_id = match.group(1)

    pre_block = _stage_block(html, "pre")
    pre_pub_period = _extract_field(pre_block, "公示日期")
    if not pre_pub_period:
        pre_match = re.search(r"公示日期.*?<span[^>]*>([^<]+)</span>", pre_block, re.IGNORECASE | re.DOTALL)
        if pre_match:
            pre_pub_period = _clean(pre_match.group(1))

    summary = dict(record.get("summary_json") or {})
    summary.update(
        {
            "approval_number": approval_number,
            "approval_date": approval_date,
            "planned_start_date": _extract_field(html, "计划开工日期"),
            "actual_start_date": _extract_field(html, "实际开工日期"),
            "completion_date": _extract_field(html, "竣工日期"),
            "debug_start_date": _extract_field(html, "开始调试日期"),
            "acceptance_pub_start": _extract_field(html, "公示起始日期"),
            "pre_pub_period": pre_pub_period,
            "st_eia_id": st_eia_id,
        }
    )

    files = _extract_files(html)
    record.update(
        {
            "approval_number": approval_number,
            "approval_date": approval_date,
            "st_eia_id": st_eia_id,
            "event_date": approval_date or record.get("event_date", ""),
            "summary_json": summary,
            "files": files,
            "source_url": record.get("source_url") or DETAIL_URL_TEMPLATE.format(
                external_id=record["external_id"]
            ),
        }
    )
    return record
