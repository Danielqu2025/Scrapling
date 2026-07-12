"""Unified disclosure sources and event types for sh_eia."""

from __future__ import annotations

SOURCE_LINK_STHJ = "link_sthj"
SOURCE_E2_QYGK = "e2_qygk"
SOURCE_DISTRICT_FENGXIAN = "district_fengxian"
SOURCE_DISTRICT_MINHANG = "district_minhang"
SOURCE_DISTRICT_SONGJIANG = "district_songjiang"

SOURCES = {
    SOURCE_LINK_STHJ: {
        "label": "环评审批公示（投用前）",
        "base_host": "https://link.sthj.sh.gov.cn",
    },
    SOURCE_E2_QYGK: {
        "label": "中后期信息公开（投用后）",
        "base_host": "https://e2.sthj.sh.gov.cn",
    },
    SOURCE_DISTRICT_FENGXIAN: {
        "label": "奉贤区环评公示",
        "base_host": "http://211.136.184.90:8090",
    },
    SOURCE_DISTRICT_MINHANG: {
        "label": "闵行区环评公示",
        "base_host": "https://zwgk.shmh.gov.cn",
    },
    SOURCE_DISTRICT_SONGJIANG: {
        "label": "松江区环评公示",
        "base_host": "https://www.songjiang.gov.cn",
    },
}

DISCLOSURE_TYPES = {
    "acceptance": {
        "label": "受理信息",
        "source": SOURCE_LINK_STHJ,
        "list_marker": "hpsl_list_login.jsp",
        "entry_url": "https://link.sthj.sh.gov.cn/shhj/fa/cms/shhj/hpgs_gz_login.jsp?applyItem=1&gongshiType=1&approvType=1",
    },
    "proposed_approval": {
        "label": "拟审批公示",
        "source": SOURCE_LINK_STHJ,
        "list_marker": "hpnsp_list_login.jsp",
        "entry_url": "https://link.sthj.sh.gov.cn/shhj/fa/cms/shhj/hpgs_gz_login.jsp?applyItem=1&gongshiType=2&approvType=1",
    },
    "approval_decision": {
        "label": "审批决定公告",
        "source": SOURCE_LINK_STHJ,
        "list_marker": "hpxm_list_login.jsp",
        "entry_url": "https://link.sthj.sh.gov.cn/shhj/fa/cms/shhj/hpgs_gz_login.jsp?applyItem=1&gongshiType=3&approvType=1",
    },
    "post_construction": {
        "label": "中后期信息公开",
        "source": SOURCE_E2_QYGK,
        "list_url": "https://e2.sthj.sh.gov.cn/qygkweb/jsp/view/hjxxgk/jsxmzhq_list.jsp",
        "detail_url_template": "https://e2.sthj.sh.gov.cn/qygkweb/jsp/view/jsxmInfo_edit.jsp?id={external_id}",
    },
}

LINK_STHJ_TYPES = [k for k, v in DISCLOSURE_TYPES.items() if v["source"] == SOURCE_LINK_STHJ]
E2_QYGK_TYPES = [k for k, v in DISCLOSURE_TYPES.items() if v["source"] == SOURCE_E2_QYGK]
# 区级与市级共用 disclosure_type 键；入库时 source 写 district_*。
DISTRICT_FENGXIAN_TYPES = list(LINK_STHJ_TYPES)
DISTRICT_MINHANG_TYPES = list(LINK_STHJ_TYPES)
# 松江公开接口目前仅「环评项目公告」≈ 审批决定。
DISTRICT_SONGJIANG_TYPES = ["approval_decision"]

FILE_TYPE_LABELS = {
    "report": "环评报告",
    "approval": "批复文件",
    "notice": "受理公示",
    "public_participation": "公众参与说明",
    "measures": "环保措施",
    "construction_measures": "施工期环保措施",
    "construction_monitoring": "施工期监测结果",
    "adjustment_report": "非重大调整报告",
    "debug_measures": "调试期环保措施",
    "e2_debug": "调试期附件",
    "e2_construction": "建设期附件",
    "e2_acceptance": "验收期附件",
    "acceptance_report": "验收监测报告",
    "acceptance_opinion": "验收意见",
    "acceptance_other": "验收其他说明",
    "pre_approval_notice": "报批前公示",
    "pre_approval_entrust": "公示委托书",
    "attachment": "附件",
}

# e2 详情页「报批前公示」tab 的附件类型（解析用；展示时归并到 link 三步，见 timeline_view）
E2_PHASE_FILE_TYPES: dict[str, set[str]] = {
    "pre": {"pre_approval_notice", "pre_approval_entrust"},
    "basic": set(),
    "construction": {"construction_measures", "construction_monitoring", "e2_construction"},
    "debug": {"adjustment_report", "debug_measures", "e2_debug"},
    "acceptance": {"acceptance_report", "acceptance_opinion", "acceptance_other", "e2_acceptance"},
}

TYPE_SORT_ORDER = {
    "acceptance": 1,
    "proposed_approval": 2,
    "approval_decision": 3,
    "post_construction": 4,
}

SORT_BY_OPTIONS = ("event_date", "synced_at", "type", "project_name")
SORT_ORDER_OPTIONS = ("asc", "desc")

SCHEMA_VERSION = 2
