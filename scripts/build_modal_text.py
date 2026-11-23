"""테스트 고침 B(사용자 11-22) — 모달 효과 · 부작용 고객 문장을 미리 만든다.

    PYTHONPATH=. .venv/bin/python scripts/build_modal_text.py [--only CAS-…,CAS-…] [--jobs 6] [--out-dir data/kos …]

1. 명동점 매핑 JSON(`CHAT_KOS_MAP`)의 케이스 전부 → 허브 `facets profile · outcome · adverse`(모달과 같은 값 · 계열 줄 뺌 ·
   이름 합침 · 분류 · 경고 줄 — `modal.effects` · `modal.sides` 그대로).
2. 원문 등급(low · very_low · 별점 …)은 `prompts/modal_text.json` `grades` · `stars` 규칙 표로 앱이 먼저 바꾼다.
3. 부위 맞춤: 케이스 부위가 얼굴(`$J` sites region face)이면 몸 전용 효과(허브 효과 나무 EFF-090 바디 컨투어링 아래 —
   `facets outcome node=effect:EFF-090` 가 내는 효과 코드)는 뺀다.
4. 케이스마다 claude CLI(앱 이해 호출과 같은 실행기 · 모델 = 앱 설정 `chat_pick_model`) 한 번 → JSON `items[]`.
5. 검증: 이름 · 분류는 입력 값으로 덮음 · 입력에 없는 칸은 빈 값 · 문장 안 숫자가 입력에 없으면 문장 버림(`dropped`).
6. 결과 `modal-text-<gold 앞 8>.json`(키 = 케이스 코드 · meta = gold 해시 · 만든 때)을 `--out-dir` 마다 쓴다.
   케이스 한 줄 설명 `desc_line` = 허브 케이스 profile 의 시술 이름 · 부위 · 방법 이음(LLM 아님 · 카드 · 모달 설명 칸).
"""

from __future__ import annotations

import argparse
import asyncio
import datetime
import hashlib
import json
import re
import sys
import time
from pathlib import Path

from clinic_chat.clients.hub_rest import HubRest
from clinic_chat.config import ROOT, load_settings
from clinic_chat.consult import kos_map, modal
from clinic_chat.consult import understand as und

PROMPT = ROOT / "prompts" / "modal_text.json"
BODY_ROOT = "EFF-090"
EFFECT_CSV = (
    Path("/Users/kknaks/orca/workspaces/kknaks_profile")
    / "\uc0c1\ub2f4\ubd07"
    / ("orchestration/work/phub-consult-demo/silver-model/silver/effect.csv")
)  # 허브 원천 silver 효과 표(효과 나무 — 허브가 내지 않음)
EFFECT = "효과"  # 효과 항목의 class 값
SEP = " · "
STAR = re.compile(r"^[★☆]+$")
NUM = re.compile(r"\d+(?:[.,]\d+)?")


def grade(v: str, p: dict) -> str:
    t = (v or "").strip()
    if STAR.match(t):
        return p["stars"].format(n=t.count("★"))
    return p["grades"].get(t, p["grades"].get(t.lower(), t))


async def rows(hub, node: str, facet: str) -> list[dict]:
    ra = await hub.read_all("facets", {"facet": facet, "node": node})
    if not ra.complete:
        raise RuntimeError(f"{facet} {node}: {ra.error}")
    return list(ra.items)


async def body_effects(hub, effect_csv: str | None = None) -> set[str]:
    """몸 전용 효과 = EFF-090(바디 컨투어링)과 그 아래. 허브는 효과 나무를 내지 않아(`scope` 효과 노드 없음 · outcome 펼침도 자기 코드만)
    허브의 원천인 silver `effect.csv` 의 `parent_code` 로 아래를 찾는다(없으면 허브가 내는 코드만)."""
    out = {str(r.get("effect_code")) for r in await rows(hub, f"effect:{BODY_ROOT}", "outcome") if r.get("effect_code")}
    out.add(BODY_ROOT)
    if effect_csv and Path(effect_csv).is_file():
        import csv

        par = {r["code"]: r["parent_code"] for r in csv.DictReader(open(effect_csv, encoding="utf-8-sig"))}
        for c in par:
            x, seen = c, set()
            while x and x not in seen:
                seen.add(x)
                if x == BODY_ROOT:
                    out.add(c)
                    break
                x = par.get(x, "")
    return out


async def gather(hub, kos, case: str, p: dict, body: set[str]) -> dict:
    node = f"procedure_case:{case}"
    prof = [r for r in await rows(hub, node, "profile") if r.get("subject_code") == case]
    own = prof[0] if prof else {}
    site = str(own.get("site_name") or "")
    region = next((v.get("region") for v in kos.sites.values() if v.get("name") == site), "")
    desc_line = SEP.join(x for x in (own.get("procedure_name"), site, own.get("methods")) if x)
    eff_rows = [{**r, "_case": case} for r in await rows(hub, node, "outcome") if r.get("effect_code")]
    dropped_body = []
    if region == "face":
        keep = []
        for r in eff_rows:
            (dropped_body if r.get("effect_code") in body else keep).append(r)
        eff_rows = keep
    side_rows = [{**r, "_case": case} for r in await rows(hub, node, "adverse") if r.get("polarity") == modal.OCCURS]
    effs = [e for e in modal.effects(eff_rows) if e["name"]]  # 이름 · 코드 없는 허브 행(분류 빈칸 5)은 뺌
    sides = [e for e in modal.sides(side_rows) if e["name"]]
    g = lambda xs: [grade(x, p) for x in xs]  # noqa: E731
    return {
        "case": case,
        "procedure": own.get("procedure_name") or "",
        "site": site,
        "region": region,
        "desc_line": desc_line,
        "body_dropped": sorted({str(r.get("effect_name")) for r in dropped_body}),
        "effects": [
            {"name": e["name"], "onset": g(e["onset"]), "peak": g(e["peak"]), "duration": g(e["duration"])}
            for e in effs
        ],
        "sides": [
            {
                "name": e["name"],
                "class": e["cls"],
                "freq": g(e["freq"]),
                "severity": g(e["severity"]),
                "onset": g(e["onset"]),
                "duration": g(e["duration"]),
                "care": e["care"],
                "alerts": e["alerts"],
            }
            for e in sides
        ],
    }


def _parse(raw: str) -> dict:
    """첫 `{` 부터 JSON 객체 하나(뒤에 붙은 글은 버림 — CAS-1053 「Extra data」)."""
    i = raw.find("{")
    if i < 0:
        raise ValueError("no_object")
    obj, _ = json.JSONDecoder().raw_decode(raw[i:])
    return obj


SENT_END = re.compile(r"[.!?]|\uc694(?=\s|$)")  # 문장 끝(마침표 · 물음표 · 느낌표 · 줄 끝 「요」)
HOSPITAL = "\ubcd1\uc6d0"  # 「병원」
URGENT = "\uc704\uae09"  # 「위급」
MAX_SENTENCES = 2
FIXABLE = ("many_numbers", "too_long", "urgent_no_hospital", "number_not_in_hub")


def sentences(s: str) -> int:
    """문장 수 — 마침표 · 물음표 · 느낌표로 끊고(끝 문장부호 없는 꼬리 하나도 셈)."""
    parts = [x for x in re.split(r"(?<=[.!?])\s*", s.strip()) if x.strip()]
    return len(parts)


def check(inp: dict, out: dict) -> tuple[list[dict], list[dict]]:
    """테스트 고침 C · D — 항목마다 {name, class, sentence} 하나. 이름 · 분류는 입력으로 덮고, 입력에 없는 숫자가 든 문장은 버리고
    (`number_not_in_hub`), 두 문장 넘음(`too_long`) · 위급인데 병원 말 없음(`urgent_no_hospital`)은 기록한다."""
    got = {str(x.get("name")): x for x in (out.get("items") or []) if isinstance(x, dict)}
    nums = set(NUM.findall(json.dumps(inp, ensure_ascii=False)))
    items, bad = [], []
    rowsrc = [(e, EFFECT) for e in inp["effects"]] + [(e, e["class"]) for e in inp["sides"]]
    for e, cls in rowsrc:
        o = got.get(e["name"]) or {}
        s = " ".join(str(o.get("sentence") or "").split())
        if not o:
            bad.append({"name": e["name"], "why": "missing_in_llm"})
        extra = [n for n in NUM.findall(s) if n not in nums]
        if extra:
            bad.append({"name": e["name"], "why": "number_not_in_hub", "numbers": extra, "sentence": s})
            s = ""
        if s and sentences(s) > MAX_SENTENCES:
            bad.append({"name": e["name"], "why": "too_long", "sentence": s})
        if s and len(re.findall(r"\d+(?:[~\-]\d+)?", s)) > 1:
            bad.append({"name": e["name"], "why": "many_numbers", "sentence": s})
        if s and cls == URGENT and HOSPITAL not in s:
            bad.append({"name": e["name"], "why": "urgent_no_hospital", "sentence": s})
        items.append({"name": e["name"], "class": cls, "sentence": s})
    return items, bad


async def one(s, hub, kos, case, p, body, cache_dir: Path, sem) -> dict:
    inp = await gather(hub, kos, case, p, body)
    key = hashlib.sha256(json.dumps([p["version"], inp], ensure_ascii=False, sort_keys=True).encode()).hexdigest()[
        :16
    ]  # 프롬프트 판이 바뀌면 다시 부름
    cf = cache_dir / f"{case}-{key}.json"
    calls = 0
    if cf.is_file():
        out = json.loads(cf.read_text(encoding="utf-8"))
    elif not inp["effects"] and not inp["sides"]:
        out = {"items": []}
    else:
        async with sem:
            calls = 1
            raw = await und.call(s, "\n".join(p["system"]), json.dumps(inp, ensure_ascii=False))
        if raw.error:
            return {"case": case, "error": raw.error, "input": inp, "calls": calls}
        try:
            out = _parse(raw.output_raw)
        except ValueError as e:
            return {"case": case, "error": {"kind": "json", "detail": str(e)[:100]}, "input": inp, "calls": calls}
        cf.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    items, bad = check(inp, out)
    fixable = [x for x in bad if x["why"] in FIXABLE]
    if fixable:  # 테스트 고침 D — 규칙을 어긴 항목만 한 번 다시(통과한 것만 씀 · 장부 `dropped` 에 남은 것만)
        ff = cache_dir / f"{case}-{key}-fix.json"
        if ff.is_file():
            out2 = json.loads(ff.read_text(encoding="utf-8"))
        else:
            async with sem:
                calls += 1
                msg = {
                    "input": inp,
                    "fix": [{"name": x["name"], "why": x["why"], "sentence": x.get("sentence", "")} for x in fixable],
                }
                raw2 = await und.call(
                    s, "\n".join(p["system"]) + "\n" + p["fix_rule"], json.dumps(msg, ensure_ascii=False)
                )
            out2 = {"items": []}
            if not raw2.error:
                try:
                    out2 = _parse(raw2.output_raw)
                except ValueError:
                    out2 = {"items": []}
            ff.write_text(json.dumps(out2, ensure_ascii=False), encoding="utf-8")
        names = {x["name"] for x in fixable}
        merged = {
            "items": [o for o in out.get("items") or [] if o.get("name") not in names] + list(out2.get("items") or [])
        }
        items2, bad2 = check(inp, merged)
        still = {x["name"] for x in bad2}
        by2 = {i["name"]: i for i in items2}
        items = [
            by2[i["name"]] if i["name"] in names and i["name"] not in still and by2[i["name"]]["sentence"] else i
            for i in items
        ]
        fixed = {n for n in names if n not in still}
        bad = [x for x in bad if x["name"] not in fixed] + [{"name": n, "why": "fixed"} for n in sorted(fixed)]
    return {"case": case, "input": inp, "items": items, "bad": bad, "calls": calls}


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    ap.add_argument("--jobs", type=int, default=6)
    ap.add_argument("--out-dir", action="append", default=[])
    ap.add_argument("--cache-dir", default=str(ROOT / "runs" / "modal-text-cache"))
    ap.add_argument("--effect-csv", default=str(EFFECT_CSV))
    a = ap.parse_args()
    s = load_settings(chat_pick_timeout_sec=240.0)
    kos = kos_map.from_settings(s)
    p = json.loads(PROMPT.read_text(encoding="utf-8"))
    hub = HubRest(s.hub_rest_url, page_limit=s.hub_page_limit)
    gold = (await hub.health())["build"]["gold_hash"]
    cases = sorted({c for it in kos.items for c in it["hub"].get("case_codes") or []})
    if a.only:
        cases = [c for c in cases if c in a.only.split(",")]
    body = await body_effects(hub, a.effect_csv)
    cache = Path(a.cache_dir)
    cache.mkdir(parents=True, exist_ok=True)
    sem = asyncio.Semaphore(a.jobs)
    t0 = time.time()
    res = await asyncio.gather(*(one(s, hub, kos, c, p, body, cache, sem) for c in cases))
    ok = {r["case"]: r for r in res if "items" in r}
    doc = {
        "meta": {
            "version": "modal-text-v1",
            "hub_gold_hash": gold,
            "kos_map_sha": kos.sha256,
            "made_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
            "model": s.chat_pick_model,
            "prompt_version": p["version"],
            "cases": len(ok),
            "errors": [{"case": r["case"], "error": r["error"]} for r in res if "error" in r],
            "llm_calls": sum(r.get("calls", 0) for r in res),
            "body_effects": sorted(body),
        },
        "cases": {
            c: {
                "desc_line": r["input"]["desc_line"],
                "site": r["input"]["site"],
                "region": r["input"]["region"],
                "body_dropped": r["input"]["body_dropped"],
                "items": r["items"],
                "dropped": r["bad"],
            }
            for c, r in sorted(ok.items())
        },
    }
    raw = json.dumps(doc, ensure_ascii=False, indent=1).encode()
    for d in a.out_dir or [str(ROOT / "data" / "kos")]:
        f = Path(d) / f"modal-text-{gold[:8]}.json"
        f.write_bytes(raw)
        print(f"wrote {f} sha256 {hashlib.sha256(raw).hexdigest()}")
    m = doc["meta"]
    print(
        f"cases {m['cases']}/{len(cases)} · llm_calls {m['llm_calls']} · errors {len(m['errors'])} · "
        f"dropped {sum(len(v['dropped']) for v in doc['cases'].values())} · {time.time() - t0:.0f}s"
    )
    return 0 if not m["errors"] else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
