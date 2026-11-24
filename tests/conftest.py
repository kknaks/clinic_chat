from __future__ import annotations

import json
from pathlib import Path

import pytest

from clinic_chat.config import Settings

FAKE_CLI = str(Path(__file__).parent / "fake_cli" / "claude")
NO_CLI = "/nonexistent/no-real-cli"  # 시험은 실물 CLI 를 절대 부르지 않는다
KOS_FIXTURE = Path(__file__).parent / "kos_fixture" / "kos_map_v3_small.json"  # 0.5 — 지은 값의 작은 `$J`(진료항목 6)
KOS_FIXTURE_ITEMS = 6


@pytest.fixture(autouse=True)
def _no_real_cli(monkeypatch):
    """어느 시험도 실물 claude 를 띄우지 않게 — 기본 bin 을 막고 인증 캐시를 비운다(옛 틀 conftest:17-18)."""
    from clinic_chat.llm import auth

    monkeypatch.setenv("CLAUDE_CLI_BIN", NO_CLI)
    auth._cache.clear()
    yield
    auth._cache.clear()


def make_settings(tmp_path: Path, **kw) -> Settings:
    """레포 .env 를 읽지 않는다(_env_file=None) · 키는 모의."""
    base = {"_env_file": None, "typesafe_api_key": "fixture-key", "claude_cli_bin": NO_CLI,
            "runs_dir": str(tmp_path / "runs"), "eval_set_dir": str(tmp_path / "eval-set"),
            "cli_timeout_sec": 20, "hub_rest_url": "http://fake-hub/procedure-hub/gold",
            "hub_gold_mcp_url": "http://fake-hub/procedure-hub/gold-mcp/mcp",
            "chat_kos_map": str(KOS_FIXTURE), "chat_kos_map_items": KOS_FIXTURE_ITEMS}
    base.update(kw)
    return Settings(**base)


def stream(answer: str = "답", *, model: str = "claude-test", tools: list[tuple[str, dict, str]] | None = None,
           structured: dict | None = None, mcp: str | None = None, thinking: bool = False,
           memos: list[str] | None = None) -> str:
    """stream-json 줄 — init · (도구 호출 · 결과) · 답 글자 · result.

    `memos[i]` = 도구 호출 i 앞에 모델이 쓴 글(도구 사이 메모 — text_delta 로 흐르고 이어 content_block_start tool_use)."""
    ev = [{"type": "system", "subtype": "init", "model": model, "apiKeySource": "none",
           "mcp_servers": [{"name": mcp, "status": "connected"}] if mcp else []}]
    for i, (name, inp, out) in enumerate(tools or []):
        ev.append({"type": "stream_event", "event": {"type": "message_start", "message": {}}})
        memo = (memos or [])[i] if i < len(memos or []) else ""
        if memo:
            ev += [{"type": "stream_event", "event": {"type": "content_block_delta",
                                                      "delta": {"type": "text_delta", "text": memo}}},
                   {"type": "assistant", "message": {"content": [{"type": "text", "text": memo}]}}]
        if memos is not None:
            ev.append({"type": "stream_event", "event": {"type": "content_block_start",
                                                         "content_block": {"type": "tool_use", "id": f"t{i}", "name": name}}})
        ev += [{"type": "assistant", "message": {"content": [{"type": "tool_use", "id": f"t{i}", "name": name, "input": inp}]}},
               {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": f"t{i}", "content": out}]}}]
    ev.append({"type": "stream_event", "event": {"type": "message_start", "message": {}}})
    if thinking:
        ev.append({"type": "stream_event", "event": {"type": "content_block_start", "content_block": {"type": "thinking"}}})
    ev += [{"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": answer}}},
           {"type": "stream_event", "event": {"type": "message_stop"}}]
    res = {"type": "result", "subtype": "success", "usage": {"input_tokens": 5, "output_tokens": 3},
           "modelUsage": {model: {}}, "result": answer}
    if structured is not None:
        res["structured_output"] = structured
    ev.append(res)
    return "\n".join(json.dumps(e, ensure_ascii=False) for e in ev) + "\n"


@pytest.fixture
def fake_cli(tmp_path, monkeypatch):
    """모의 CLI 를 쓰는 시험 — 반환 = (bin, route 폴더, log 파일)."""
    route = tmp_path / "cli-route"
    route.mkdir()
    log = tmp_path / "cli-log.jsonl"
    monkeypatch.setenv("FAKE_CLI_ROUTE", str(route))
    monkeypatch.setenv("FAKE_CLI_LOG", str(log))
    return FAKE_CLI, route, log


def read_log(log: Path) -> list[dict]:
    return [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines() if x.strip()] if log.is_file() else []
