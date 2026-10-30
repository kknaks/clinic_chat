"""env 읽기 — pydantic-settings 가 레포 루트 `.env` 를 직접 읽는다(SPEC-006 §2-1 · `--env-file` 안 씀).

환경 변수가 있으면 그것이 `.env` 보다 앞선다. 키 값은 SecretStr 로만 들고, 출력 · 기록에는 「있음 · 없음」만 쓴다.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", env_file_encoding="utf-8", extra="ignore")

    anthropic_api_key: SecretStr | None = None
    typesafe_api_key: SecretStr | None = None
    typesafe_default_model: str = "jev-1.14.0"

    answer_model: str = "claude-sonnet-5-5"
    judge_model: str = "claude-opus-5-5"
    extract_model: str = "claude-opus-5-5"

    hub_gold_mcp_url: str = "http://127.0.0.1:18005/procedure-hub/gold-mcp/mcp"
    hub_raw_mcp_url: str = "http://127.0.0.1:18005/procedure-hub/raw-mcp/mcp"
    hub_rest_url: str = "http://127.0.0.1:18005/procedure-hub/gold"

    raw_dir: str = ""
    semantic_json: str = ""
    runs_dir: str = "./runs"

    clinic_chat_port: int = 18210
    case_tool_loop_max: int = 12
    jev_confidence_min: float = 0.3
    tool_result_max_bytes: int = 20_000  # case1 · 2 도구 결과를 Sonnet 에 넘길 때 상한(넘으면 잘림 표시)
    auto_quality: bool = False  # ① · ② 를 질문마다 자동으로 돌릴지(기본 끔 — 화면 「채점」 버튼)

    @property
    def has_anthropic_key(self) -> bool:
        return bool(self.anthropic_api_key and self.anthropic_api_key.get_secret_value().strip())

    @property
    def has_typesafe_key(self) -> bool:
        return bool(self.typesafe_api_key and self.typesafe_api_key.get_secret_value().strip())

    @property
    def runs_path(self) -> Path:
        p = Path(self.runs_dir)
        return p if p.is_absolute() else ROOT / p

    def env_file_loaded(self) -> bool:
        return (ROOT / ".env").is_file()

    def summary_line(self) -> str:
        """기동 확인 줄 — 키는 있음 · 없음만(값 출력 금지)."""
        yn = lambda b: "있음" if b else "없음"
        return (
            f".env {yn(self.env_file_loaded())} · ANTHROPIC_API_KEY {yn(self.has_anthropic_key)} · "
            f"TYPESAFE_API_KEY {yn(self.has_typesafe_key)} · RAW_DIR {yn(bool(self.raw_dir))} · "
            f"SEMANTIC_JSON {yn(bool(self.semantic_json))} · answer={self.answer_model} · judge={self.judge_model} · "
            f"jev={self.typesafe_default_model} · port={self.clinic_chat_port}"
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
