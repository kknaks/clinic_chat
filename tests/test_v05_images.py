"""SPEC-010 WP-4 — 그림 라우트(§8 IM1 ~ IM3 · IM13). 외부 호출 0(임시 시드 폴더)."""

from __future__ import annotations

import os

from fastapi.testclient import TestClient

from clinic_chat.consult import images as img
from clinic_chat.consult.fake_turn import make
from clinic_chat.consult.server import create_app
from tests.conftest import make_settings

SVG = b'<svg xmlns="http://www.w3.org/2000/svg"/>'


def seed(tmp_path):
    d = tmp_path / "images"
    (d / "mechanism").mkdir(parents=True)
    (d / "method").mkdir()
    (d / "mechanism" / "MEC-001.svg").write_bytes(SVG)
    (d / "mechanism" / "MEC-001__botulinum-toxin.svg").write_bytes(SVG)  # 다른 이름 사본(IM13 · 서빙 0)
    (d / "method" / "MET-002.svg").write_bytes(SVG)
    (d / "method" / "MET-003.png").write_bytes(b"x")
    return d


def client(tmp_path, image_dir):
    s = make_settings(
        tmp_path,
        chat_dir=str(tmp_path / "chat"),
        chat_fake_turn=True,
        chat_image_dir=str(image_dir) if image_dir else None,
    )
    return TestClient(create_app(s, turn_fn=make(0)))


def test_im2_serves_with_headers(tmp_path):
    c = client(tmp_path, seed(tmp_path))
    r = c.get("/img/mechanism/MEC-001.svg")
    assert r.status_code == 200 and r.content == SVG and r.headers["content-type"] == "image/svg+xml"
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["cache-control"] == "no-cache"
    assert len(r.headers["etag"]) == 18  # 따옴표 + sha256 앞 16
    assert c.get("/img/method/MET-002.svg").status_code == 200


def test_im2_rejects(tmp_path):
    d = seed(tmp_path)
    (tmp_path / "outside.svg").write_bytes(SVG)
    os.symlink(tmp_path / "outside.svg", d / "method" / "MET-009.svg")  # 폴더 밖 링크
    c = client(tmp_path, d)
    for url in (
        "/img/mechanism/MET-002.svg",  # 표 ↔ 접두어
        "/img/method/MEC-001.svg",
        "/img/other/MEC-001.svg",
        "/img/mechanism/MEC-001__botulinum-toxin.svg",
        "/img/mechanism/MEC-01.svg",
        "/img/method/MET-003.png",
        "/img/mechanism/..%2Fmethod%2FMET-002.svg",
        "/img/mechanism/MEC-404.svg",
        "/img/method/MET-009.svg",
    ):
        assert c.get(url).status_code == 404, url


def test_im3_presence_per_request_and_304(tmp_path):
    d = seed(tmp_path)
    c = client(tmp_path, d)
    assert c.get("/img/mechanism/MEC-002.svg").status_code == 404
    (d / "mechanism" / "MEC-002.svg").write_bytes(SVG)  # 시험 중 넣으면 다음 요청에 보임(캐시 0)
    r = c.get("/img/mechanism/MEC-002.svg")
    assert r.status_code == 200
    r2 = c.get("/img/mechanism/MEC-002.svg", headers={"If-None-Match": r.headers["etag"]})
    assert r2.status_code == 304 and not r2.content
    (d / "mechanism" / "MEC-002.svg").write_bytes(SVG + b" ")  # 바뀌면 ETag 도 바뀜
    assert c.get("/img/mechanism/MEC-002.svg", headers={"If-None-Match": r.headers["etag"]}).status_code == 200


def test_empty_image_dir_always_404(tmp_path):
    seed(tmp_path)
    c = client(tmp_path, None)
    assert c.get("/img/mechanism/MEC-001.svg").status_code == 404
    assert c.app.state.image_counts is None


def test_im13_counts_regex_only(tmp_path):
    d = seed(tmp_path)
    assert img.counts(d) == {"mechanism": 1, "method": 1}
    assert client(tmp_path, d).app.state.image_counts == {"mechanism": 1, "method": 1}
    assert img.present(d, "mechanism", "MEC-001") and not img.present(d, "mechanism", "MEC-404")
    assert not img.present(d, "method", None) and not img.present(None, "method", "MET-002")
