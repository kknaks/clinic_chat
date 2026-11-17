"""gold 코드 집합 · 이름 사전(SPEC-002 §8-2 「gold 코드 집합」 · O-7 (a)) — **채점 단계에서만** 로컬 gold CSV 를 읽는다.

(i) `g_entry` (entity_table, entity_code) (ii) `g_scope` subject · holder (iii) 속성 표의 키 코드 칸 · 곁 칸의 합집합.
허브 `health.files[]` 의 gold 파일 sha256 과 로컬 파일을 대조 — 다르면 채점 중단.
"""

from __future__ import annotations

import csv
import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

from clinic_chat.walk.keycodes import KEY_CODE, _codes

CODE_RE = re.compile(r"\b(?!G[A-Z]{2}-)[A-Z]{1,4}-\d{2,5}\b|\bM\d{1,2}\b")


def norm(s: str) -> str:
    """gold-build `norm` 과 같은 식 — 공백 지움 + 소문자(SPEC-001 §5-1 R1)."""
    return re.sub(r"\s+", "", s or "").lower()


class BuildMismatch(RuntimeError):
    pass


@dataclass
class GoldCodes:
    codes: set[str] = field(default_factory=set)
    gids: set[str] = field(default_factory=set)
    entry_codes: set[str] = field(default_factory=set)
    names: dict[str, set[str]] = field(default_factory=dict)  # norm(text) → {표:코드}
    files: dict[str, str] = field(default_factory=dict)  # 파일 이름 → sha256
    # D11 재료 — 시간 칸이 있는 효과 · 부작용 · 회복 행의 (표, 대상 `표:코드`, 시간 꼴들). 값 칸은 기록에 복사하지 않는다
    times: list[tuple[str, str, frozenset]] = field(default_factory=list)

    def find_names(self, text: str) -> set[str]:
        """답 글을 norm 으로 훑어 사전 이름을 찾는다(긴 것 먼저 · 겹치면 전부)."""
        t = norm(text)
        out: set[str] = set()
        for n in sorted(self.names, key=len, reverse=True):
            if len(n) >= 2 and n in t:
                out |= self.names[n]
        return out


def _read(p: Path) -> list[dict]:
    with p.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def load(gold_dir: str | Path) -> GoldCodes:
    d = Path(gold_dir)
    g = GoldCodes()
    for p in sorted(d.glob("g_*.csv")):
        g.files[p.name] = hashlib.sha256(p.read_bytes()).hexdigest()
        t = p.stem
        rows = _read(p)
        for r in rows:
            if r.get("gid"):
                g.gids.add(r["gid"])
            if t == "g_entry":
                c = f"{r['entity_table']}:{r['entity_code']}"
                g.codes.add(c)
                g.entry_codes.add(c)
                g.names.setdefault(norm(r.get("text") or ""), set()).add(c)
            elif t == "g_scope":
                g.codes.add(f"{r['subject_table']}:{r['subject_code']}")
                g.codes.add(f"{r['holder_table']}:{r['holder_code']}")
            elif t in KEY_CODE:
                kc, sc = KEY_CODE[t]
                g.codes.update(_codes(r, kc) + _codes(r, sc))
            if t in TIME_TABLES and r.get("target_table") and r.get("target_code"):
                sp = row_spans(r, t)
                if sp:
                    g.times.append((t, f"{r['target_table']}:{r['target_code']}", frozenset(sp)))
    g.names.pop("", None)
    return g


TIME_TABLES = ("g_outcome", "g_adverse", "g_recovery")
UNIT = {"분": "분", "시간": "시간", "일": "일", "주": "주", "개월": "개월", "달": "개월", "년": "년"}
SPAN_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:(분|시간|일|주|개월|달|년)\s*)?(?:[~\-–]\s*(\d+(?:\.\d+)?)\s*)?(분|시간|일|주|개월|달|년)")


def _n(x: str) -> str:
    return str(float(x)).rstrip("0").rstrip(".")


def text_spans(text: str) -> set[tuple[str, str, str]]:
    """글의 시간 꼴 → {(최소, 최대, 단위)} — 「3~6개월」 · 「7-14일」 · 「24시간」 · 「2주~1개월」(단위가 다르면 뒤 단위)."""
    out = set()
    for a, u1, b, u2 in SPAN_RE.findall(text or ""):
        if b and u1 and u1 != u2:
            out.add((_n(a), _n(a), UNIT[u1]))
            out.add((_n(b), _n(b), UNIT[u2]))
            continue
        out.add((_n(a), _n(b or a), UNIT[u2]))
    return out


def row_spans(r: dict, table: str) -> set[tuple[str, str, str]]:
    """한 행의 시간 꼴 — 글 칸 + (min, max, unit) 칸. 회복은 `text` · `min` · `max` · `unit`."""
    out: set[tuple[str, str, str]] = set()
    pre = ("",) if table == "g_recovery" else ("onset_", "duration_")
    for p in pre:
        out |= text_spans(r.get(f"{p}text") or "")
        lo, hi, u = r.get(f"{p}min") or "", r.get(f"{p}max") or "", r.get(f"{p}unit") or ""
        if (lo or hi) and u in UNIT:
            try:
                out.add((_n(lo or hi), _n(hi or lo), UNIT[u]))
            except ValueError:
                pass
    return out


def check_build(g: GoldCodes, health: dict) -> None:
    hub = {f["name"]: f.get("sha256") for f in health.get("files") or [] if f.get("kind") == "gold"}
    for name, sha in hub.items():
        local = g.files.get(Path(name).name)
        if local is not None and local != sha:
            raise BuildMismatch(f"gold 파일 다름: {name}")
    if hub and not any(Path(n).name in g.files for n in hub):
        raise BuildMismatch("허브 gold 파일과 로컬 gold 폴더가 맞지 않음")


def bare_code(c: str) -> str:
    return c.split(":", 1)[-1]
