"""고민 부위 → 점 · 라벨(SPEC-004 §6-2 SI1 ~ SI7 · §6-3 HN1 ~ HN4 · WORK-011 §13).

재료 = 허브가 `concern_candidates` 응답 머리에 내는 고민 부위 목록 — **SPEC-001 0.7 이 칸 이름을 정한다**(별도 발주).
그 칸을 읽는 곳은 `from_response` 하나 · 칸 이름은 상수 하나(`SITES_FIELD`). 칸이 없으면 점 0 · 라벨 0(오류 없음).
다른 길(이름 글자 맞춤 · silver CSV)로 메우지 않는다.
"""

from __future__ import annotations

from clinic_chat.consult.face_coords import DOT_SHAPES, FACE_COORDS
from clinic_chat.consult.material import CC_TOOL

# 0.7 이 칸 이름을 정하면 이 줄만 맞춘다(그때 점이 켜진다) — 지금은 임시 이름
SITES_FIELD = "concern_sites"
PAIR_KEYS = {"concern": "concern_code", "site": "site_code", "name": "site_name", "link": "csi_id"}


def from_response(resp: dict, field: str = SITES_FIELD, keys: dict[str, str] | None = None) -> list[dict]:
    """응답 머리 → [{concern_code, site_code, site_name, csi_id}] · 칸이 없으면 []."""
    k = keys or PAIR_KEYS
    out = []
    for x in resp.get(field) or []:
        if not isinstance(x, dict) or not x.get(k["site"]):
            continue
        out.append({"concern_code": x.get(k["concern"]), "site_code": str(x[k["site"]]),
                    "site_name": x.get(k["name"]) or "", "csi_id": x.get(k["link"])})
    return out


def build_sites(reads, field: str = SITES_FIELD, keys: dict[str, str] | None = None) -> dict | None:
    """장부 `sites` 꼴 — `concern_candidates` 를 읽지 않았으면 None(보내지 않는다). 읽었는데 부위 0 → 빈 꼴(SI5)."""
    pairs: list[dict] = []
    read = False
    for r in reads:
        if r.call.tool != CC_TOOL or r.call.purpose != "read":
            continue
        read = True
        pairs.extend(from_response(r.ra.as_response(), field, keys))
    if not read:
        return None
    return place(pairs)


def place(pairs: list[dict]) -> dict:
    """SI3 · SI4 — 좌표표의 꼴대로 점 · 라벨 · 같은 부위는 한 번(허브가 낸 순서)."""
    dots: dict[str, dict] = {}
    labels: dict[str, dict] = {}
    for p in pairs:
        code = p["site_code"]
        fc = FACE_COORDS.get(code)
        if fc and fc["shape"] in DOT_SHAPES:
            d = dots.setdefault(code, {"site_code": code, "shape": fc["shape"], "points": fc["points"],
                                       "concern_codes": [], "csi_ids": []})
            if p.get("concern_code") and p["concern_code"] not in d["concern_codes"]:
                d["concern_codes"].append(p["concern_code"])
            if p.get("csi_id") and p["csi_id"] not in d["csi_ids"]:
                d["csi_ids"].append(p["csi_id"])
        elif code not in labels:
            labels[code] = {"site_code": code, "site_name": p.get("site_name") or "",
                            "why": fc["shape"] if fc else "off_face"}
    return {"dots": list(dots.values()), "labels": list(labels.values())}


def all_dots() -> dict:
    """`/face-check` — 좌표표의 점을 전부 켠다."""
    return {"dots": [{"site_code": c, "shape": v["shape"], "points": v["points"], "concern_codes": [], "csi_ids": []}
                     for c, v in FACE_COORDS.items() if v["shape"] in DOT_SHAPES],
            "labels": [{"site_code": c, "site_name": "", "why": v["shape"]}
                       for c, v in FACE_COORDS.items() if v["shape"] not in DOT_SHAPES]}
