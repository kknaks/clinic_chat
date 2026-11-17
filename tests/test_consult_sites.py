"""WP-8 — 부위 → 점 · 라벨(T10 · T25) · 좌표표(T11 · FC10)."""

from __future__ import annotations

import re
import subprocess

from clinic_chat.config import ROOT
from clinic_chat.consult.face_coords import FACE_COORDS, SHAPES
from clinic_chat.consult.render import Render
from clinic_chat.consult.sites import SITES_FIELD, build_sites, from_response, place
from tests.consult_fixture import consult_hub
from tests.test_consult_material import _walk


def test_t11_coords_table():
    assert len(FACE_COORDS) == 60 and all(re.fullmatch(r"SITE-\d{3}", c) for c in FACE_COORDS)
    counts = {s: sum(1 for v in FACE_COORDS.values() if v["shape"] == s) for s in SHAPES}
    assert counts == {"point": 17, "pair": 27, "area": 8, "line": 4, "none": 4}
    want = {"point": 1, "pair": 2, "area": 0, "line": 0, "none": 0}
    for c, v in FACE_COORDS.items():
        assert len(v["points"]) == want[v["shape"]], c
        for x, y in v["points"]:
            assert 0 <= x <= 1 and 0 <= y <= 1, c
        if v["shape"] == "pair":
            (lx, ly), (rx, ry) = v["points"]
            assert lx < 0.5 < rx and ly == ry, c
    assert sum(len(v["points"]) for v in FACE_COORDS.values()) == 71


def test_t10_dots_labels_once_and_empty():
    pairs = [{"concern_code": "C1", "site_code": "SITE-014", "site_name": "a", "csi_id": "1"},
             {"concern_code": "C2", "site_code": "SITE-014", "site_name": "a", "csi_id": "2"},
             {"concern_code": "C1", "site_code": "SITE-046", "site_name": "b", "csi_id": "3"},
             {"concern_code": "C1", "site_code": "SITE-007", "site_name": "c", "csi_id": "4"},
             {"concern_code": "C1", "site_code": "SITE-043", "site_name": "d", "csi_id": "5"},
             {"concern_code": "C1", "site_code": "SITE-100", "site_name": "e", "csi_id": "6"},
             {"concern_code": "C1", "site_code": "SITE-100", "site_name": "e", "csi_id": "7"}]
    s = place(pairs)
    assert [(d["site_code"], len(d["points"])) for d in s["dots"]] == [("SITE-014", 1), ("SITE-046", 2)]
    assert s["dots"][0]["concern_codes"] == ["C1", "C2"] and s["dots"][0]["csi_ids"] == ["1", "2"]
    assert [(lb["site_code"], lb["why"]) for lb in s["labels"]] == [("SITE-007", "area"), ("SITE-043", "none"),
                                                                     ("SITE-100", "off_face")]
    html = Render().sites(s)
    assert html.count("<circle") == 3 and html.count('data-site-code="SITE-046"') == 2
    assert ">e<" in html and "SITE-" not in re.sub(r'data-site-code="[^"]+"', "", html)  # 점 옆 글 없음 · 라벨은 이름
    empty = Render().sites(place([]))
    assert 'id="face-marks"' in empty and "<circle" not in empty and len(empty) > 0  # 빈 조각 = 빈 요소(W-3 ③)


async def test_t25_hub_without_field_is_empty_not_error(tmp_path):
    w = await _walk(tmp_path, "가짜고민 시술", {"indication": 0.9})
    assert build_sites(w.reads) == {"dots": [], "labels": []}
    sites = [{"concern_code": "ZZC-1", "site_code": "SITE-046", "site_name": "가짜부위", "csi_id": "CSI-1"}]
    w2 = await _walk(tmp_path, "가짜고민 시술", {"indication": 0.9}, hub=consult_hub(sites=sites, sites_field="tmp_name"))
    assert build_sites(w2.reads, field="tmp_name")["dots"][0]["site_code"] == "SITE-046"
    assert build_sites(w2.reads) == {"dots": [], "labels": []}  # 칸 이름이 다르면 점 0(다른 길로 메우지 않음)
    assert build_sites([]) is None
    assert from_response({SITES_FIELD: [{"site_code": "SITE-014"}]})[0]["site_code"] == "SITE-014"


def test_no_png_in_repo():
    out = subprocess.run(["git", "-C", str(ROOT), "ls-files", "--others", "--cached", "--exclude-standard"],
                         capture_output=True, text=True, check=True).stdout.split()
    assert not [f for f in out if f.lower().endswith((".png", ".jpg", ".jpeg"))]
