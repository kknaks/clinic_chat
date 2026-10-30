"""env 읽기 — pydantic-settings 가 레포 루트 `.env` 를 직접 읽는다(SPEC-006 §2-1 · `--env-file` 안 씀).

환경 변수가 있으면 그것이 `.env` 보다 앞선다. 키 값은 SecretStr 로만 들고, 출력 · 기록에는 「있음 · 없음」만 쓴다.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr, field_validator
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
    auto_quality: bool = False  # sdk 판 ① · ② 자동 여부(기본 끔 — 화면 「채점」 버튼). CLI 판은 늘 자동

    # CLI provider(실험 — 구독 인증 · cli-research) — 10-30 사용자 결정 값
    default_provider: str = "claude-cli"
    claude_cli_bin: str = "claude"
    codex_cli_bin: str = "codex"
    claude_cli_model: str = "claude-sonnet-5-5"
    claude_cli_effort: str = "medium"
    codex_cli_model: str = "gpt-5.6-terra"
    codex_cli_effort: str = "medium"
    codex_service_tier: str = ""  # 비면 CLI 기본
    cli_timeout_sec: float = 600  # 도구 루프 상한 대신(CLI 에 칸 없음 · D-3)
    cli_max_budget_usd: float = 1.0  # Claude --max-budget-usd(추정 $ 상한)
    judge_provider: str = "claude-cli"  # ① · ② 채점 provider(Opus) — claude-cli(기본) · sdk
    judge_cli_effort: str = "medium"
    extract_cli_effort: str = "low"
    judge_max_budget_usd: float = 3.0

    # 일괄 실행 — 값은 사용자가 정함(10-30 「한도는 구현부터 하고 정할게」) · 비면 배치를 시작하지 않음
    batch_gap_sec: float | None = None  # 판 사이 간격
    batch_retries: int | None = None  # 판 하나 재시도 수(기동 실패 · 비정상 종료 · 시간 초과 · 과부하만)
    batch_retry_backoff_sec: str = ""  # 재시도 간격 목록(예 "60,180")
    batch_limit_wait_sec: float | None = None  # 한도에 걸렸는데 다시 열리는 시각을 모를 때 기다릴 시간
    questions_md: str = ""  # 질문 리스트 파일(레포 밖 · 사용자 경로)

    @field_validator("batch_gap_sec", "batch_retries", "batch_limit_wait_sec", mode="before")
    @classmethod
    def _blank_none(cls, v):  # .env 의 빈칸 = 안 정함
        return None if isinstance(v, str) and not v.strip() else v

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
