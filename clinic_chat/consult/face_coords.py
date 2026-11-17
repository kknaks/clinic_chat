"""얼굴 좌표표(SPEC-004 §8-2 FC5 ~ FC10 · 부록 B) — 앱의 화면 자료(gold · silver 가 아니다 · FC9).

`site_code → {shape, points}` · `shape` ∈ point · pair · area · line · none · `points` = 0 ~ 1 비율(`viewBox 0 0 590 1060` 기준).
`pair` 는 [왼쪽, 오른쪽](x 가 0.5 양쪽). 좌표는 **워커 초안**이다 — 사용자 눈 확인(§8-4) 뒤 어긋난 코드만 고친다.
부위 이름은 주석으로만(이름은 허브가 낸 값을 쓴다). 몸 부위는 표에 없다 — 표에 없는 코드 = 얼굴 밖(라벨).
"""

from __future__ import annotations

W, H = 590, 1060


def _p(x: float, y: float) -> list[list[float]]:
    return [[round(x / W, 4), round(y / H, 4)]]


def _pair(x: float, y: float) -> list[list[float]]:
    """왼쪽 점 x(픽셀) — 오른쪽은 가운데 줄로 뒤집는다."""
    return [[round(x / W, 4), round(y / H, 4)], [round((W - x) / W, 4), round(y / H, 4)]]


FACE_COORDS: dict[str, dict] = {
    # ── point 17 ──
    "SITE-014": {"shape": "point", "points": _p(295, 262)},  # 이마
    "SITE-015": {"shape": "point", "points": _p(295, 394)},  # 미간
    "SITE-035": {"shape": "point", "points": _p(295, 555)},  # 코
    "SITE-036": {"shape": "point", "points": _p(295, 505)},  # 코등
    "SITE-037": {"shape": "point", "points": _p(295, 580)},  # 코끝
    "SITE-040": {"shape": "point", "points": _p(295, 598)},  # 비주
    "SITE-041": {"shape": "point", "points": _p(295, 432)},  # 비근부
    "SITE-057": {"shape": "point", "points": _p(295, 666)},  # 입
    "SITE-060": {"shape": "point", "points": _p(262, 678)},  # 입술
    "SITE-062": {"shape": "point", "points": _p(295, 652)},  # 윗입술
    "SITE-064": {"shape": "point", "points": _p(295, 686)},  # 하순
    "SITE-069": {"shape": "point", "points": _p(295, 623)},  # 인중
    "SITE-072": {"shape": "point", "points": _p(295, 745)},  # 턱
    "SITE-073": {"shape": "point", "points": _p(295, 788)},  # 턱끝
    "SITE-077": {"shape": "point", "points": _p(295, 830)},  # 턱밑
    "SITE-079": {"shape": "point", "points": _p(295, 905)},  # 목
    "SITE-084": {"shape": "point", "points": _p(295, 130)},  # 정수리
    # ── pair 27 ──
    "SITE-016": {"shape": "pair", "points": _pair(88, 362)},  # 관자놀이
    "SITE-018": {"shape": "pair", "points": _pair(187, 438)},  # 눈
    "SITE-019": {"shape": "pair", "points": _pair(140, 462)},  # 눈 주변
    "SITE-024": {"shape": "pair", "points": _pair(182, 484)},  # 눈밑
    "SITE-025": {"shape": "pair", "points": _pair(160, 417)},  # 눈꺼풀
    "SITE-026": {"shape": "pair", "points": _pair(185, 401)},  # 안와상부
    "SITE-027": {"shape": "pair", "points": _pair(212, 415)},  # 윗눈꺼풀
    "SITE-028": {"shape": "pair", "points": _pair(187, 457)},  # 하안검
    "SITE-029": {"shape": "pair", "points": _pair(180, 380)},  # 눈썹
    "SITE-030": {"shape": "pair", "points": _pair(118, 430)},  # 눈꼬리
    "SITE-031": {"shape": "pair", "points": _pair(238, 450)},  # 내안각
    "SITE-032": {"shape": "pair", "points": _pair(133, 440)},  # 외안각
    "SITE-033": {"shape": "pair", "points": _pair(230, 480)},  # 눈물고랑
    "SITE-034": {"shape": "pair", "points": _pair(190, 467)},  # 애교살
    "SITE-039": {"shape": "pair", "points": _pair(248, 580)},  # 비익
    "SITE-046": {"shape": "pair", "points": _pair(226, 628)},  # 팔자주름
    "SITE-051": {"shape": "pair", "points": _pair(165, 565)},  # 볼
    "SITE-055": {"shape": "pair", "points": _pair(198, 518)},  # 상협부
    "SITE-056": {"shape": "pair", "points": _pair(128, 495)},  # 광대
    "SITE-067": {"shape": "pair", "points": _pair(217, 666)},  # 입꼬리
    "SITE-075": {"shape": "pair", "points": _pair(178, 742)},  # 턱선
    "SITE-076": {"shape": "pair", "points": _pair(132, 676)},  # 사각턱
    "SITE-086": {"shape": "pair", "points": _pair(84, 425)},  # 구레나룻
    "SITE-089": {"shape": "pair", "points": _pair(38, 480)},  # 이개
    "SITE-090": {"shape": "pair", "points": _pair(100, 602)},  # 이하부
    "SITE-091": {"shape": "pair", "points": _pair(64, 500)},  # 이주
    "SITE-120": {"shape": "pair", "points": _pair(220, 708)},  # 마리오네트라인
    # ── area 8 (라벨) ──
    "SITE-007": {"shape": "area", "points": []},  # 얼굴
    "SITE-011": {"shape": "area", "points": []},  # 중안면
    "SITE-013": {"shape": "area", "points": []},  # 하안면
    "SITE-045": {"shape": "area", "points": []},  # 코안면
    "SITE-058": {"shape": "area", "points": []},  # 입 주변
    "SITE-083": {"shape": "area", "points": []},  # 두피
    "SITE-087": {"shape": "area", "points": []},  # 수염
    "SITE-088": {"shape": "area", "points": []},  # 머리
    # ── line 4 (라벨) ──
    "SITE-065": {"shape": "line", "points": []},  # 점막-피부 경계
    "SITE-080": {"shape": "line", "points": []},  # 목선
    "SITE-081": {"shape": "line", "points": []},  # 네크라인
    "SITE-085": {"shape": "line", "points": []},  # 헤어라인
    # ── none 4 (라벨 — 정면에 안 보임) ──
    "SITE-043": {"shape": "none", "points": []},  # 비강
    "SITE-044": {"shape": "none", "points": []},  # 부비동
    "SITE-070": {"shape": "none", "points": []},  # 잇몸
    "SITE-071": {"shape": "none", "points": []},  # 구강
}

SHAPES = ("point", "pair", "area", "line", "none")
DOT_SHAPES = ("point", "pair")


def px(pt: list[float]) -> tuple[float, float]:
    """비율 → viewBox 좌표."""
    return round(pt[0] * W, 1), round(pt[1] * H, 1)
