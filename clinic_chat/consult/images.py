"""원리 · 방법 그림 서빙(SPEC-010 §8 IM1 ~ IM13 · 결정 D-11 · D-19 — 허브가 아니라 앱이 시드 폴더에서).

- 자리 = `CHAT_IMAGE_DIR`(시드 `images/`) + `<표>/<코드>.svg` · 표 = mechanism · method 둘만 · 코드 꼴 `^MEC-\\d{3}$` ·
  `^MET-\\d{3}$`(표와 접두어가 맞아야) · 그 밖 · `..` · 다른 확장자 · 폴더 밖으로 가는 심볼릭 링크 = 없음(404).
- 있음 판단은 요청마다 파일을 본다(캐시 0 · IM3) · ETag = 파일 sha256 앞 16 자 · `Cache-Control: no-cache`.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

PREFIX = {"mechanism": "MEC", "method": "MET"}
NAME_RE = {t: re.compile(rf"^{p}-\d{{3}}\.svg$") for t, p in PREFIX.items()}


def root(image_dir: str | Path | None) -> Path | None:
    if not image_dir:
        return None
    p = Path(str(image_dir)).expanduser()
    return p if p.is_dir() else None


def file_of(image_dir: str | Path | None, table: str, name: str) -> Path | None:
    """IM1 · IM2 — 받을 수 있는 파일이면 그 경로(실제 경로가 폴더 안) · 아니면 None."""
    base = root(image_dir)
    if base is None or table not in NAME_RE or not NAME_RE[table].match(name or ""):
        return None
    p = base / table / name
    try:
        real = p.resolve(strict=True)
        if not real.is_file() or not real.is_relative_to(base.resolve()):
            return None
    except (OSError, RuntimeError):
        return None
    return p


def present(image_dir: str | Path | None, table: str, code: str | None) -> bool:
    return bool(code) and file_of(image_dir, table, f"{code}.svg") is not None


def etag(path: Path) -> str:
    return '"' + hashlib.sha256(path.read_bytes()).hexdigest()[:16] + '"'


def counts(image_dir: str | Path | None) -> dict[str, int] | None:
    """IM13 — 정규식에 맞는 파일만 표마다 셈(다른 이름 사본은 세지도 서빙하지도 않음)."""
    base = root(image_dir)
    if base is None:
        return None
    out = {}
    for t, rx in NAME_RE.items():
        d = base / t
        out[t] = sum(1 for f in d.iterdir() if rx.match(f.name)) if d.is_dir() else 0
    return out
