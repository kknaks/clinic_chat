"""A 열 원장 도구 3 — 앱 로컬 구현(SPEC-002 §2-3 · 옛 틀 `quality/raw_tools.py` 꼴만).

- 읽는 파일 = `RAW_DIR` 의 `*.json` **전부**(이름 목록을 코드에 박지 않는다 — WORK-002 §4 · O-11). 0 이면 시작 실패.
- gold · silver · `g_entry` 에 기대지 않는다(기준선). 검색 정규화 = 공백 지우기 · 소문자뿐.
- 경로 문법이 「파일 이름으로 시작」 이라 폴더 밖을 못 가리킨다. 시작 때 읽은 파일만 메모리에 둔다.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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


_TOKEN = re.compile(r'\.([^.\[\]"]+)|\[(\d+)\]|\["((?:[^"\\]|\\.)*)"\]')


def parse_path(path: str) -> tuple[str, list[str | int]]:
    m = re.match(r'^([^.\[\]"]+)', path or "")
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
            segs.append(json.loads('"' + t.group(3) + '"'))
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
        d = Path(raw_dir) if raw_dir else None
        if d is None or not d.is_dir():
            raise RawToolError(f"RAW_DIR 없음: {raw_dir}")
        self.files: dict[str, RawFile] = {}
        for p in sorted(d.glob("*.json")):
            if not p.is_file():
                continue
            b = p.read_bytes()
            try:
                doc = json.loads(b)
            except ValueError as e:
                raise RawToolError(f"원장 파일을 읽지 못함: {p.name}") from e
            self.files[p.stem] = RawFile(p.stem, len(b), hashlib.sha256(b).hexdigest()[:16], doc)
        if not self.files:
            raise RawToolError(f"RAW_DIR 에 JSON 없음: {raw_dir}")
        # manifest = files[{name, bytes, sha16}] 의 canonical JSON sha256 앞 16자(옛 틀과 같은 셈)
        manifest = [{"name": f"{f.name}.json", "bytes": f.bytes, "sha16": f.sha16} for f in self.files.values()]
        canon = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        self.dir_manifest_sha16 = hashlib.sha256(canon.encode("utf-8")).hexdigest()[:16]

    # 응답 머리
    def head(self, request: dict) -> dict:
        return {"raw": {"dir_manifest_sha16": self.dir_manifest_sha16, "files": len(self.files)}, "request": request}

    def resolve(self, path: str) -> Any:
        file, segs = parse_path(path)
        if file not in self.files:
            raise RawToolError(f"없는 경로: {path} — 파일 {file} 없음")
        v = self.files[file].doc
        cur = file
        for s in segs:
            cur += f"[{s}]" if isinstance(s, int) else key_seg(s)
            if isinstance(s, int) and isinstance(v, list) and 0 <= s < len(v) or isinstance(s, str) and isinstance(v, dict) and s in v:
                v = v[s]
            else:
                raise RawToolError(f"없는 경로: {path} — {cur}")
        return v

    # R1
    def raw_list(self, file: str | None = None, depth: int = 2) -> dict:
        if not isinstance(depth, int) or not 0 <= depth <= LIST_DEPTH_MAX:
            raise RawToolError(f"depth 는 0~{LIST_DEPTH_MAX}")
        if file is not None and file not in self.files:
            raise RawToolError(f"없는 파일: {file} — {', '.join(sorted(self.files))}")
        names = [file] if file else sorted(self.files)

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
            items = list(v.items()) if isinstance(v, dict) else list(v)
            if offset > len(items):
                raise RawToolError(f"offset 이 원소 수({len(items)})보다 큼")
            taken, used, i = [], 2, offset
            while i < len(items):
                b = _bytes(dict([items[i]]) if isinstance(v, dict) else items[i]) + 1
                if taken and used + b > cap:
                    break
                taken.append(items[i])
                used += b
                i += 1
            out["value"] = dict(taken) if isinstance(v, dict) else taken
            out["truncated"] = i < len(items)
            out.update({"offset": offset, "count": len(taken), "length": len(items)})
            if out["truncated"]:
                out["next_offset"] = i
        elif isinstance(v, str) and total > cap:
            out["value"] = v.encode("utf-8")[: max(0, cap - 2)].decode("utf-8", errors="ignore")
            out["truncated"] = True
        else:
            out["value"] = v
        return {**self.head({"path": path, "offset": offset, "max_bytes": max_bytes}), **out}

    # R3
    def raw_search(self, query: str, files: list[str] | None = None, limit: int = 20, snippet_chars: int = 80) -> dict:
        if not isinstance(query, str) or not query or len(query) > 100:
            raise RawToolError("query 는 1~100자")
        if not 1 <= limit <= SEARCH_LIMIT_MAX:
            raise RawToolError(f"limit 은 1~{SEARCH_LIMIT_MAX}")
        if not 1 <= snippet_chars <= SNIPPET_MAX:
            raise RawToolError(f"snippet_chars 는 1~{SNIPPET_MAX}")
        qn, _ = _norm_search(query)
        if not qn:
            raise RawToolError("query 가 공백뿐")
        for f in files or []:
            if f not in self.files:
                raise RawToolError(f"없는 파일: {f}")
        names = sorted(files or self.files)
        hits: list[dict] = []
        total = 0

        def snippet(s: str) -> str | None:
            n, idx = _norm_search(s)
            j = n.find(qn)
            if j < 0:
                return None
            a, b = idx[j], idx[j + len(qn) - 1] + 1
            pad = max(0, (snippet_chars - (b - a)) // 2)
            return s[max(0, a - pad): min(len(s), b + pad)]

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
        return {**self.head(req), "query_norm": qn, "total": total, "truncated": total > limit, "hits": hits}

    def call(self, name: str, args: dict) -> dict:
        fn = {"raw_list": self.raw_list, "raw_get": self.raw_get, "raw_search": self.raw_search}.get(name)
        if fn is None:
            raise RawToolError(f"없는 도구: {name}")
        return fn(**args)


def paths_of(tool: str, request: dict, response: dict | None) -> list[str]:
    """A trace `paths[]`(SPEC-002 §2-3 · §7-3) — raw_get = 입력 path · raw_search = hits[].path · raw_list = 빈 목록."""
    if tool == "raw_get":
        p = (request or {}).get("path")
        return [p] if isinstance(p, str) else []
    if tool == "raw_search":
        return [h.get("path") for h in (response or {}).get("hits") or [] if isinstance(h, dict) and h.get("path")]
    return []
