"""명동점 매핑 JSON v3(`$J`) 읽기 · 꼴 검사 · 색인(SPEC-010 §3 JS1 ~ JS6 · WP-1).

- JS1: 서버 시작 때 `CHAT_KOS_MAP` 을 한 번 읽는다. 다시 읽기는 재시작으로만(파일 감시 0).
- JS2: 꼴이 어긋나면 `KosMapError`(= 시작 실패 · 화면 0). 오류 글에는 코드 · 키만 — 가격 숫자 0(JS6).
- JS3: 파일 sha256 을 들고 장부 시작 줄에 `kos_map{sha, made_at, hub_gold_hash}`(+ JS4 `hash_mismatch`).
  `CHAT_KOS_MAP_SHA` 를 주면 다를 때 시작 실패(앞 8 자 이상이면 앞자리 비교).
- JS4: `meta.hub_gold_hash` ≠ 허브 `/health` 의 `build.gold_hash` 면 시작은 하고 표지만(`hash_mismatch`).
- JS5: 색인 여덟(메모리). 진료항목 코드 목록은 늘 `groups` 순서(KOS 시술 묶음 순 → 묶음 안 순)다.
- v3 부터 가격의 정본은 `products[]` 다(D-20) — `items[].options`(v2 칸)는 읽지 않는다.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

VERSION = "v3"
N_ITEMS = 324  # JS2 — 명동점 스냅샷 진료항목 수(v3 실측)
FACE_SHAPES = ("point", "pair")
REGIONS = ("face", "body")
OPTION_TYPES = ("single", "group")  # OP8 — 그 밖 0
SHA_PREFIX_MIN = 8

OptKey = tuple[str, str]  # (product_key, option_key) — v3 안정 키(RC4)


class KosMapError(RuntimeError):
    """`$J` 를 쓸 수 없다 — 서버 시작 실패."""


@dataclass(frozen=True)
class KosMap:
    path: str
    sha256: str
    meta: dict
    items: tuple[dict, ...]
    groups: tuple[dict, ...]
    sites: dict[str, dict]
    products: tuple[dict, ...]
    product_categories: tuple[dict, ...]
    # JS5 색인 여덟
    item: dict[str, dict] = field(default_factory=dict)  # 1
    by_case: dict[str, tuple[str, ...]] = field(default_factory=dict)  # 2
    by_proc: dict[str, tuple[str, ...]] = field(default_factory=dict)  # 3
    by_product: dict[str, tuple[str, ...]] = field(default_factory=dict)  # 4
    by_site: dict[str, tuple[str, ...]] = field(default_factory=dict)  # 5
    by_kos_procedure: dict[str, tuple[str, ...]] = field(default_factory=dict)  # 6(묶음 순서 = groups)
    site_children: dict[str, tuple[str, ...]] = field(default_factory=dict)  # 7
    option: dict[OptKey, dict] = field(default_factory=dict)  # 8
    option_product: dict[OptKey, dict] = field(default_factory=dict)  # 8 — 옵션의 상품
    covers_of: dict[str, tuple[OptKey, ...]] = field(default_factory=dict)  # 8 — `items[].option_refs` 순서
    order: dict[str, int] = field(default_factory=dict)  # 진료항목 → groups 안 자리

    @property
    def hub_gold_hash(self) -> str:
        return str(self.meta.get("hub_gold_hash") or "")

    def stamp(self) -> dict:
        """JS3 — 장부 칸 `kos_map`(가격 · KOS id 0)."""
        return {"sha": self.sha256, "made_at": self.meta.get("made_at"), "hub_gold_hash": self.hub_gold_hash}

    def descendants(self, site: str) -> list[str]:
        """자기 + 자손 부위(`sites[].parent_code` · 너비 우선 · 같은 코드 한 번)."""
        if site not in self.sites:
            return []
        out, i = [site], 0
        while i < len(out):
            for c in self.site_children.get(out[i], ()):
                if c not in out:
                    out.append(c)
            i += 1
        return out

    def ancestors(self, site: str) -> list[str]:
        """부모 → 뿌리 순(자기 빼고)."""
        out: list[str] = []
        cur = (self.sites.get(site) or {}).get("parent_code") or ""
        while cur and cur in self.sites and cur not in out:
            out.append(cur)
            cur = self.sites[cur].get("parent_code") or ""
        return out

    def sort_codes(self, codes) -> list[str]:
        """진료항목 코드 → groups 순서(같은 코드 한 번)."""
        return sorted(set(codes), key=lambda c: self.order.get(c, len(self.order)))


def file_sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _need(cond: bool, what: str, bad: list | None = None) -> None:
    if not cond:
        tail = f" — {', '.join(map(str, bad[:5]))}{' …' if len(bad) > 5 else ''}" if bad else ""
        raise KosMapError(f"kos_map: {what}{tail}")


def check(d: Any, *, expect_items: int | None = N_ITEMS) -> None:
    """JS2 꼴 검사 — 하나라도 어긋나면 `KosMapError`."""
    _need(isinstance(d, dict), "top level is not an object")
    for k in ("meta", "items", "groups", "sites", "products", "product_categories"):
        _need(k in d, f"missing key: {k}")
    meta, items, groups, sites, products = d["meta"], d["items"], d["groups"], d["sites"], d["products"]
    _need(isinstance(meta, dict) and meta.get("version") == VERSION, f"meta.version != {VERSION}")
    _need(
        isinstance(items, list)
        and isinstance(groups, list)
        and isinstance(products, list)
        and isinstance(d["product_categories"], list)
        and isinstance(sites, dict),
        "key types (list / object)",
    )
    if expect_items:
        _need(len(items) == expect_items, f"items count {len(items)} != {expect_items}")
    codes = [i.get("kos_item_code") for i in items]
    dup = sorted({c for c in codes if codes.count(c) > 1}, key=str)
    _need(all(isinstance(c, str) and c for c in codes) and not dup, "kos_item_code empty or duplicated", dup)
    code_set = set(codes)
    # 상품 · 옵션 — 키 유일 · 종류 · 덮개
    pkeys = [p.get("product_key") for p in products]
    _need(
        all(pkeys) and len(set(pkeys)) == len(pkeys),
        "product_key empty or duplicated",
        sorted({k for k in pkeys if pkeys.count(k) > 1}, key=str),
    )
    okeys: list[OptKey] = []
    bad_type, bad_cover = [], []
    for p in products:
        _need(isinstance(p.get("options"), list), f"products[{p.get('product_key')}].options missing")
        for o in p["options"]:
            k = (p["product_key"], o.get("option_key"))
            okeys.append(k)
            if o.get("type") not in OPTION_TYPES:
                bad_type.append(k)
            bad_cover += [(k, c) for c in o.get("covers") or [] if c not in code_set]
    _need(
        all(k[1] for k in okeys) and len(set(okeys)) == len(okeys),
        "option_key empty or duplicated",
        sorted({k for k in okeys if okeys.count(k) > 1}),
    )
    _need(not bad_type, "option type not single / group", bad_type)
    _need(not bad_cover, "options[].covers not in items", bad_cover)
    okey_set = set(okeys)
    refs = {
        (i["kos_item_code"], r.get("product_key"), r.get("option_key"))
        for i in items
        for r in i.get("option_refs") or []
    }
    bad_ref = sorted(r for r in refs if (r[1], r[2]) not in okey_set)
    _need(not bad_ref, "items[].option_refs not in products[].options", bad_ref)
    covers = {
        (c, p["product_key"], o["option_key"]) for p in products for o in p["options"] for c in o.get("covers") or []
    }
    _need(refs == covers, "option_refs and covers differ", sorted(refs ^ covers))
    # 부위
    bad_site = sorted({s for i in items for s in (i.get("hub") or {}).get("site_codes") or [] if s not in sites})
    _need(not bad_site, "hub.site_codes not in sites", bad_site)
    bad_face = sorted(
        k
        for k, v in sites.items()
        if v.get("region") not in REGIONS
        or (v.get("region") == "face" and (v.get("shape") not in FACE_SHAPES or not v.get("points")))
    )
    _need(not bad_face, "site shape (face = point / pair with points, else body)", bad_face)
    bad_parent = sorted(k for k, v in sites.items() if v.get("parent_code") and v["parent_code"] not in sites)
    _need(not bad_parent, "sites[].parent_code not in sites", bad_parent)
    # 묶음 — 진료항목을 한 번씩
    gcodes = [c for g in groups for c in g.get("items") or []]
    _need(
        sorted(gcodes) == sorted(code_set) and len(gcodes) == len(code_set),
        "groups do not cover items exactly once",
        sorted(set(gcodes) ^ code_set),
    )
    for i in items:
        _need(isinstance(i.get("hub"), dict), f"items[{i['kos_item_code']}].hub missing")


def build(d: dict, *, path: str = "", sha: str = "") -> KosMap:
    """JS5 — 검사를 지난 `$J` 에서 색인 여덟."""
    items, groups = d["items"], d["groups"]
    order = {c: n for n, c in enumerate(c for g in groups for c in g["items"])}
    item = {i["kos_item_code"]: i for i in items}

    def idx(get: Callable[[dict], list[str]]) -> dict[str, tuple[str, ...]]:
        m: dict[str, list[str]] = {}
        for i in sorted(items, key=lambda x: order[x["kos_item_code"]]):
            for k in dict.fromkeys(get(i)):
                m.setdefault(k, []).append(i["kos_item_code"])
        return {k: tuple(v) for k, v in m.items()}

    children: dict[str, list[str]] = {}
    for k, v in d["sites"].items():
        if v.get("parent_code"):
            children.setdefault(v["parent_code"], []).append(k)
    option, option_product = {}, {}
    for p in d["products"]:
        for o in p["options"]:
            option[(p["product_key"], o["option_key"])] = o
            option_product[(p["product_key"], o["option_key"])] = p
    by_kos: dict[str, list[str]] = {}
    for g in groups:
        by_kos.setdefault(g["kos_procedure"], []).extend(g["items"])
    return KosMap(
        path=path,
        sha256=sha,
        meta=d["meta"],
        items=tuple(items),
        groups=tuple(groups),
        sites=d["sites"],
        products=tuple(d["products"]),
        product_categories=tuple(d["product_categories"]),
        item=item,
        by_case=idx(lambda i: i["hub"].get("case_codes") or []),
        by_proc=idx(lambda i: i["hub"].get("procedure_codes") or []),
        by_product=idx(lambda i: [p["code"] for p in i["hub"].get("products") or [] if p.get("code")]),
        by_site=idx(lambda i: i["hub"].get("site_codes") or []),
        by_kos_procedure={k: tuple(v) for k, v in by_kos.items()},
        site_children={k: tuple(v) for k, v in children.items()},
        option=option,
        option_product=option_product,
        covers_of={
            i["kos_item_code"]: tuple((r["product_key"], r["option_key"]) for r in i.get("option_refs") or [])
            for i in items
        },
        order=order,
    )


def sha_matches(sha: str, want: str | None) -> bool:
    if not want:
        return True
    w = want.strip().lower()
    return sha == w or (len(w) >= SHA_PREFIX_MIN and sha.startswith(w))


def parse(raw: bytes, *, path: str = "", expect_sha: str | None = None, expect_items: int | None = N_ITEMS) -> KosMap:
    sha = file_sha(raw)
    _need(sha_matches(sha, expect_sha), f"sha256 {sha[:16]} != CHAT_KOS_MAP_SHA")
    try:
        d = json.loads(raw)
    except ValueError as e:
        raise KosMapError(f"kos_map: not JSON ({type(e).__name__})") from e
    check(d, expect_items=expect_items)
    return build(d, path=path, sha=sha)


def load(path: Path | str | None, *, expect_sha: str | None = None, expect_items: int | None = N_ITEMS) -> KosMap:
    """JS1 — 비면 · 없으면 · 꼴이 틀리면 `KosMapError`."""
    _need(bool(path), "CHAT_KOS_MAP is empty")
    p = Path(str(path))
    _need(p.is_file(), f"file not found: {p.name}")
    return parse(p.read_bytes(), path=str(p), expect_sha=expect_sha, expect_items=expect_items)


def from_settings(s) -> KosMap:
    return load(s.path(s.chat_kos_map), expect_sha=s.chat_kos_map_sha, expect_items=s.chat_kos_map_items or None)


def hash_check(kos: KosMap, health: Callable[[], dict] | None) -> dict:
    """JS4 — 허브 `/health` 의 `build.gold_hash` 와 대조. 허브를 못 부르면 `hash_mismatch: None` + `hub_error`."""
    out = {**kos.stamp(), "hub_build_gold_hash": None, "hash_mismatch": None}
    if health is None:
        return out
    try:
        gh = ((health() or {}).get("build") or {}).get("gold_hash")
    except Exception as e:  # noqa: BLE001 — 시작은 한다(JS4)
        return {**out, "hub_error": type(e).__name__}
    if not gh:
        return {**out, "hub_error": "no_gold_hash"}
    return {**out, "hub_build_gold_hash": gh, "hash_mismatch": gh != kos.hub_gold_hash}
