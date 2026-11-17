"""0.3 WP-2 — `system_v3` · 「앱 판정」 칸 · 금지 낱말 검사(SPEC-006 VO1 ~ VO10 · §3-2 · §3-3 · S6-T8 · T9 · T22 · T23)."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

from clinic_chat.columns.col_b import TAIL_MARK
from clinic_chat.consult import voice
from clinic_chat.consult.prompt import KIND_LABEL_KEYS, V3_SECTION_IDS, PromptError, load, parse
from tests.conftest import read_log, stream
from tests.state_fixture import Run, jev
from tests.test_state_blanks import SYSTEM_0_1_SHA

ROOT = Path(__file__).resolve().parent.parent
PROMPT = ROOT / "prompts" / "consult.json"
HANGUL = re.compile(r"[가-힣]")


def _doc() -> dict:
    return json.loads(PROMPT.read_text(encoding="utf-8"))


def _sys(r: Run, i: int = 0) -> str:
    cli = [x for x in read_log(r.log) if "-p" in x["argv"]]
    return cli[i]["argv"][cli[i]["argv"].index("--system-prompt") + 1]


def _judgement(msg: str) -> dict:
    return json.loads(msg.split("\n")[1])


def test_s6_t8_v3_sections_in_order_and_off_sha_unchanged():
    p = load(PROMPT)
    d = _doc()
    assert [x["id"] for x in d["system_v3"]] == list(V3_SECTION_IDS) and len(V3_SECTION_IDS) == 13
    assert p.system_text_v3 == "\n\n".join("\n".join(x["lines"]) for x in d["system_v3"])
    assert p.tail_marker in p.system_text_v3 and p.version == "0.3.1-draft"
    assert hashlib.sha256(p.system_text.encode()).hexdigest() == SYSTEM_0_1_SHA  # 끈 글 = 0.1 글자 그대로
    assert p.state_ready and set(KIND_LABEL_KEYS) <= set(p.kind_labels) and p.voice_check
    assert set(p.turn_kind) == {"new", "talk", "follow"} and not HANGUL.search("".join(p.turn_kind.values()))
    assert p.slots == {"mark-modality-level": "{name} 계열 시술"}  # W-10 — 정해진 문구 하나


@pytest.mark.parametrize("breaker", [
    lambda d: d.pop("system_v3"),
    lambda d: d["jev_state"].pop("turn_kind"),
    lambda d: d.pop("kind_labels"),
    lambda d: d.pop("voice_check"),
    lambda d: d["turn_headers"].pop("close"),
    lambda d: d["turn_headers"].pop("prev"),
])
def test_s6_t8_missing_v3_key_not_ready(breaker):
    d = _doc()
    breaker(d)
    assert not parse(json.dumps(d, ensure_ascii=False).encode()).state_ready


@pytest.mark.parametrize("breaker", [
    lambda d: d["system_v3"].reverse(),
    lambda d: d["system_v3"][3].update(lines=[]),
    lambda d: d["system_v3"][-1].update(lines=["no marker"]),
    lambda d: d["voice_check"].append("(unclosed"),
    lambda d: d["kind_labels"].pop("combo"),
    lambda d: d["jev_state"]["turn_kind"].update(new=" "),
])
def test_s6_t8_bad_v3_shape_fails(breaker):
    d = _doc()
    breaker(d)
    with pytest.raises(PromptError):
        parse(json.dumps(d, ensure_ascii=False).encode())


def test_s6_t8_on_conv_uses_v3_off_conv_uses_0_1(tmp_path, fake_cli):
    r = Run(tmp_path / "on", fake_cli, noul=jev(kinds={"indication": 0.9}))
    r.turn("가짜고민 시술")
    assert _sys(r) == load(PROMPT).system_text_v3
    r.close()
    r2 = Run(tmp_path / "off", fake_cli, state="off", noul=jev(kinds={"indication": 0.9}))
    r2.turn("가짜고민 시술")
    assert hashlib.sha256(_sys(r2, -1).encode()).hexdigest() == SYSTEM_0_1_SHA
    r2.close()


def test_s6_t8_on_conv_rejected_without_v3(tmp_path, fake_cli):
    d = _doc()
    d.pop("system_v3")
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    r = Run(tmp_path / "x", fake_cli, chat_prompt_file=str(bad))
    frag = r.c.post(f"/c/{r.cid}/turns", data={"text": "가짜고민"}).text
    assert 'data-error="prompt"' in frag and not (tmp_path / "x" / "chat" / "turns.jsonl").exists()
    r.close()


def test_s6_t9_judgement_fields(tmp_path, fake_cli):
    r = Run(tmp_path, fake_cli, noul=jev({"가짜제품": 0.9}, kinds={"overview": 0.9}))
    row, _, _ = r.turn("가짜제품 받고 싶어요")  # BK2 — 선택지가 실리는 턴
    msg = row["llm_user_message"]
    j = _judgement(msg)
    for k in ("read_as", "unresolved"):
        assert k not in j
    assert all(set(o) == {"kind"} for o in j["offered"])  # EM4 — 행 수 없음 · 종류 이름만
    assert j["turn_kind"] == "new" and "ask" in j
    assert all(set(x) == {"about", "what", "safety"} for x in j["empty"])  # 이름만(WP-4 가 채운다)
    if j["ask"]:
        assert all("layer" not in o and "holder" in o for o in j["ask"]["options"])
    head = load(PROMPT).turn_headers
    assert head["close"] in msg and json.loads(msg.split(head["close"] + "\n", 1)[1].split("\n", 1)[0])["by"] \
        in ("ask", "alt", "offered", None)  # CS1 — 끝맺음이 늘 있다
    assert '"rows"' not in msg.split(head["material"])[0]  # 판정 · 끝맺음 · 상담 상태 블록에 행 수 0
    assert head["consult"] in msg
    r.close()


def test_s6_t9_off_conv_judgement_is_0_1(tmp_path, fake_cli):
    r = Run(tmp_path, fake_cli, state="off", noul=jev({"가짜제품": 0.9}, kinds={"overview": 0.9}))
    row, _, _ = r.turn("가짜제품 받고 싶어요")
    j = _judgement(row["llm_user_message"])
    assert "read_as" in j and "unresolved" in j and "turn_kind" not in j
    assert load(PROMPT).turn_headers["close"] not in row["llm_user_message"]
    r.close()


def test_s6_t22_voice_check_records_only(tmp_path, fake_cli):
    bad = "이 시술은 원장 진술 기준이에요. 자료에서 14행 더 볼 수 있어요.\n" + TAIL_MARK + "\n1: 1\n"
    r = Run(tmp_path, fake_cli, noul=jev(kinds={"indication": 0.9}))
    (fake_cli[1] / "answer.jsonl").write_text(stream(bad), encoding="utf-8")
    row, _, body = r.turn("가짜고민 시술")
    v = row["consult"]["voice"]
    texts = {h["text"] for h in v["hits"]}
    assert v["n"] == len(v["hits"]) >= 3 and {"원장 진술", "자료에서", "14행"} <= texts
    assert row["answer"] == bad.split(TAIL_MARK)[0].rstrip()  # 답은 고치지 않는다
    r.close()
    r2 = Run(tmp_path / "off", fake_cli, chat_voice_check="off", noul=jev(kinds={"indication": 0.9}))
    (fake_cli[1] / "answer.jsonl").write_text(stream(bad), encoding="utf-8")
    row2, _, _ = r2.turn("가짜고민 시술")
    assert "voice" not in row2["consult"]
    r2.close()


def test_voice_check_unit():
    pats = tuple(_doc()["voice_check"])
    assert voice.check("다행이에요. 진행이 빨라요.", pats)["n"] == 0  # 한 글자 「행」 은 「n행」 · 「행이」 꼴로만
    assert voice.check("앱은 볼로 추정해요", pats)["n"] >= 2
    assert voice.check("", pats) == {"hits": [], "n": 0}


def test_s6_t23_new_code_no_hangul():
    for name in ("voice.py",):
        body = (ROOT / "clinic_chat" / "consult" / name).read_text(encoding="utf-8")
        body = re.sub(r'(?s)""".*?"""', "", body)
        body = "\n".join(ln.split("#", 1)[0] for ln in body.splitlines())
        assert not HANGUL.search(body), name
