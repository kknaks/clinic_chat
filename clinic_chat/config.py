"""env 읽기 — pydantic-settings 가 레포 루트 `.env` 를 직접 읽는다(SPEC-006 §2-1 · `--env-file` 안 씀).

환경 변수가 있으면 그것이 `.env` 보다 앞선다. 키 값은 SecretStr 로만 들고, 출력 · 기록에는 「있음 · 없음」만 쓴다.
"""

from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent

# 도구 결과 잘림 표시(SPEC-006 §3-5) — provider 별 문자열 · 기록 settings.truncation_markers_sha.
# Claude: WP-0 확인 시험(11-01 · 실물 2회)에서 마커가 나오지 않아 비움(F-3 「안 나온 후보는 뺀다」 · 코디 결정 A) —
#   허브 도구 meta(maxResultSizeChars 200,000)로 91k자 결과도 그대로 전달 · MAX_MCP_OUTPUT_TOKENS=1000 이어도 그대로.
#   잘림은 verify_runs 바이트 대조(unknown 도 잘림으로 셈)로만 판정.
# Codex: 이번 판 실물 호출 없음(사용자 결정 — Claude 만) → 바이너리 문자열 시작값 그대로(미확인).
TRUNCATION_MARKERS: dict[str, tuple[str, ...]] = {
    "claude-cli": (),
    "codex-cli": (
        "Warning: truncated output (original token count:",
        "<truncated omitted_approx_tokens=",
        "omitted_due_to_size_limit (overage_bytes=",
        " bytes omitted ...",
        " tokens truncated",
    ),
    "sdk": ("…[잘림: 결과 ",),  # loop.cap_text 표시(TOOL_RESULT_MAX_BYTES > 0 일 때만)
}


# 허브 MCP 도구 결과 크기 meta 키(SPEC-005 §5-0-2 M-1 — Claude CLI 저장 문턱을 그 도구만 올림)
META_MAX_CHARS = "anthropic/maxResultSizeChars"


def truncation_markers_sha() -> str:
    return hashlib.sha256(json.dumps(TRUNCATION_MARKERS, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]


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
    # 도구 · 허브 결과 입력 상한(세 케이스 공통 하나) — sdk 판에서 case1 · 2 도구 결과와 case3 허브 결과에 같이.
    # 0 = 없음(SPEC-006 v2 §2-5 · 두 판 같게 — 허브 응답 전체를 모델에). 기록 settings.input_cap_bytes
    tool_result_max_bytes: int = 0
    # 도구 결과 전달 보장(§3-5) — 허브 MCP 도구 meta 와 같은 값 · Claude 자식 env MAX_MCP_OUTPUT_TOKENS · Codex 토큰 한도
    mcp_result_max_chars: int = 200_000
    # case3 Jev 걸음(§3-4) — 걸음 상한(0 = 걸음 없이 v1 경로만) · 펼친 바이트 예산 · 「충분」 문턱
    walk_max_steps: int = 4
    walk_byte_budget: int = 20_000
    walk_enough_min: float = 0.8  # 걷기 고침 2 ② (11-01 · 0.5 → 0.8)
    walk_enough_bytes_ratio: float = 0.5  # 모은 바이트가 예산의 이 비율 미만이면 「충분」 멈춤 무시(고침 2 ②)
    walk_required_sections_csv: str = "checks,site_caution"  # 필수 묶음(고침 2 ①) — 시술 노드마다 Jev 와 무관하게
    walk_required_max: int = 3  # 필수 묶음을 받을 시술 수(모은 줄 rank 순 위 · 시작 시술은 따로)
    # 의도별 고정 섹션(고침 2 ③) — 첫 시술 노드에서 Jev 고름에 더함 · 「의도:섹션+섹션,…」
    walk_fixed_sections_by_intent: str = ("I-02:overview+method,I-03:aftercare+recovery,"
                                          "I-04:safety.side_effects+safety.complications+safety.management,I-05:recovery+timeline,"
                                          "I-07:evidence,I-08:evidence")
    # v2.2~v2.4 걸음 규칙(§3-4-1 · §3-4-6) — 걸음당 섹션 · 이웃 · 걸음 1 섹션 상한(의도별 덮어씀) · ⑤ cut 줄 원문 예산 · 원문 줄 수
    walk_sections_per_step: int = 2
    walk_neighbors_max: int = 3
    walk_first_section_max: int = 1
    walk_first_section_max_by_intent: str = "I-02:2,I-03:2,I-04:2,I-05:2"
    walk_full_byte_budget: int = 15_000
    walk_full_facts_max: int = 64
    walk_order_table: str = ""  # 비면 clinic_chat/cases/walk_order.json(order_v24 복사본)
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
    cli_max_concurrent: int = 6  # 동시에 띄우는 CLI 수 상한(답 세 열 + 열마다 채점 · 질문끼리 공유) — 넘으면 cli_queue 로 기다림
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
    question_set_json: str = ""  # 질문 세트 {questions:[{no, src_id, topic, question, reference_answer, …}]} — 있으면 이것을 씀

    @field_validator("batch_gap_sec", "batch_retries", "batch_limit_wait_sec", mode="before")
    @classmethod
    def _blank_none(cls, v):  # .env 의 빈칸 = 안 정함
        return None if isinstance(v, str) and not v.strip() else v

    def required_sections(self) -> list[str]:
        return [x.strip() for x in self.walk_required_sections_csv.split(",") if x.strip()]

    def fixed_sections(self, intent: str | None) -> list[str]:
        for part in self.walk_fixed_sections_by_intent.split(","):
            k, _, v = part.strip().partition(":")
            if k and k == intent:
                return [x.strip() for x in v.split("+") if x.strip()]
        return []

    def first_section_max(self, intent: str | None) -> int:
        """걸음 1 섹션 상한 — WALK_FIRST_SECTION_MAX_BY_INTENT(「I-02:2,…」)에 있으면 그 값."""
        for part in self.walk_first_section_max_by_intent.split(","):
            k, _, v = part.strip().partition(":")
            if k and k == intent and v.strip().isdigit():
                return int(v)
        return self.walk_first_section_max

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
