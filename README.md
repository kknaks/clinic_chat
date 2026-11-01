# clinic_chat

상담 질문 하나를 세 갈래(원장 MCP + Claude · 골드 MCP + Claude · 골드 + Jev + Claude)로 동시에 돌려 속도와 답 품질을 비교하는 로컬 실험 앱.

- 키 · 원장 · 실버 · 골드 데이터 · 실험 기록은 이 레포에 커밋하지 않는다. 값은 `.env`(git 제외)에만 둔다 — `.env.example` 참고.
- 환자 정보를 질문에 넣지 않는다.

## 데이터 · 키 규칙(공개 레포)

- 레포에 넣지 않는 것: API 키 · `.env` · 원장 json 21 · `semantic.json` · 실버 · 골드 파일 · 실행 기록(`runs/` · `*.jsonl`) · 허브 응답 캐시(`.cache/`) · 사용자 홈 절대경로.
- 경로 · 키는 `.env` 로만 받는다. `config.py`(pydantic-settings)가 레포 루트 `.env` 를 직접 읽는다 — `--env-file` 필요 없음. 환경 변수가 있으면 그것이 앞선다.
- 서버는 키 값을 출력 · 기록하지 않는다(기동 줄에 「있음 · 없음」만). 키가 없으면 그 케이스만 「키 없음」으로 비활성.
- 질문과 허브 노드 이름은 Jev(TypeSafe · 미국)로 나간다 — 환자 정보를 넣지 않는다.

## 실행 절차

전제: 허브(SPEC-005)가 `127.0.0.1:18005` 에 떠 있다 · 키는 `.env` 에 있다.

```bash
cp .env.example .env        # .env 가 이미 있으면 건너뜀 · ANTHROPIC_API_KEY · TYPESAFE_API_KEY · RAW_DIR · SEMANTIC_JSON 채움
uv sync
uv run python -m clinic_chat.server   # 127.0.0.1:18210
open http://127.0.0.1:18210
```

- 첫 질문: 환자 정보 없는 일반 질문 하나 → [보내기]. 세 열(case1 원장 MCP · case2 골드 MCP · case3 골드 + Jev)이 같이 흐르고, 셋 다 끝나면 ① 단어 검사 → ② Opus 채점이 붙고, 기록 목록에 한 줄이 생긴다.
- 기동 확인 줄: `.env` · 키(있음 · 없음) · 허브 골드 · 원장 MCP 연결 · vocab 캐시 셈 · 모델 id.
- 모의 판(키 · 허브 없이): `uv run python -m tests.mock_server` — 모의 허브 · 모의 Anthropic · 모의 Jev 로 같은 화면을 18210 에 띄운다.
- 기록 검증(SPEC-006 v2 §4-4): `uv run python -m clinic_chat.verify_runs --batch <배치 id>` — 잘림(허브 재호출 대조) · intent 전달 · 격리 밖 도구 · case3 걸음 · 공정성 바이트 · C 비율을 판단 없이 셈. 출력은 레포 밖(RUNS_DIR 옆 `build-out/verify-<배치>.{md,json}`) · 두 번 돌리면 같음.

## 시험

```bash
uv run pytest -q
uv run ruff check .
```
