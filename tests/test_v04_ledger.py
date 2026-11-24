"""S8-T17 — 장부 · 패널(SPEC-008 §6 · WORK-017 WP-8). 가짜 CLI · 가짜 허브."""

from __future__ import annotations

import html
import json
import re

from clinic_chat.consult.ledger import CONSULT_REV
from clinic_chat.consult.render import state_view
from tests.state_fixture import Run
from tests.v04_fixture import set_out

UNDERSTAND_KEYS = {"model", "thinking", "pick_version", "list_sha", "aliases", "input_bytes", "output_raw", "parsed",
                   "accepted", "dropped", "merged", "added_from_sites", "ms", "api_ms", "usage", "models", "error", "retry"}
GONE = ("retract", "retract_ms", "retract_resolve_ms", "retract_requests", "options_carried", "jev_requests")


def test_s8_t17_ledger_fields(tmp_path, fake_cli):
    r = Run(tmp_path, fake_cli, chat_debug_panel="on")
    try:
        set_out(fake_cli[1], [("concern", "ZZC-1"), ("concern", "ZZC-404")], sites=["ZZS-1"], kinds=["indication"])
        row, kinds, body = r.turn("가짜고민 가짜부위")
        c = row["consult"]
        assert set(c["understand"]) == UNDERSTAND_KEYS
        u = c["understand"]
        assert u["pick_version"] == "0.6.0-draft" and u["added_from_sites"] == ["ZZS-1"] and u["dropped"][0]["code"] == "ZZC-404"
        assert set(u["input_bytes"]) == {"system", "message"} and set(u["usage"]) == {
            "input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens", "thinking_tokens"}
        assert "가짜고민 가짜부위" not in json.dumps(u, ensure_ascii=False).replace(u["output_raw"], "")  # 메시지 전문 없음
        assert set(c["site_filter"]) == {"sites", "carried", "from", "pair_kept", "pair_dropped"}
        assert c["turn_kind"] == {"kind": "new", "from": "understand"}
        for k in GONE:
            assert k not in c
        assert all(set(t) == {"table", "code", "name", "sure", "how"} for t in row["targets"])
        assert "entry" not in row["trace"] and "entry_pick" not in row["trace"]
        assert set(row["trace"]["depth"]) == {"decisions", "read"}
        assert set(row["timing"]["parts"]) == {"understand_ms", "hub_ms", "llm_ms", "material_ms"}
        assert all("noul" not in (x.get("source") or {}) and "sure" in x["source"]
                   for x in c["after"]["concerns"] + c["after"]["sites"])
        assert r.rows("start")[0]["consult_rev"] == CONSULT_REV == 4
        block = body.split("event: state\n", 1)[1].split("\n\n", 1)[0]
        frag = html.unescape(block)
        assert 'data-key="understand"' in frag and 'data-key="site_filter"' in frag
        fields = dict(state_view(row)["fields"])
        assert {"understand", "site_filter"} <= set(fields)
        assert not re.search(r"[가-힣]", "".join(k for k in fields))  # 칸 이름 = 장부 키
    finally:
        r.close()
