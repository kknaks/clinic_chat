"""상담 재료 덧붙이기(SPEC-004 §5-3 AP1 ~ AP8 · WORK-011 §10 · 검수 W-1 · W-2 · B-6).

- 걷기 안의 줄 번호는 1 부터 그대로다(`walk/material.build` · `gate` 를 안 고친다). 이 모듈이 걷기 **뒤에** 묶음을 복사해
  gid → 대화 줄 번호로 옮겨 적는다: 새 gid 만 `next_n` 부터 새 번호 · 앞 턴에 실은 gid 는 옛 번호(`seen_lines`) ·
  묶음 머리 `lines` 와 표지 `conflict_lines` 도 옮긴 번호로. 새 줄이 0 인 묶음은 뺀다. 글은 `to_lines`(안 고침).
- 목록 요약 줄(AP4 · AP5): `concern_candidates` 조회와 그 이어 조회(후보별 profile)에서**만** 온 행은 계열 하나에 한 줄.
  요약 줄의 `gids[]` 는 근거 되짚기용이다 — 「실은 gid」 에 넣지 않는다(W-2). 같은 요약(고민 · 계열 · gid 집합)은 다시
  싣지 않고 그 줄 번호를 `seen_lines` 로(B-6).
- 이름 목록은 싣지 않는다(AP7). 한 턴 메시지가 `ANSWER_INPUT_MAX_TOKENS` 를 넘으면 `FT5`(AP8 — 부르는 쪽이 판단).
- 0.3(SPEC-006 §3-3 · S6-T9 · WORK-014 §9): 켠 상태(rev 3)의 「앱 판정」 은 `judgement_v3` — `read_as[]` · `offered[].rows` ·
  `unresolved[]` 를 빼고 `offered` 는 종류 이름(`kind_labels`)만 · `ask.options[].layer` 대신 `holder` · `turn_kind` · `empty[]` ·
  `site` 를 더한다. 끝맺음 · 앞 턴은 「앱 판정」 안의 블록(`turn_headers.close` · `turn_headers.prev`)으로 싣는다(`message_v3`).
  끈 상태는 0.1 그대로(`col_b.judgement` 는 안 고친다).
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass, field

from clinic_chat.consult import fetch_read as fr
from clinic_chat.consult.prompt import ConsultPrompt
from clinic_chat.walk.fold import est_tokens
from clinic_chat.walk.material import to_lines
from clinic_chat.walk.verified import response_rows

CC_TOOL = "concern_candidates"


@dataclass
class TurnMaterial:
    groups: list[dict] = field(default_factory=list)  # 대화 번호로 옮긴 묶음(새 줄만)
    summary_groups: list[dict] = field(default_factory=list)  # 목록 요약 묶음(새 요약만)
    new_lines: list[dict] = field(default_factory=list)  # 장부 `material.new_lines[]`
    seen_lines: list[int] = field(default_factory=list)
    next_n: int = 1

    def ledger(self) -> dict:
        return {"new_lines": self.new_lines, "seen_lines": self.seen_lines, "next_n": self.next_n}

    @property
    def empty(self) -> bool:
        return not self.groups and not self.summary_groups


def _uniq(xs) -> list:
    return list(dict.fromkeys(x for x in xs if x not in (None, "")))


def summary_only_gids(reads) -> set[str]:
    """`concern_candidates` 조회 · 그 이어 조회(profile)에서만 온 gid(AP4)."""
    listed: set[str] = set()
    other: set[str] = set()
    for r in reads:
        if r.call.purpose not in ("read", "depth"):
            continue
        if r.call.tool == CC_TOOL or (r.call.meta.get("followup") and r.call.meta.get("from_group")):
            listed.update(r.gids)
        else:
            other.update(r.gids)
    return listed - other


def _profiles(reads) -> dict[str, list[dict]]:
    """후보 노드 → 그 profile 행(이어 조회)."""
    out: dict[str, list[dict]] = {}
    for r in reads:
        node = r.call.meta.get("from_group")
        if node and r.call.meta.get("followup"):
            out.setdefault(node, []).extend(response_rows(r.ra.as_response()))
    return out


def summaries(reads, only: set[str], v3: bool = False) -> list[dict]:
    """고민마다 묶음 하나 — 계열마다 한 줄 `{modality_name, procedures, reach[], concern_match[], mechanism_name[], gids[]}`.
    0.3 LS6(`v3`): 내려온 행(`descended[].from[].gid`)은 그 계열 묶음의 줄로 모은다(`modality_level` 참 · 시술 수 = 그 계열의
    내려온 시술 수) — 줄에서 `reach` · `concern_match` 를 뺀 꼴은 `renumber` 가 짓는다."""
    prof = _profiles(reads)
    out: list[dict] = []
    by_concern: dict[str, dict] = {}
    for r in reads:
        if r.call.tool != CC_TOOL or r.call.purpose != "read":
            continue
        resp = r.ra.as_response()
        code = str(r.call.request.get("concern_code") or "")
        blk = by_concern.get(code)
        if blk is None:
            blk = {"concern_code": code, "concern_name": "", "inherited_from": resp.get("inherited_from"), "mods": {}}
            by_concern[code] = blk
            out.append(blk)
        blk["inherited_from"] = blk["inherited_from"] or resp.get("inherited_from")
        seen = {g for m in blk["mods"].values() for g in m["gids"]}

        def mod(name: str, blk: dict = blk, level: bool = False) -> dict:
            key = ("\x00" if level else "") + name
            if key not in blk["mods"]:
                blk["mods"][key] = {"modality_name": name, "procs": [], "reach": [], "concern_match": [],
                                    "mechanism_name": [], "gids": [], "modality_level": level}
            return blk["mods"][key]

        desc_of: dict[str, dict] = {}
        if v3:
            for dd in resp.get("descended") or []:
                for fr in dd.get("from") or []:
                    desc_of.setdefault(str(fr.get("gid")), dd)

        for g in resp.get("groups") or []:
            node = f"{g.get('candidate_table')}:{g.get('candidate_code')}"
            pr = [x for x in prof.get(node, []) if f"{x.get('subject_table')}:{x.get('subject_code')}" == node]
            name = next((str(x.get("modality_name")) for x in pr if x.get("modality_name")), "")
            m = mod(name)
            gids = [str(x.get("gid")) for x in g.get("rows") or [] if str(x.get("gid")) in only]
            gids += [str(x.get("gid")) for x in pr if str(x.get("gid")) in only]
            gids = [x for x in gids if x not in seen]
            if not gids:
                continue
            seen.update(gids)
            m["gids"].extend(gids)
            if g.get("procedure_code"):
                m["procs"].append(g["procedure_code"])
            m["reach"].extend(g.get("reach") or [])
            m["concern_match"].extend(g.get("concern_match") or [])
            m["mechanism_name"].extend(x.get("mechanism_name") for x in pr)
            for x in g.get("rows") or []:
                blk["concern_name"] = blk["concern_name"] or str(x.get("asked_concern_name") or "")
        for key, by_name in (("modality_only", True), ("unreached", False)):
            for x in resp.get(key) or []:
                gid = str(x.get("gid"))
                if gid not in only or gid in seen:
                    continue
                seen.add(gid)
                dd = desc_of.get(gid)
                if dd is not None:  # LS6 — 내려온 계열 묶음 줄
                    m = mod(str(dd.get("modality_name") or ""), level=True)
                    m["procs"].extend(str(pr.get("procedure_code")) for pr in dd.get("procedures") or []
                                      if not pr.get("in_groups"))
                else:
                    m = mod(str(x.get("candidate_name") or "") if by_name else "")
                m["gids"].append(gid)
                m["reach"].append(x.get("reach"))
                m["concern_match"].append(x.get("concern_match"))
                blk["concern_name"] = blk["concern_name"] or str(x.get("asked_concern_name") or "")
    for blk in out:
        blk["lines"] = [{"modality_name": m["modality_name"], "procedures": len(_uniq(m["procs"])),
                         "reach": _uniq(m["reach"]), "concern_match": _uniq(m["concern_match"]),
                         "mechanism_name": _uniq(m["mechanism_name"]), "gids": m["gids"],
                         "modality_level": m["modality_level"]}
                        for m in blk.pop("mods").values() if m["gids"]]
    return [b for b in out if b["lines"]]


def summary_key(concern_code: str, modality_name: str, gids: list[str]) -> str:
    h = hashlib.sha256("\n".join(sorted(gids)).encode("utf-8")).hexdigest()[:16]
    return f"{concern_code}|{modality_name}|{h}"


V3_SUMMARY = ("modality_name", "procedures", "mechanism_name", "modality_level")  # 0.3 LS6 — reach · concern_match 뺌
V1_SUMMARY = ("modality_name", "procedures", "reach", "concern_match", "mechanism_name")


def renumber(w, sent: dict[str, int], summaries_seen: dict[str, int], next_n: int, v3: bool = False,
             cut: dict[str, tuple[str, str]] | None = None, sent_names: dict[str, int] | None = None) -> TurnMaterial:
    """걷기 결과 → 이번 턴에 새로 싣는 것(대화 번호). `v3`(0.3 켠 상태) = 요약 줄 꼴 LS6.

    SPEC-011(0.6) — `cut` = 2차 읽기(`fetch_read.decide`)의 gid → (전체 | 이름만 | 뺌). 뺌은 싣지 않는다 · 이름만은 이름 칸만
    (묶음을 갈라 머리도 이름 칸만 · 머리 `marks.names_only`). G7 — 이름만 실은 gid 는 `sent` 가 아니라 `sent_names` 에 둔다:
    뒤 턴에 같은 gid 를 전체로 읽게 되면 **같은 번호로** 상세를 다시 싣는다 · 다시 이름만이면 그 번호를 `seen_lines` 로."""
    out = TurnMaterial(next_n=next_n)
    cut = cut or {}
    sent_names = sent_names or {}
    local_gid = {x["n"]: x["gid"] for x in w.material.lines}
    line_of = {x["gid"]: x for x in w.material.lines}
    only = summary_only_gids(w.reads)
    new_n: dict[str, int] = {}
    thin: dict[str, str] = {}  # 이름만 gid → 까닭(branch · lineup — 머리 `marks.names_only` 값 · AR1)
    seen: list[int] = []
    # ① 칸째 싣는 행 — 묶음 순서 · 줄 순서대로 새 번호
    for g in w.material.groups:
        for r in g["rows"]:
            gid = local_gid.get(r["n"])
            if gid is None or gid in only or gid in new_n:
                continue
            fate = cut.get(gid, ("full", ""))[0]
            if fate == "drop":
                continue
            if gid in sent:
                seen.append(sent[gid])
            elif gid in sent_names:
                if fate == "names":
                    seen.append(sent_names[gid])
                else:  # G7 — 앞 턴 이름만 → 이번 턴 전체: 같은 번호로 상세를 다시 싣는다
                    new_n[gid] = sent_names[gid]
            else:
                new_n[gid] = out.next_n
                out.next_n += 1
                if fate == "names":
                    thin[gid] = cut[gid][1] or "branch"

    def conv_n(local: int) -> int | None:
        gid = local_gid.get(local)
        return new_n.get(gid) if gid in new_n else sent.get(gid)

    k = 0
    for g in w.material.groups:
        full_rows: list[dict] = []
        thin_rows: dict[str, list[dict]] = {}
        for r in g["rows"]:
            gid = local_gid.get(r["n"])
            if gid not in new_n:
                continue
            if gid in thin:
                body = fr.names_only({c: x for c, x in r.items() if c not in ("n", "marks")})
                thin_rows.setdefault(thin[gid], []).append({"n": new_n[gid], **body})
                continue
            r2 = copy.deepcopy(r)
            r2["n"] = new_n[gid]
            if r2.get("marks", {}).get("conflict_lines"):
                r2["marks"]["conflict_lines"] = [x for x in map(conv_n, r2["marks"]["conflict_lines"]) if x]
            full_rows.append(r2)
        if full_rows:
            k += 1
            g2 = {"group": k, "table": g["table"], "same": copy.deepcopy(g["same"]), "marks": copy.deepcopy(g["marks"]),
                  "lines": [r["n"] for r in full_rows], "rows": full_rows}
            if g2["marks"].get("conflict_lines"):
                g2["marks"]["conflict_lines"] = [x for x in map(conv_n, g2["marks"]["conflict_lines"]) if x]
            out.groups.append(g2)
        for why, rows in thin_rows.items():  # G4 · G5 — 이름만 묶음(머리도 이름 칸만 · 까닭별)
            k += 1
            out.groups.append({"group": k, "table": g["table"], "same": fr.names_only(g["same"]),
                               "marks": {"names_only": why}, "lines": [r["n"] for r in rows], "rows": rows})
    for gid, n in sorted(new_n.items(), key=lambda x: x[1]):
        ln = line_of[gid]
        out.new_lines.append({"n": n, "gid": gid, **{c: ln.get(c) for c in (
            "gold_table", "silver_table", "silver_id", "evidence_kind", "kind", "range", "layer")},
            **({"names_only": True, "names_why": thin[gid]} if gid in thin else {}),
            **({"fetch": cut[gid][1]} if gid in cut else {})})
    # ② 목록 요약 줄
    for blk in summaries(w.reads, only, v3):
        rows = []
        for ln in blk["lines"]:
            key = summary_key(blk["concern_code"], ln["modality_name"], ln["gids"])
            if key in summaries_seen:
                seen.append(summaries_seen[key])
                continue
            n = out.next_n
            out.next_n += 1
            rows.append({"n": n, "summary": True, **{c: ln[c] for c in (V3_SUMMARY if v3 else V1_SUMMARY)}})
            out.new_lines.append({"n": n, "summary": True, "key": key, "concern_code": blk["concern_code"],
                                  "modality_name": ln["modality_name"], "gids": ln["gids"]})
        if rows:
            k += 1
            same = {"concern_name": blk["concern_name"]}
            if blk["inherited_from"]:
                same["inherited_from"] = blk["inherited_from"]
            out.summary_groups.append({"group": k, "summary": True, "same": same, "marks": {},
                                       "lines": [r["n"] for r in rows], "rows": rows})
    out.seen_lines = sorted(set(seen))
    return out


def judgement_of(w, tm: TurnMaterial, turn: int, consult: dict | None = None) -> dict:
    """앱 판정. `consult`(0.2 켠 상태만) = {ask, synergy_lines} 를 더한다(끈 상태는 0.1 그대로)."""
    from clinic_chat.columns.col_b import judgement

    out = {**judgement(w), "seen_lines": tm.seen_lines, "turn": turn}
    if consult is not None:
        out["ask"] = consult.get("ask")
        out["synergy_lines"] = consult.get("synergy_lines") or []
    return out


V3_DROP = ("read_as", "unresolved")  # 0.3 S6-T9 — 켠 상태 「앱 판정」 에서 빼는 칸(`offered[].rows` 는 아래에서)


def judgement_v3(w, tm: TurnMaterial, turn: int, *, kind_labels: dict[str, str], extra: dict) -> dict:
    """0.3 켠 상태 「앱 판정」 — 0.1 판정에서 VO7 · EM4 의 칸을 빼고 `extra`(turn_kind · ask · synergy_lines · empty · site)를 더한다."""
    base = judgement_of(w, tm, turn)
    for k in V3_DROP:
        base.pop(k, None)
    f = w.front
    offered = [] if not f or w.end_state in _no_b_on() else f.offered
    base["offered"] = [{"kind": kind_labels.get(o.get("code"), "")} for o in offered]  # EM4 — 행 수 없음
    base.update(extra)
    return base


def _no_b_on():
    from clinic_chat.columns.col_b import NO_B_ON

    return NO_B_ON


def message_v3(prompt: ConsultPrompt, judgement: dict, close: dict, prev: dict | None, tm: TurnMaterial, question: str,
               consult: dict) -> str:
    """0.3 턴 메시지 — 앱 판정(JSON) · 그 안의 끝맺음 블록(· `talk` 턴은 앞 턴 블록) → 상담 상태 → 자료 → 질문."""
    h = prompt.turn_headers
    head = h["judgement"] + "\n" + json.dumps(judgement, ensure_ascii=False) + "\n"
    head += h["close"] + "\n" + json.dumps(close, ensure_ascii=False) + "\n"
    if prev is not None:
        head += h["prev"] + "\n" + json.dumps(prev, ensure_ascii=False) + "\n"
    head += "\n" + h["consult"] + "\n" + json.dumps(consult, ensure_ascii=False) + "\n\n"
    q = h["question"] + "\n" + question + "\n"
    if tm.empty:
        return head + h["material"] + "\n" + h["empty"] + "\n\n" + q
    return head + h["material"] + "\n" + to_lines(tm.groups + tm.summary_groups) + "\n\n" + q


def add_summaries(tm: TurnMaterial, rule: str, same: dict, rows: list[tuple[str, list[str], dict]],
                  summaries_seen: dict[str, int]) -> list[int]:
    """0.2 — 객체 조회의 요약 줄(QY3 · QY7 선택지 · SY4 시너지)을 0.1 과 같은 번호 줄기(`next_n`)에 붙인다(O5-8 고정).

    `rows` = [(키, gids, 줄 칸)] · 같은 키는 다시 싣지 않고 그 번호를 `seen_lines` 로(B-6 꼴). 장부 `new_lines` 에는
    `{n, summary, key, rule, gids}`. 반환 = 이번 턴 줄 번호(새 줄 + 다시 본 줄)."""
    lines, ns = [], []
    for key, gids, body in rows:
        if key in summaries_seen:
            tm.seen_lines = sorted({*tm.seen_lines, summaries_seen[key]})
            ns.append(summaries_seen[key])
            continue
        n = tm.next_n
        tm.next_n += 1
        lines.append({"n": n, "summary": True, **body})
        tm.new_lines.append({"n": n, "summary": True, "key": key, "rule": rule, "gids": gids})
        ns.append(n)
    if lines:
        k = len(tm.groups) + len(tm.summary_groups) + 1
        tm.summary_groups.append({"group": k, "summary": True, "same": same, "marks": {},
                                  "lines": [x["n"] for x in lines], "rows": lines})
    return ns


def consult_view(obj: dict) -> dict:
    """0.2 「상담 상태」 블록 — 이름 · 상태만(코드 · id 0 · SPEC-005 §6-3)."""
    f = obj.get("focus")
    return {"concerns": [{"name": x["name"], "state": x["state"], "focus": x["code"] == f} for x in obj["concerns"]],
            "subjects": [{"name": x["name"], "table": x["table"], "state": x["state"]} for x in obj["subjects"]],
            "sites": [{"name": x["name"], "state": x["state"]} for x in obj["sites"]],
            "wishes": [({"name": x["name"]} if x.get("table") else {"kind": x["kind"]}) | {"state": x["state"]}
                       for x in obj["wishes"]]}


def message(prompt: ConsultPrompt, judgement: dict, tm: TurnMaterial, question: str, consult: dict | None = None) -> str:
    """턴 메시지 — 앱 판정(JSON) → (0.2 상담 상태) → 자료 → 질문(질문이 맨 뒤 — `col_b.prompt_pages` 와 같은 순서)."""
    h = prompt.turn_headers
    head = h["judgement"] + "\n" + json.dumps(judgement, ensure_ascii=False) + "\n\n"
    if consult is not None:
        head += h["consult"] + "\n" + json.dumps(consult, ensure_ascii=False) + "\n\n"
    q = h["question"] + "\n" + question + "\n"
    if tm.empty:
        return head + h["material"] + "\n" + h["empty"] + "\n\n" + q
    body = to_lines(tm.groups + tm.summary_groups)
    return head + h["material"] + "\n" + body + "\n\n" + q


def too_large(prompt: ConsultPrompt, msg: str, max_tokens: int, system_text: str | None = None) -> bool:
    return est_tokens((system_text if system_text is not None else prompt.system_text) + msg) > max_tokens


def size_of(prompt: ConsultPrompt, msg: str, system_text: str | None = None) -> dict:
    """테스트 고침 E(11-23) — 답 입력 크기 기록(장부 `material_size`): 메시지 글자 · 바이트 · 시스템 포함 추정 토큰."""
    sys_t = system_text if system_text is not None else prompt.system_text
    return {"msg_chars": len(msg), "msg_bytes": len(msg.encode("utf-8")), "est_tokens": est_tokens(sys_t + msg)}


def _gsize(g: dict) -> int:
    return len(json.dumps(g.get("rows") or [], ensure_ascii=False).encode("utf-8"))


def shrink(tm: TurnMaterial, fits) -> dict:
    """테스트 고침 E(사용자 11-23 「큰 재료 답 실패」) — 입력 한도를 넘으면 재료를 줄여서라도 답한다(옛 FT5 「중단」 대체).
    가장 큰 묶음(바이트)의 행을 앞쪽 절반만 남기기를 `fits(tm)` 가 참일 때까지 되풀이한다(행 차례 = 허브 응답 차례 그대로 ·
    줄 번호는 남은 행 것 그대로). 행이 하나 남은 묶음은 통째로 뺀다. 뺀 줄 번호는 `new_lines` 에서도 지운다(장부 · 다음 턴 덧붙이기와
    맞게). → `{rounds, dropped_rows, dropped_groups, by_table{table: [전, 후]}}`."""
    rec: dict = {"rounds": 0, "dropped_rows": 0, "dropped_groups": 0, "by_table": {}}
    gone: set[int] = set()
    pools = [tm.groups, tm.summary_groups]
    while not fits(tm):
        cands = [(pool, g) for pool in pools for g in pool if g.get("rows")]
        if not cands:
            break
        pool, g = max(cands, key=lambda x: _gsize(x[1]))
        t = str(g.get("table") or g.get("group"))
        before = len(g["rows"])
        rec["by_table"].setdefault(t, [0, 0])
        rec["by_table"][t][0] = max(rec["by_table"][t][0], before)
        rec["rounds"] += 1
        if before <= 1:
            pool.remove(g)
            gone |= {r.get("n") for r in g["rows"]}
            rec["dropped_rows"] += before
            rec["dropped_groups"] += 1
            rec["by_table"][t][1] = 0
            continue
        keep = before // 2
        drop = g["rows"][keep:]
        g["rows"] = g["rows"][:keep]
        g["lines"] = [r.get("n") for r in g["rows"]]
        gone |= {r.get("n") for r in drop}
        rec["dropped_rows"] += len(drop)
        rec["by_table"][t][1] = keep
    if gone:
        tm.new_lines = [x for x in tm.new_lines if x.get("n") not in gone]
    return rec
