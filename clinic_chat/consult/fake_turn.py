"""가짜 턴(WORK-011 §6 — E0 · 템플릿 시험 전용) — 외부 호출 0 · 값은 코드 값 · 영문 자리 글자.

`CHAT_FAKE_TURN=1` 일 때만 `turn_fn` 으로 쓰인다. 꼴: `step`(단계 줄) → `items`(0.5 — 진료항목 목록 + 점 · 몸 목록) → `step` → `delta` 여럿(지금까지 글 전체 ·
마크다운 · 앞 공백 · 줄바꿈 꼴 포함 — 검수 N-9) → `step`(모두 끝) → `done`. 질문에 `error` 가 들면 `step` 뒤 `turn_error` 로 끝난다(W-3 ④).
"""

from __future__ import annotations

import asyncio

from clinic_chat.consult import items_view as iv_mod
from clinic_chat.consult.sites import place
from clinic_chat.consult.steps import Steps
from clinic_chat.walk.end_state import OK

FAKE_LIST = {
    "concerns": [
        {
            "concern_code": "CON-000",
            "concern_name": "concern-x",
            "inherited_from": None,
            "modalities": [
                {
                    "modality_name": "modality-a",
                    "procedures": [
                        {
                            "procedure_code": "PRC-900",
                            "procedure_name": "procedure-<a>",
                            "reach": ["reach-1"],
                            "concern_match": ["match-1"],
                            "gids": ["GIN-00001"],
                        },
                        {
                            "procedure_code": "PRC-901",
                            "procedure_name": "procedure-b",
                            "reach": ["reach-1", "reach-2"],
                            "concern_match": ["match-2"],
                            "gids": ["GIN-00002"],
                        },
                    ],
                },
                {
                    "modality_name": "",
                    "procedures": [
                        {
                            "procedure_code": "PRC-902",
                            "procedure_name": "procedure-c",
                            "reach": [],
                            "concern_match": [],
                            "gids": ["GIN-00003"],
                        }
                    ],
                },
            ],
            "modality_only": [{"modality_name": "modality-b", "gids": ["GIN-00004"]}],
            "unreached": [{"candidate_name": "candidate-d", "reach": "reach-3", "gid": "GIN-00005"}],
            "counts": {"procedures": 3, "modalities": 2, "modality_only": 1, "unreached": 1, "rows": 5},
        }
    ]
}
FAKE_SITES = [
    {"concern_code": "CON-000", "site_code": "SITE-046", "site_name": "site-a", "csi_id": "CSI-1"},
    {"concern_code": "CON-000", "site_code": "SITE-014", "site_name": "site-b", "csi_id": "CSI-2"},
    {"concern_code": "CON-000", "site_code": "SITE-007", "site_name": "site-c", "csi_id": "CSI-3"},
    {"concern_code": "CON-000", "site_code": "SITE-999", "site_name": "site-d", "csi_id": "CSI-4"},
]
FAKE_DELTAS = [
    "Fake",
    " answer",
    " <b>not bold</b>",
    " **bold**",
    "\n\n",
    "- item one\n",
    "- item ",
    "two\n",
    "\n",
    "  second line",
    " end.",
]
FAKE_END = OK
FAKE_GROUPS = 3  # 0.5 — 이름이 안 맞으면 `$J` 앞 묶음 셋


def fake_topics(conv, n: int, kos, codes: list[str]) -> tuple[list[dict], str | None]:
    """0.5 IL11 화면 틀용 가짜 주제(실제 주제 규칙은 WP-3): 감지가 있으면 턴마다 새 주제 하나(조건 = 감지된 KOS 시술 이름
    앞 둘 · 시술 칸) · 앞 주제는 그대로 남음 · 감지 0 이면 주제 그대로(TP3 7)."""
    topics, active = conv.last_topics()
    if not codes:
        return topics, active
    names = list(dict.fromkeys(kos.item[c]["kos_procedure"] for c in codes if c in kos.item))[:2]
    tid = f"t{n}"
    new = {
        "id": tid,
        "conditions": {"procedure": [{"code": "", "name": x} for x in names]},
        "items": codes,
        "created_turn": n,
    }
    return [*topics, new], tid


def fake_codes(kos, question: str) -> list[str]:
    """0.5 가짜 감지(외부 호출 0 · 화면 틀 확인용 — 실제 감지는 WP-3): 공백 지운 말과 KOS 시술 이름이 서로 들어 있으면 그 묶음 ·
    없으면 앞 묶음 `FAKE_GROUPS` 개. 말에 `empty` 가 들면 0(빈 목록 자리 확인)."""
    if kos is None or "empty" in question:
        return []
    q = "".join(question.split())
    hit = [
        g
        for g in kos.groups
        if q and (q in "".join(g["kos_procedure"].split()) or "".join(g["kos_procedure"].split()) in q)
    ]
    return [c for g in (hit or list(kos.groups)[:FAKE_GROUPS]) for c in g["items"]]


def make(delay: float = 0.15):
    async def run(ctx, conv, n: int, question: str, push, *, clicked_at: float | None = None) -> None:
        r = ctx.render

        async def pause() -> None:
            if delay:
                await asyncio.sleep(delay)

        steps = Steps()

        def step(code: str | None = None, *, listed: bool = False, finish: bool = False) -> None:
            changed = steps.listed() if listed else steps.finish() if finish else steps.raw(code or "")
            if changed:
                push("step", r.steps(steps.view()))

        step("front")
        await pause()
        if "error" in question:
            step(finish=True)
            push("turn_error", r.error(n, "fake"))
            ctx.ledger.end(
                conv,
                {
                    "turn": n,
                    "question": question,
                    "error": {"kind": "fake", "stage": "fake", "detail": None},
                    "fake": True,
                },
            )
            return
        step("hub")
        await pause()
        sites = place(FAKE_SITES)
        kos = getattr(ctx, "kos", None)
        codes = fake_codes(kos, question)
        topics, active = fake_topics(conv, n, kos, codes) if kos is not None else ([], None)
        if kos is not None:
            iv = (
                iv_mod.topics_view(kos, topics, active, max_n=ctx.settings.chat_topic_max)
                if topics
                else iv_mod.view(kos, codes)
            )
            push("items", r.items(iv, conv.conv_id))
        step(listed=True)
        step("llm")
        await pause()
        await pause()
        shown = ""
        for d in FAKE_DELTAS:
            await pause()
            shown += d
            if shown.strip():
                push("delta", r.delta(shown))
        body = "".join(FAKE_DELTAS)
        step(finish=True)
        push("done", r.answer(n, body, FAKE_END))
        ctx.ledger.end(
            conv,
            {
                "turn": n,
                "question": question,
                "answer": body,
                "end_state": FAKE_END,
                "list": FAKE_LIST,
                "sites": sites,
                "items": {"codes": codes},
                "consult": {"topics": topics, "active_topic": active},
                "error": None,
                "fake": True,
            },
        )

    return run
