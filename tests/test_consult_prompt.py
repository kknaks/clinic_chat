"""WP-2 — 프롬프트 JSON 로더 · 설정 칸(T19 · PM2 · W-5)."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

from clinic_chat import prompts
from clinic_chat.config import ROOT, Settings, default_chat_cli_cwd
from clinic_chat.consult.prompt import SECTION_IDS, PromptError, keep, kept, load, parse
from tests.conftest import make_settings

FILE = ROOT / "prompts" / "consult.json"


def doc() -> dict:
    return json.loads(FILE.read_text(encoding="utf-8"))


def raw(d: dict) -> bytes:
    return json.dumps(d, ensure_ascii=False).encode("utf-8")


def test_loads_and_joins_in_section_order():
    p = load(FILE)
    d = doc()
    assert [s[0] for s in p.sections] == list(SECTION_IDS)
    want = "\n\n".join("\n".join(s["lines"]) for s in d["system"])
    assert p.system_text == want
    assert p.sha256 == hashlib.sha256(FILE.read_bytes()).hexdigest()
    assert p.tail_marker in p.system_text and p.draft and p.version.endswith("-draft")


@pytest.mark.parametrize("breaker", [
    lambda d: d.pop("tail_marker"),
    lambda d: d.update(system=d["system"][:-1]),
    lambda d: d["system"][1].update(lines=[]),
    lambda d: d["system"][0]["lines"].append("  "),
    lambda d: d.update(tail_marker="<<<없는 표식>>>"),
    lambda d: d["jev_front"].pop("target_carried"),
    lambda d: d["turn_headers"].update(empty=""),
    lambda d: d["system"].reverse(),
])
def test_shape_errors_fail_start(breaker):
    d = doc()
    breaker(d)
    with pytest.raises(PromptError):
        parse(raw(d))


def test_bad_file_fails_app_start(tmp_path):
    from clinic_chat.consult.server import create_app

    bad = tmp_path / "bad.json"
    bad.write_text("{}", encoding="utf-8")
    with pytest.raises(PromptError):
        create_app(make_settings(tmp_path, chat_prompt_file=str(bad), chat_dir=str(tmp_path / "chat")))


def test_no_time_or_id_placeholders_in_system():
    """PM2 — 시각 · 대화 id · 세션 id · 사용자 정보 자리 채움이 없다."""
    t = load(FILE).system_text
    assert not re.search(r"\{[a-z_]+\}", t)
    for w in ("session", "conv_id", "asked_at", "{date}"):
        assert w not in t


def test_jev_front_sections_equal_test_prompt():
    """`target` · `kind` · `gap` 은 시험 `prompts/jev/front.md` 의 절과 글자 그대로 같다(CR7 — 시험 파일은 안 고침)."""
    p = load(FILE)
    fr = prompts.jev_sections("front")
    for k in ("target", "kind", "gap"):
        assert p.jev_front[k] == fr[k]
    assert "{name}" in p.jev_front["target_carried"] and "{i}" in p.jev_front["target_carried"]


def test_keep_once_and_kept(tmp_path):
    chat = tmp_path / "chat"
    p = keep(FILE, chat)
    f = chat / "prompts" / f"{p.sha256}.json"
    assert f.read_bytes() == FILE.read_bytes()
    mtime = f.stat().st_mtime_ns
    keep(FILE, chat)
    assert f.stat().st_mtime_ns == mtime and len(list((chat / "prompts").iterdir())) == 1
    assert kept(chat, p.sha256).system_text == p.system_text


def test_settings_defaults_unchanged_and_chat_fields(tmp_path):
    s = Settings(_env_file=None)
    assert s.walk_settings() == {
        "jev_noul_yes_at": 0.7, "jev_noul_no_at": 0.3, "jev_kind_read_at": 0.5, "jev_batch_size": 32,
        "cli_timeout_sec": 600.0, "walk_max_steps": 10, "answer_input_max_tokens": 150_000,
        "answer_reasoning": "lowest_effort", "effort": "medium", "lowest_effort": "low", "hub_page_limit": 100}
    assert (s.chat_port, s.chat_model, s.chat_fallback_model, s.chat_effort, s.chat_turn_timeout_sec,
            s.chat_max_turns, s.chat_carry_turns) == (18222, "sonnet", "haiku", "low", 120.0, 20, 1)
    assert s.chat_dir_path() == ROOT / "runs" / "consult" and s.chat_fake_turn is False
    assert s.chat_prompt_path() == FILE


def test_cli_cwd_default_is_cache_not_tmp():
    """W-5 — TMPDIR 과 무관 · 레포 밖."""
    x = default_chat_cli_cwd({"XDG_CACHE_HOME": "/xdg", "TMPDIR": "/tmpx"}, "linux")
    assert x == Path("/xdg/clinic-chat/consult-cli")
    m = default_chat_cli_cwd({"TMPDIR": "/tmpx"}, "darwin")
    assert m == Path.home() / "Library" / "Caches" / "clinic-chat" / "consult-cli"
    o = default_chat_cli_cwd({}, "linux")
    assert o == Path.home() / ".cache" / "clinic-chat" / "consult-cli"
    assert ROOT not in m.parents
