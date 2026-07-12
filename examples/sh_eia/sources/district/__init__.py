"""District EIA source constants shared by sync adapters."""

from __future__ import annotations

DISTRICT_FENGXIAN = "fengxian"
DISTRICT_MINHANG = "minhang"
DISTRICT_SONGJIANG = "songjiang"

DISTRICT_LABELS = {
    DISTRICT_FENGXIAN: "奉贤区",
    DISTRICT_MINHANG: "闵行区",
    DISTRICT_SONGJIANG: "松江区",
}

STAGE_ACCEPTANCE = "acceptance"
STAGE_PROPOSED = "proposed_approval"
STAGE_DECISION = "approval_decision"

STAGE_LABELS = {
    STAGE_ACCEPTANCE: "受理信息",
    STAGE_PROPOSED: "拟审批公示",
    STAGE_DECISION: "审批决定公告",
}
