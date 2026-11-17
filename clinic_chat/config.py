"""설정 — pydantic-settings · 레포 루트 `.env`(gitignore). WORK-002 §2-5 의 env 이름 그대로.

값(키)은 로그 · 기록 · 보고에 「있음 · 없음」 만 적는다(`describe()`). 코드는 `.env` 를 직접 열지 않는다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent

# 답을 만드는 세 열의 자식 프로세스에 주지 않는 env(WORK-002 §2-5 끝 · SPEC-002 §2-3 · §8-4)
EVAL_ONLY_ENV = ("GOLD_CSV_DIR", "LEDGER_CHECK_PATH", "QUESTIONS_CSV", "EVAL_SET_DIR")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", env_file_encoding="utf-8", extra="ignore")

    # 키 — ANTHROPIC_API_KEY 는 쓰지 않는다(있어도 자식 env 에서 지운다 · D8)
    typesafe_api_key: SecretStr | None = None
    typesafe_default_model: str = "jev-latest"

    # LLM(CLI 구독 — SPEC-002 §6-1)
    claude_cli_bin: str = "claude"
    answer_model: str = "sonnet"
    judge_model: str = "opus"
    claude_cli_effort: str = "medium"
    cli_timeout_sec: float = 600.0
    cli_max_concurrent: int = 1
    answer_reasoning: Literal["off", "lowest_effort"] = "lowest_effort"  # E2 · E5 결과(.env.example 설명)
    lowest_effort: str = "low"  # `--effort` 가 받는 가장 낮은 값(claude --help: low · medium · high · xhigh · max)
    mcp_output_tokens: int | None = None  # CLI 의 MCP 결과 토큰 상한(MAX_MCP_OUTPUT_TOKENS) — 비우면 CLI 기본

    # 허브(로컬 18105 — WORK-001 §2-5)
    hub_rest_url: str = "http://127.0.0.1:18105/procedure-hub/gold"
    hub_gold_mcp_url: str = "http://127.0.0.1:18105/procedure-hub/gold-mcp/mcp"
    hub_page_limit: int = 100
    hub_commit: str | None = None
    hub_worktree: str | None = None

    # A 열 원장 폴더
    raw_dir: str | None = None

    # 걷기 — 전부 임시값(SPEC-002 §3-4 · §3-1 C3 · §3-6 FT4)
    jev_noul_yes_at: float = 0.7
    jev_noul_no_at: float = 0.3
    jev_kind_read_at: float = 0.5  # KD10 — 사이 값 가운데 이 값 이상인 정보 종류는 읽는다(SPEC-002 0.6 · 시작값 · O-32 ①)
    jev_batch_size: int = 32
    walk_max_steps: int = 10
    answer_input_max_tokens: int = 150_000

    # 세트 · 채점(답을 만드는 세 열에는 주지 않는다 — EVAL_ONLY_ENV)
    eval_set_dir: str = "data/eval-set"
    questions_csv: str | None = None
    gold_csv_dir: str | None = None
    ledger_check_path: str | None = None

    # 기록
    runs_dir: str = "runs"
    batch_pause_sec: float = Field(default=0.0, ge=0)

    @property
    def has_typesafe_key(self) -> bool:
        return bool(self.typesafe_api_key and self.typesafe_api_key.get_secret_value().strip())

    def path(self, value: str | None) -> Path | None:
        if not value:
            return None
        p = Path(value).expanduser()
        return p if p.is_absolute() else ROOT / p

    def walk_settings(self) -> dict:
        """줄 머리 `settings`(SPEC-002 §7-1) — 값만 · 키 없음."""
        return {
            "jev_noul_yes_at": self.jev_noul_yes_at, "jev_noul_no_at": self.jev_noul_no_at,
            "jev_kind_read_at": self.jev_kind_read_at,
            "jev_batch_size": self.jev_batch_size, "cli_timeout_sec": self.cli_timeout_sec,
            "walk_max_steps": self.walk_max_steps, "answer_input_max_tokens": self.answer_input_max_tokens,
            "answer_reasoning": self.answer_reasoning, "effort": self.claude_cli_effort,
            "lowest_effort": self.lowest_effort, "hub_page_limit": self.hub_page_limit,
        }

    def describe(self) -> dict:
        """보고 · `--check` 용 — 키는 있음/없음만."""
        return {"typesafe_api_key": "있음" if self.has_typesafe_key else "없음",
                "answer_model": self.answer_model, "judge_model": self.judge_model,
                "hub_rest_url": self.hub_rest_url, "raw_dir": "있음" if self.raw_dir else "없음"}


def load_settings(**kw) -> Settings:
    return Settings(**kw)
