from __future__ import annotations

import pytest

from clinic_chat.clients.hub_mcp import HubMCP, asgi_client_factory
from clinic_chat.clients.hub_rest import HubRest
from clinic_chat.config import Settings
from clinic_chat.deps import Deps
from clinic_chat.quality.normalize import load_rules, make_normalizer
from clinic_chat.quality.raw_tools import RawStore
from tests.fake_data import SEMANTIC_FIXTURE, write_raw_dir, write_semantic
from tests.fake_hub import GOLD_MCP_URL, RAW_MCP_URL, REST_URL, FakeHub


def make_settings(tmp_path, **kw) -> Settings:
    """시험은 레포 .env 를 읽지 않는다(_env_file=None) · 키는 모의."""
    base = dict(_env_file=None, anthropic_api_key="fixture-key", typesafe_api_key="fixture-key",
                raw_dir=str(tmp_path / "rawfx"), semantic_json=str(tmp_path / "semfx" / "sem.json"),
                runs_dir=str(tmp_path / "runs"), hub_gold_mcp_url=GOLD_MCP_URL, hub_raw_mcp_url=RAW_MCP_URL,
                hub_rest_url=REST_URL)
    base.update(kw)
    return Settings(**base)


@pytest.fixture
def raw_dir(tmp_path):
    return write_raw_dir(tmp_path / "rawfx")


@pytest.fixture
def semantic_path(tmp_path):
    return write_semantic(tmp_path / "semfx" / "sem.json")


@pytest.fixture
async def hub(raw_dir):
    h = FakeHub(str(raw_dir))
    await h.start()
    yield h
    await h.stop()


@pytest.fixture
def deps_factory(tmp_path, hub, semantic_path):
    def make(anthropic=None, jev=None, **kw) -> Deps:
        s = make_settings(tmp_path, **kw)
        f = asgi_client_factory(hub.app)
        return Deps(settings=s, anthropic=anthropic, jev=jev, gold_mcp=HubMCP(s.hub_gold_mcp_url, f),
                    raw_mcp=HubMCP(s.hub_raw_mcp_url, f), hub_rest=HubRest(s.hub_rest_url, f),
                    raw_store=RawStore(s.raw_dir), normalizer=make_normalizer(load_rules(semantic_path)),
                    semantic=SEMANTIC_FIXTURE["dictionary"])

    return make
