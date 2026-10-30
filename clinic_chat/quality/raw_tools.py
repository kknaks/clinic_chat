"""채점용 원장 도구 3 — 앱 안 로컬 구현(SPEC-006 §5-2 · R-10).

SPEC-005 §5-12 R1~R3(`raw_list` · `raw_get` · `raw_search`)과 같은 이름 · 입력 · 출력 계약. `RAW_DIR` 로컬 원장 파일을
읽고 허브 원장 MCP 를 부르지 않는다. 골드 · 시맨틱 · 정규화 규칙에 기대지 않는다(검색 정규화 = 공백 지우기 · 소문자만).
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

RAW_FILES = (
    "anatomy-danger-zones", "combo-recipes", "complications", "consent-checklist", "contraindications",
    "extracted", "literature-evidence", "market-pricing", "patient-education", "pih-risk",
    "post-procedure-timeline", "pre-procedure-checklist", "problem-combos", "problem-packages",
    "procedure-comparison", "procedure-profiles", "products", "synergy", "taxonomy",
    "treatment-optimization", "treatment-sequence",
)
RAW_GET_MAX_BYTES = 20_000
LIST_DEPTH_MAX = 3
SEARCH_LIMIT_MAX = 100
SNIPPET_MAX = 200


class RawToolError(ValueError):
    """도메인 오류(4xx · MCP 는 같은 문구의 tool 오류)."""


def _bytes(v: Any) -> int:
    return len(json.dumps(v, ensure_ascii=False).encode("utf-8"))


def _kind(v: Any) -> str:
    if isinstance(v, dict):
        return "object"
    if isinstance(v, list):
        return "array"
    if isinstance(v, str):
        return "string"
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, (int, float)):
        return "number"
    return "null"


_PLAIN_KEY = re.compile(r"^[^.\[\]\s\"]+$")


def key_seg(k: str) -> str:
    return "." + k if _PLAIN_KEY.match(k) else "[" + json.dumps(k, ensure_ascii=False) + "]"


_TOKEN = re.compile(r'\.([^.\[\]]+)|\[(\d+)\]|\[("(?:[^"\\]|\\.)*")\]')


def parse_path(path: str) -> tuple[str, list[str | int]]:
    m = re.match(r"^([^.\[\]]+)", path or "")
    if not m:
        raise RawToolError(f"경로 문법 오류: {path!r}")
    file, pos, segs = m.group(1), m.end(), []
    while pos < len(path):
        t = _TOKEN.match(path, pos)
        if not t:
            raise RawToolError(f"경로 문법 오류: {path!r}")
        if t.group(1) is not None:
            segs.append(t.group(1))
        elif t.group(2) is not None:
            segs.append(int(t.group(2)))
        else:
            segs.append(json.loads(t.group(3)))
        pos = t.end()
    return file, segs


def _norm_search(s: str) -> tuple[str, list[int]]:
    """공백 지우기 · 소문자 — 정규화 글자마다 원문 위치."""
    out, idx = [], []
    for i, ch in enumerate(s):
        if ch.isspace():
            continue
        for c in ch.lower():
            out.append(c)
            idx.append(i)
    return "".join(out), idx


@dataclass
class RawFile:
    name: str
    bytes: int
    sha16: str
    doc: Any


class RawStore:
    def __init__(self, raw_dir: str | Path) -> None:
        d = Path(raw_dir)
        if not d.is_dir():
            raise RawToolError(f"RAW_DIR 없음: {raw_dir}")
        self.files: dict[str, RawFile] = {}
        for n in RAW_FILES:
            p = d / f"{n}.json"
            if not p.is_file():
                raise RawToolError(f"원장 파일 없음: {n}.json")
            b = p.read_bytes()
            self.files[n] = RawFile(n, len(b), hashlib.sha256(b).hexdigest()[:16], json.loads(b))
        lines = "".join(f"{f.name}.json\t{f.bytes}\t{f.sha16}\n" for f in self.files.values())
        self.dir_manifest_sha16 = hashlib.sha256(lines.encode()).hexdigest()[:16]

    # 응답 머리
    def head(self, request: dict) -> dict:
        return {"raw": {"dir_manifest_sha16": self.dir_manifest_sha16, "files": len(self.files)}, "request": request}

    def _file(self, name: str) -> RawFile:
        if name not in self.files:
            raise RawToolError(f"없는 원장 파일: {name}")
        return self.files[name]

    def resolve(self, path: str) -> Any:
        file, segs = parse_path(path)
        v = self._file(file).doc
        for s in segs:
            if isinstance(s, int) and isinstance(v, list) and 0 <= s < len(v):
                v = v[s]
            elif isinstance(s, str) and isinstance(v, dict) and s in v:
                v = v[s]
            else:
                raise RawToolError(f"없는 경로: {path}")
        return v

    # R1
    def raw_list(self, file: str | None = None, depth: int = 2) -> dict:
        if not 0 <= depth <= LIST_DEPTH_MAX:
            raise RawToolError(f"depth 는 0~{LIST_DEPTH_MAX}")
        names = [self._file(file).name] if file else list(self.files)

        def struct(v: Any, d: int) -> dict:
            k = _kind(v)
            node: dict[str, Any] = {"type": k}
            if k == "array":
                node["length"] = len(v)
                if d > 0 and v:
                    node["item"] = struct(v[0], d - 1)
            elif k == "object":
                node["keys"] = len(v)
                if d > 0:
                    node["children"] = {kk: struct(vv, d - 1) for kk, vv in v.items()}
            return node

        files = [{"name": n, "bytes": self.files[n].bytes, "sha16": self.files[n].sha16,
                  "structure": struct(self.files[n].doc, depth)} for n in names]
        return {**self.head({"file": file, "depth": depth}), "files": files}

    # R2
    def raw_get(self, path: str, offset: int = 0, max_bytes: int | None = None) -> dict:
        if offset < 0:
            raise RawToolError("offset 은 0 이상")
        cap = RAW_GET_MAX_BYTES if max_bytes is None else max_bytes
        if not 1 <= cap <= RAW_GET_MAX_BYTES:
            raise RawToolError(f"max_bytes 는 1~{RAW_GET_MAX_BYTES}")
        v = self.resolve(path)
        total = _bytes(v)
        out: dict[str, Any] = {"path": path, "type": _kind(v), "bytes_total": total, "truncated": False}
        if isinstance(v, (list, dict)):
            items = list(enumerate(v)) if isinstance(v, list) else list(v.items())
            items = items[offset:]
            taken, used = [], 2
            for k, x in items:
                b = _bytes(x) + (_bytes(k) + 1 if isinstance(v, dict) else 0) + 1
                if taken and used + b > cap:
                    break
                taken.append((k, x))
                used += b
            val = [x for _, x in taken] if isinstance(v, list) else {k: x for k, x in taken}
            out["value"] = val
            if len(taken) < len(items):
                out["truncated"] = True
                out["next_offset"] = offset + len(taken)
        elif isinstance(v, str) and total > cap:
            s, used = [], 2
            for ch in v:
                b = _bytes(ch) - 2
                if used + b > cap:
                    break
                s.append(ch)
                used += b
            out["value"] = "".join(s)
            out["truncated"] = True
        else:
            out["value"] = v
        return {**self.head({"path": path, "offset": offset, "max_bytes": max_bytes}), **out}

    # R3
    def raw_search(self, query: str, files: list[str] | None = None, limit: int = 20, snippet_chars: int = 80) -> dict:
        if not query or not 1 <= len(query) <= 100:
            raise RawToolError("query 는 1~100자")
        if not 1 <= limit <= SEARCH_LIMIT_MAX:
            raise RawToolError(f"limit 은 1~{SEARCH_LIMIT_MAX}")
        if not 1 <= snippet_chars <= SNIPPET_MAX:
            raise RawToolError(f"snippet_chars 는 1~{SNIPPET_MAX}")
        qn, _ = _norm_search(query)
        if not qn:
            raise RawToolError("query 가 공백뿐")
        names = sorted(self._file(f).name for f in files) if files else sorted(self.files)
        hits: list[dict] = []
        total = 0

        def snippet(s: str) -> str | None:
            n, idx = _norm_search(s)
            j = n.find(qn)
            if j < 0:
                return None
            a, b = idx[j], idx[j + len(qn) - 1] + 1
            pad = max(0, (snippet_chars - (b - a)) // 2)
            return s[max(0, a - pad): b + pad][:snippet_chars] if b - a <= snippet_chars else s[a:a + snippet_chars]

        def walk(v: Any, path: str, file: str) -> None:
            nonlocal total
            if isinstance(v, dict):
                for k, x in v.items():
                    p = path + key_seg(k)
                    sn = snippet(k)
                    if sn is not None:
                        total += 1
                        if len(hits) < limit:
                            hits.append({"file": file, "path": p, "in": "key", "snippet": sn})
                    walk(x, p, file)
            elif isinstance(v, list):
                for i, x in enumerate(v):
                    walk(x, f"{path}[{i}]", file)
            elif isinstance(v, str):
                sn = snippet(v)
                if sn is not None:
                    total += 1
                    if len(hits) < limit:
                        hits.append({"file": file, "path": path, "in": "value", "snippet": sn})

        for n in names:
            walk(self.files[n].doc, n, n)
        req = {"query": query, "files": files, "limit": limit, "snippet_chars": snippet_chars}
        return {**self.head(req), "query_norm": qn, "total": total, "truncated": total > len(hits), "hits": hits}

    def call(self, name: str, args: dict) -> dict:
        fn = {"raw_list": self.raw_list, "raw_get": self.raw_get, "raw_search": self.raw_search}.get(name)
        if fn is None:
            raise RawToolError(f"없는 도구: {name}")
        return fn(**args)

    def iter_strings(self):
        """① 단어 검사용 — (파일, 경로, 문자열 값) 전부."""
        def walk(v: Any, path: str, file: str):
            if isinstance(v, dict):
                for k, x in v.items():
                    yield from walk(x, path + key_seg(k), file)
            elif isinstance(v, list):
                for i, x in enumerate(v):
                    yield from walk(x, f"{path}[{i}]", file)
            elif isinstance(v, str):
                yield file, path, v

        for n, f in self.files.items():
            yield from walk(f.doc, n, n)


RAW_TOOL_DEFS = [
    {
        "name": "raw_list",
        "description": "원장 파일 목록과 키 구조 트리(값 본문 없음). file 없으면 21 파일 전부.",
        "input_schema": {"type": "object", "properties": {
            "file": {"type": "string", "description": "파일 이름(.json 뺌)"},
            "depth": {"type": "integer", "default": 2, "minimum": 0, "maximum": LIST_DEPTH_MAX}}},
    },
    {
        "name": "raw_get",
        "description": "경로의 원문 값. 경로 문법: 파일 이름 뒤 .키 · [번호] · [\"키\"]. 크면 잘리고 next_offset.",
        "input_schema": {"type": "object", "required": ["path"], "properties": {
            "path": {"type": "string"},
            "offset": {"type": "integer", "default": 0, "minimum": 0},
            "max_bytes": {"type": "integer", "minimum": 1, "maximum": RAW_GET_MAX_BYTES}}},
    },
    {
        "name": "raw_search",
        "description": "원장 문자열 값 · 키에서 부분 일치 검색(공백 · 대소문자만 무시).",
        "input_schema": {"type": "object", "required": ["query"], "properties": {
            "query": {"type": "string", "minLength": 1, "maxLength": 100},
            "files": {"type": "array", "items": {"type": "string"}},
            "limit": {"type": "integer", "default": 20, "minimum": 1, "maximum": SEARCH_LIMIT_MAX},
            "snippet_chars": {"type": "integer", "default": 80, "minimum": 1, "maximum": SNIPPET_MAX}}},
    },
]
