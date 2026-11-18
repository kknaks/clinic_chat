"""고민 부위 → 점 · 라벨(SPEC-004 §6-2 SI1 ~ SI7 · §6-3 HN1 ~ HN4 · WORK-011 §13).

재료 = 허브가 `concern_candidates` 응답 머리에 내는 고민 부위 목록 — **SPEC-001 0.7 이 칸 이름을 정한다**(별도 발주).
그 칸을 읽는 곳은 `from_response` 하나 · 칸 이름은 상수 하나(`SITES_FIELD`). 칸이 없으면 점 0 · 라벨 0(오류 없음).
다른 길(이름 글자 맞춤 · silver CSV)로 메우지 않는다.
"""

from __future__ import annotations

from clinic_chat.consult.face_coords import DOT_SHAPES, FACE_COORDS
from clinic_chat.consult.material import CC_TOOL

# SPEC-001 0.7 §4-5 CS 의 칸 이름(0.3 FD1 — 임시 이름 `concern_sites` 에서 이 한 줄만 맞췄다)
SITES_FIELD = "sites"
PAIR_KEYS = {"concern": "concern_code", "site": "site_code", "name": "site_name", "link": "csi_id", "match": "match"}


def from_response(resp: dict, field: str = SITES_FIELD, keys: dict[str, str] | None = None) -> list[dict]:
    """응답 머리 → [{concern_code, site_code, site_name, csi_id}] · 칸이 없으면 []."""
    k = keys or PAIR_KEYS
    out = []
    for x in resp.get(field) or []:
        if not isinstance(x, dict) or not x.get(k["site"]):
            continue
        out.append({"concern_code": x.get(k["concern"]), "site_code": str(x[k["site"]]),
                    "site_name": x.get(k["name"]) or "", "csi_id": x.get(k["link"]),
                    **({"match": x.get(k["match"])} if k.get("match") and x.get(k["match"]) else {})})
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


def place(pairs: list[dict], focus: str | None = None) -> dict:
    """SI3 · SI4 — 좌표표의 꼴대로 점 · 라벨 · 같은 부위는 한 번(허브가 낸 순서).

    0.3 FD2: 짝에 `asked`(그 응답을 물은 객체 고민)가 있으면 점 · 라벨마다 `concern_codes[]`(그 부위를 가진 고민 — `하위` 면
    하위 고민 코드) · `asked_codes[]` · `match[]` · `focus`(`focus` 고민의 것인가)를 단다. 없으면 0.1 꼴 그대로."""
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
        else:
            d = labels.setdefault(code, {"site_code": code, "site_name": p.get("site_name") or "",
                                         "why": fc["shape"] if fc else "off_face"})
            if "asked" in p:
                d.setdefault("concern_codes", [])
                if p.get("concern_code") and p["concern_code"] not in d["concern_codes"]:
                    d["concern_codes"].append(p["concern_code"])
        if "asked" in p:
            d.setdefault("asked_codes", [])
            d.setdefault("match", [])
            if p["asked"] and p["asked"] not in d["asked_codes"]:
                d["asked_codes"].append(p["asked"])
            if p.get("match") and p["match"] not in d["match"]:
                d["match"].append(p["match"])
            d["focus"] = bool(focus) and (focus in d["asked_codes"] or focus in d.get("concern_codes", []))
    return {"dots": list(dots.values()), "labels": list(labels.values())}


def all_dots() -> dict:
    """`/face-check` — 좌표표의 점을 전부 켠다."""
    return {"dots": [{"site_code": c, "shape": v["shape"], "points": v["points"], "concern_codes": [], "csi_ids": []}
                     for c, v in FACE_COORDS.items() if v["shape"] in DOT_SHAPES],
            "labels": [{"site_code": c, "site_name": "", "why": v["shape"]}
                       for c, v in FACE_COORDS.items() if v["shape"] not in DOT_SHAPES]}


DIRECT = "\uc9c1\uc811"  # 허브 `sites[].match` 값 「직접」(앱 문장 아님)


def site_pairs(obj: dict) -> list[dict]:
    """0.3 FD1 → 0.4 DT1(SPEC-008 AR-5) — 객체 `concern_sites[]` 가운데 `match` = 「직접」 인 것만 `place` 의 짝(하위 고민의
    부위는 점이 아니다 — 되묻기 선택지 `plan.sub_sites` 로만 · DT3). `match` 칸이 없는 응답(옛 허브)은 직접으로 본다."""
    return [{"concern_code": x["concern_code"], "site_code": x["code"], "site_name": x.get("name") or "",
             "csi_id": x.get("csi_id"), "asked": x.get("asked") or x["concern_code"], "match": x.get("match")}
            for x in obj.get("concern_sites") or [] if x.get("match") in (None, DIRECT)]


def viewable_subs(obj: dict) -> dict[str, str]:
    """0.3 W-1 — 누를 수 있는 하위 고민(`concern_sites[]` 에 객체 고민의 하위로 실린 고민 코드 → 이름은 아직 모름이면 "")."""
    axis = {x["code"] for x in obj.get("concerns") or [] if x.get("state") in ("said", "confirmed")}
    out: dict[str, str] = {}
    for x in obj.get("concern_sites") or []:
        if x.get("asked") in axis and x.get("concern_code") and x["concern_code"] != x.get("asked") \
                and x["concern_code"] not in axis:
            out.setdefault(x["concern_code"], "")
    return out
