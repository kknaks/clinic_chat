"""모의 판 서버 — 모의 허브 · 모의 Anthropic · 모의 Jev 로 같은 화면을 127.0.0.1:18210 에 띄운다(키 · 허브 없이).

    uv run python -m tests.mock_server [--runs-dir 레포 밖 폴더]

원장 · 시맨틱 fixture 는 임시 폴더에 쓴다. 기록은 --runs-dir(없으면 임시 폴더) — 실험 기록(RUNS_DIR)과 섞지 않는다.
"""

from __future__ import annotations

import argparse
import re
import tempfile
from pathlib import Path

import uvicorn

from clinic_chat.clients.hub_mcp import HubMCP, asgi_client_factory
from clinic_chat.clients.hub_rest import HubRest
from clinic_chat.config import Settings
from clinic_chat.deps import Deps
from clinic_chat.quality.normalize import load_rules, make_normalizer
from clinic_chat.quality.raw_tools import RawStore
from clinic_chat.server import create_app
from tests.fake_anthropic import FakeAnthropic, mock_policy
from tests.fake_data import SEMANTIC_FIXTURE, write_raw_dir, write_semantic
from tests.fake_hub import GOLD_MCP_URL, RAW_MCP_URL, REST_URL, FakeHub
from tests.fake_jev import FakeJev

INTENT_RULES = [(r"어떻게|방법", "I-02"), (r"사우나|주의|관리", "I-03"), (r"부작용|멍", "I-04"), (r"출근|회복|얼마나 가", "I-05"),
                (r"비교|달라|차이", "I-06"), (r"같이|순서|간격", "I-07"), (r"전에", "I-08"), (r"임신|체질", "I-09"),
                (r"몇 ?cc|용량|회차", "I-10"), (r"패키지|조합", "I-11"), (r"얼마|가격|비용", "I-12")]


def mock_intent(state: str) -> str:
    return next((i for pat, i in INTENT_RULES if re.search(pat, state)), "I-01")


def build(runs_dir: str | None, realtime: bool = True) -> tuple[Deps, FakeHub]:
    tmp = Path(tempfile.mkdtemp(prefix="clinic-chat-mock-"))
    raw = write_raw_dir(tmp / "rawfx")
    sem = write_semantic(tmp / "semfx" / "sem.json")
    s = Settings(_env_file=None, anthropic_api_key="mock", typesafe_api_key="mock", raw_dir=str(raw),
                 semantic_json=str(sem), runs_dir=runs_dir or str(tmp / "runs"), hub_gold_mcp_url=GOLD_MCP_URL,
                 hub_raw_mcp_url=RAW_MCP_URL, hub_rest_url=REST_URL, judge_provider="sdk", default_provider="sdk")
    hub = FakeHub(str(raw))
    f = asgi_client_factory(hub.app)
    deps = Deps(settings=s, anthropic=FakeAnthropic(mock_policy, realtime=realtime),
                jev=FakeJev(intent=mock_intent).client(s), gold_mcp=HubMCP(GOLD_MCP_URL, f),
                raw_mcp=HubMCP(RAW_MCP_URL, f), hub_rest=HubRest(REST_URL, f), raw_store=RawStore(raw),
                normalizer=make_normalizer(load_rules(sem)), semantic=SEMANTIC_FIXTURE["dictionary"])
    return deps, hub


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs-dir")
    a = ap.parse_args()
    deps, hub = build(a.runs_dir)
    print(f"[mock] RUNS_DIR={deps.settings.runs_path}", flush=True)
    uvicorn.run(create_app(deps, before_start=hub.start), host="127.0.0.1", port=deps.settings.clinic_chat_port)


if __name__ == "__main__":
    main()
