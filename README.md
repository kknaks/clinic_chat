# clinic_chat

상담 질문 하나를 세 갈래(원장 MCP + Claude · 골드 MCP + Claude · 골드 + Jev + Claude)로 동시에 돌려 속도와 답 품질을 비교하는 로컬 실험 앱.

- 키 · 원장 · 실버 · 골드 데이터 · 실험 기록은 이 레포에 커밋하지 않는다. 값은 `.env`(git 제외)에만 둔다 — `.env.example` 참고.
- 환자 정보를 질문에 넣지 않는다.

## 실행 안내(e2e)

전제: ① 허브(0.5.2 — `coverage` 도구 9 · 보조 입력 ③ `concern_site.csv` 를 읽음 · 윗고민 물려받음 · 자손 층 · 부위 묶임)가 `127.0.0.1:18105` 에 떠 있다(새 허브를 다른 포트로 쓰려면 실행 env 로 `HUB_REST_URL` · `HUB_GOLD_MCP_URL`) ② `.env` 에 `.env.example` 의 값이 있다(키 포함 · 값은 커밋 금지) ③ `claude` CLI 가 **구독**으로 로그인돼 있다 ④ `uv sync`.

| 하고 싶은 것 | 명령 | 결과를 보는 곳 |
|---|---|---|
| 시험 화면(브라우저 · `http://127.0.0.1:18220`) | `uv run python -m clinic_chat.web` | 화면 — 질문 · 세트 문항 → 열 카드 답 스트리밍 · B trace · 질문 기록(같은 `runs/runs.jsonl`) · 포트 18220 만 |
| 속도 탭(같은 화면 「속도」 탭) | `uv run python -m clinic_chat.web` | B · C 카드(체크로 B 전체) — 전체 ms · 첫 글자 ms · 단계별 ms 막대(Jev · 허브 · LLM) · LLM 입력 KB / 토큰 · 출력 토큰 · 추론 · `front.kinds[]` 표(종류 · Noul · 결정) · 「세트 몇 문항 한꺼번에」 표(평균 · 중앙값) |
| 한 질문 · B · C · B 전체(속도) | `uv run python -m clinic_chat.runner --question "<질문>" --columns b,c,b_full --no-b-on` | 같은 `runs.jsonl` 줄의 `columns.b_full`(단계 ① 은 B 와 같고 읽는 종류만 전부 · 싣는 칸은 0.5.1 — 0.4.1 기준선과 직접 견줄 수 없다) — **채점 대상 아님**. `runner` 에는 effort 인자가 없어 **열마다 effort 가 다르다**(B · B 전체 = `LOWEST_EFFORT` · C = `CLAUDE_CLI_EFFORT`) — **같은 조건의 속도 비교는 화면 속도 탭으로만**(effort 하나를 모든 열에 · 열 동시 실행) |
| 연결 확인 | `uv run python -m clinic_chat.batch --check` | 터미널 — 허브 · Jev · CLI · 원장 · 세트 · 답 키 · 프롬프트의 됨 / 안 됨 |
| 허브 꼴 대조(맞춤 꼴 · coverage 키 집합) | `uv run python -m clinic_chat.clients.hub_rest --ping` | 터미널 — `keys` 가 `tests/test_clients.py` `REAL_KEYS` 와 같아야 한다 |
| 한 질문 · 세 열 | `uv run python -m clinic_chat.runner --question "<질문>"` | 터미널(열마다 종료 · ms · 호출 수 · 답 글자 수 · `run_id`) · `runs/runs.jsonl` 마지막 줄 |
| 한 질문 · 답 글까지 터미널에 | `uv run python -m clinic_chat.runner --question "<질문>" --show-answers` | 터미널(답 원문) |
| 한 질문 · 한 열 | `uv run python -m clinic_chat.runner --question "<질문>" --columns b` | 같음 |
| 세트 문항 하나 | `uv run python -m clinic_chat.runner --set-id Q08` | 같음 + 기대 종료 상태 |
| 전체 세트 | `uv run python -m clinic_chat.batch --label <라벨>` | 진행 줄 · 끝에 `batch_id` |
| 일부만 | `uv run python -m clinic_chat.batch --ids Q01,Q10,Q33` · `--bundle <묶음>` · `--columns a,c` | 같음 |
| 채점(답 키를 확인한 뒤에만 돈다) | `uv run python -m clinic_chat.quality.grade --batch <batch_id>` | `runs/quality.jsonl` |
| 채점 배관만(합성 키 · 점수 아님) | `uv run python -m clinic_chat.quality.grade --plumbing` | `runs/plumbing/` |
| 답 키 뽑기(37) | `uv run python -m clinic_chat.evalset.key_calls --from-gold-build <gold-build.py>` → `uv run python -m clinic_chat.evalset.answer_key --all` | `data/eval-set/calls/<id>.json` · `answer-key/<id>.json` · `key-summary.md` |
| 추가 문항 확정(draft → confirmed) | `uv run python -m clinic_chat.evalset.loader --confirm N01,N02 --by <확인한 사람>` | `data/eval-set/set.jsonl` 의 `status` · `confirmed` |
| 추가 문항 답 키 | (확정 뒤) `uv run python -m clinic_chat.evalset.key_calls --from-set [--ids N01,N02]` → `uv run python -m clinic_chat.evalset.answer_key --ids N01,N02` | 같은 자리 · 입구는 세트 항목의 `entries[]`(없으면 `expected_entries[]`) |
| 세트 검증 | `uv run python -m clinic_chat.evalset.loader --validate` | 터미널(줄 수 · draft 수 · 오류) |
| 37 기대 입구 채움 | `uv run python -m clinic_chat.evalset.loader --fill-entries`(`key_calls --from-gold-build` 끝에도 돈다) | `set.jsonl` 의 빈 `expected_entries[]` ← 명세 `calls/<id>.json` 의 `entries`(상품 이름 길 뺌) — 보고서 「대상 ⊇ 기대 입구」 의 기대 |
| 답 키 대조(앞 판 ↔ 지금) | `uv run python -m clinic_chat.evalset.answer_key --diff <앞 판 폴더 이름> --diff-out <파일 이름>.md` → `uv run python -m clinic_chat.evalset.key_table --prev <앞 판 폴더 이름>` | `data/eval-set/<파일 이름>.md`(문항마다 같음 · 다른 칸 · 명세 해시 · 갈래 수) · `key-table.md` |
| 답 키 확인 기록 | `uv run python -m clinic_chat.evalset.answer_key --confirm <id 목록> --by <확인한 사람>` | `data/eval-set/answer-key/<id>.json` 의 `confirmed` |
| 프롬프트 확인 기록 | `uv run python -m clinic_chat.prompts --confirm --by <확인한 사람>` | `data/eval-set/prompts-confirmed.json` |
| 보고서 | `uv run python -m clinic_chat.report --batch <batch_id>` | `runs/reports/<batch_id>.md` |
| 한 줄 자세히 | `uv run python -m clinic_chat.runs --show <run_id> [--column b --trace]` | 터미널 |

**순서(처음 한 번)**: `batch --check` → (37 명세 `data/eval-set/calls/` 가 없을 때) `key_calls --from-gold-build <gold-build.py>` — `<gold-build.py>` 는 레포 밖 로컬 데이터 폴더의 `gold/gold-build.py`(경로는 각자 · 읽기만 · 실행하지 않음) → `answer_key --all`(또는 이미 있는 키) → `key-summary.md` 의 걸린 문항 확인 → `answer_key --confirm …` → 추가 문항 `loader --confirm …` → `key_calls --from-set` → `answer_key --ids …` → `answer_key --confirm …` → `prompts --confirm` → `batch --ids <몇 개>` → `quality.grade --batch <batch_id>` → `report --batch <batch_id>`.

**0.6 설정 한 줄**: `JEV_KIND_READ_AT`(`.env.example` — 임시값 0.5) = B 가 사이 값인 정보 종류를 읽는 문턱(KD10). 바꾸면 `front.kinds[].by` 가 `kd10` 인 종류가 달라진다 — 줄 머리 `settings.jev_kind_read_at` 와 trace `front.thresholds.kind_read` 에 남는다.

### 헷갈릴 자리

1. **답 글이 터미널에 안 나온다** — `runner` 요약 줄은 종료 · ms · 호출 수 · 답 글자 수뿐이다. 답은 `--show-answers` 또는 `runs --show <run_id>`(요약 줄의 `run_id`).
2. **A · C · B 켬 줄의 `end=None`** 은 정상이다 — 앱 판정(종료 상태)은 B 만 한다. A · C 가 답에서 말한 상태는 채점(②)이 읽는다(`graded_end_state`).
3. **기대 종료 상태 「상태 안 맞음」** 은 종료 상태 6 에 없는 값 — 답 키 스크립트가 기대를 못 세운 문항 표지다(`key-summary.md` 「걸린 문항」). Q12 는 기대 「원천 미확정」 인데 B 는 「근거 충족」 으로 끝난다(1회차 (다) 미판정 — 알고 간다). Q10 은 상위 고민 펼침 행을 (가) 셈에서 빼 「원천 미확정」 이 기대다.
4. **채점이 `rejected` 만 낸다** — 답 키가 전부 미확인이면 `quality.grade --batch` 가 `graded 0` 이다. `--confirm Q01,Q02,…`(쉼표 · 공백 없음) 뒤 다시. 키를 다시 뽑았거나(스크립트 · 명세가 바뀜) build 가 바뀌면 확인한 키도 거부된다 — 다시 뽑고 확인. 확인하면 키 파일 sha 가 바뀐다 — 채점에 쓴 sha 는 `quality.jsonl` 의 `answer_key_sha_graded`.
5. **「프롬프트 미확정(사용자 확인 전)」** 은 `prompts --confirm --by` 뒤 사라진다. 프롬프트 한 글자만 고쳐도 다시 뜬다.
6. **보고서**는 열별 두 벌(전체 / 원장 = silver) · 범위별 · 상태별 · B 전용 · 추론 끔/켬 표다. 줄 하나를 깊이 보려면 `runs --show <id> --column b --trace`.
7. **B 「실행 중단」 의 까닭** — 터미널엔 `error=있음` 만. 까닭은 `runs --show <id> --column b --trace` 의 `end_reason`(`gate:K3` · `hub_breadth` · `C3` · `FT5` …). 어느 조회에도 안 쓴 입구는 trace `entry.unused[]`(까닭 `br1_no_attr_filter` · `range_start_tables`).
8. **draft 문항** — `--set-id N01` 은 거부되고 batch 는 「draft 건너뜀 N」 을 찍는다. `loader --confirm` 으로 확정한다.
9. **B 켬 — CLI · batch 는 기본이 켬 · 화면은 기본이 끔(고르면 켬 — O-26 ①)** — `batch` 는 `--b-on` / `--no-b-on` 둘 다 받고, `runner` 에는 `--b-on` 이 없다(늘 켬 · 끄려면 `--no-b-on` 만). 전체 세트는 문항 × CLI 4 회(A · B · B 켬 · C) + 채점 문항 × 4 회(Opus)라 길다 — `--ids` 몇 개부터. `--columns` 는 `a,b,c,b_full` 중 소문자 · 쉼표(기본 `a,b,c`) — 모르는 이름이면 멈춘다(B 켬은 열 이름이 아니다 — 위 `--no-b-on` 으로만 끈다). CLI 로 돌리면 effort 가 열마다 다르다(B `LOWEST_EFFORT` · C `CLAUDE_CLI_EFFORT`) — 같은 조건 비교는 속도 탭.
10. **단계 ⑤ 「추론 끔」 은 실제로는 가장 낮은 effort(`low`)** 다 — CLI 가 추론을 끄지 못한다. 보고서 머리에 그렇게 찍힌다.
11. **B 의 길(0.5.1)** — ① 맨 앞 판단: `resolve`(질문 그대로 · 부분 일치 전부) → `coverage`(후보마다 종류 × 층 셈) → Jev 한 요청(`target_{i}` · `kind_{k}` 14 · `gap`) → 대상(덮이지 않은 일치는 Noul 과 무관하게 대상 — `named`) · 읽는 종류(yes · 1위 · 입구 지정 · 범위 기본 묶음) · 범위(이름표). ② 고른 대상 × 읽는 종류만 끝까지(하위 층 자기 행 포함). ④ 재료 = 행 전부 · 그 종류의 싣는 칸만 · 줄 번호(`trace.verified_lines`). required 는 RQ1(자기 → 하위 → 물려받음)로 — `trace.front.required[].met_by`. 답 꼬리(`<<<근거>>>` 뒤 「답 줄: 재료 줄」)는 앱이 떼어 `trace.answer_lines` 로. 옛 「B 선별」 열은 없어졌다(새 B 가 선별이다). `--columns` 는 `a,b,c,b_full` 을 받는다(기본 `a,b,c`). 옛 줄(0.4.1 — `front` 가 없는 줄)도 `runs --show` · 화면 「기록 다시 보기」 로 열린다(없는 필드는 비움).

- 자유 질문(`--question`)은 `set_id = null` 이라 채점되지 않는다(기록 · 속도에만 남음).
- `status: draft` 문항(사용자 확정 전)은 batch 가 건너뛴다.
- 전체 세트는 문항 × (A + B + B 켬 + C) 의 CLI 호출이라 오래 걸린다 — 처음에는 `--ids` 로 몇 문항만.
- 프롬프트를 사용자가 확인하기 전에는 결과에 「프롬프트 미확정(사용자 확인 전)」 이 찍힌다.

검증: `uv sync` · `uv run ruff check .` · `uv run pytest -q`(모의만 — 키 · 허브 · 구독 없이 통과 · skip 1 = 아래 데이터 env 판). 데이터 env 판: `GOLD_CSV_DIR=<로컬 gold CSV 폴더> uv run pytest -q tests/test_walk_ranges.py` — 「LLM 에 싣는 칸」(§3-2a) 이 gold CSV 헤더에 다 있는지 본다(env 가 없으면 이 시험 하나가 skip).

## 상담 화면(SPEC-004 0.1.0 · WORK-011)

여러 턴 상담 1판 — 왼쪽 위 얼굴(부위 점) · 왼쪽 아래 시술 리스트 · 오른쪽 채팅. 걷기는 시험 앱 B 와 같은 `walk()` 이고, 리스트 · 점은 LLM 답보다 먼저 그린다. 화면 문구는 아직 없다(빈 자리 `data-slot` — 사용자가 채운다).

| 하고 싶은 것 | 명령 | 결과를 보는 곳 |
|---|---|---|
| 상담 화면(`http://127.0.0.1:18222`) | `uv run python -m clinic_chat.consult`(다른 포트 `--port N`) | 화면 — 새 대화 `/`(→ `303 /c/<새 id>` · 새로 고침 · 서버 재시작 뒤에도 같은 대화) · 그 대화 `/c/<conv_id>` |
| 좌표 확인 | 같은 서버의 `/face-check` | 좌표표의 점 전부 + 부위 코드(어긋난 코드를 알려 주면 그 코드의 좌표만 고친다) |
| 빈 화면 확인(외부 호출 0) | `CHAT_FAKE_TURN=1 uv run python -m clinic_chat.consult --port <빈 포트>` | 가짜 턴 — `step` → `list` → `sites` → `delta` → `done`(질문에 `error` 가 들면 `turn_error`) |
| CLI 실측(판을 올릴 때마다) | `uv run python -m clinic_chat.consult.probe --e1` · `--e2` · `--e3` | 터미널 JSON · `CHAT_DIR/probe/<시각>.json` |

- **전제**: 허브가 `HUB_REST_URL`(기본 `127.0.0.1:18105`)에 떠 있다 · `.env` 에 Jev 키 · `claude` CLI 가 **구독**으로 로그인돼 있다.
- **대화 글은 사용자 홈의 Claude CLI 세션 파일(CLI 설정 폴더의 프로젝트 기록 — `CLAUDE_CONFIG_DIR` 를 쓰면 그 아래)에 평문으로 남는다. 앱은 그 파일을 지우지 않는다.** CLI 는 레포 밖 고정 폴더(`CHAT_CLI_CWD` — 기본 = 사용자 캐시 폴더 아래 `clinic-chat/consult-cli`)에서 뜬다.
- **프롬프트(`prompts/consult.json` — `version` 이 `-draft` 로 끝나면 사용자 확인 전)를 고쳤으면 새 대화로 본다.** 대화는 시작할 때 읽은 프롬프트 보관본(`CHAT_DIR/prompts/<sha256>.json`)으로 끝까지 돈다 — 장부 `prompts.changed` 가 고친 것을 표지만 한다.
- 장부 = `CHAT_DIR/turns.jsonl`(기본 `runs/consult/` — gitignore) · 턴마다 시작 줄 · 맺는 줄. 맺는 줄 없는 턴이 있는 대화 · 세션이 깨진 대화 · 허브 `gold_hash` 가 바뀐 대화 · 턴 한도(`CHAT_MAX_TURNS`)에 닿은 대화는 더 받지 않는다(새 대화).
- 고민 부위 점은 허브가 `concern_candidates` 머리에 부위 칸을 낼 때 켜진다(SPEC-001 0.7 — 칸 이름은 `clinic_chat/consult/sites.py` 의 `SITES_FIELD` 한 줄). 그 전에는 점 0 · 라벨 0 으로 돈다.

### 상담 0.2 — 상담 객체(SPEC-005 0.1.0 · WORK-012)

대화마다 「상담 객체」(고민 · 부위 · 관심 대상 · 닿는 시술 · 바람 · 보여 준 것 · 물은 것)를 두고 턴마다 갱신한다. 객체의 빈 칸 하나를 답 끝의 질문 하나로 잇고(선택지는 전부 허브 응답의 값), 관심 대상과 고민이 둘 다 잡히면 시너지(`g_pair` 「보완」)를 묻지 않아도 리스트 칸에 보인다.

| 손잡이(env) | 기본 | 뜻 |
|---|---|---|
| `CHAT_STATE` | `off` | `on` 이면 상담 객체로 돈다 · `off` = 0.1 그대로. **`.env` 에 두지 말고 프로세스 env 로만 켠다** — `CHAT_STATE=on uv run python -m clinic_chat.consult` |
| `CHAT_ASK_MAX` | `2` | 같은 빈 칸(종류 + 대상)을 주는 횟수 한도 |
| `CHAT_SYNERGY` | `on` | 시너지 먼저(켠 대화에서만) |
| `CHAT_RETRACT` | `on` | 거둠 판단(「그건 아니고」 — Jev 요청 하나를 걷기와 함께 · 켠 대화에서만) |

- **대화의 켠 / 끈 상태는 첫 턴의 값으로 끝까지 간다**(장부 시작 줄 `consult_state`). 서버를 다른 값으로 다시 띄워도 이미 시작한 대화는 그대로이고, 그 칸이 없는 대화(0.1 로 쓴 대화)는 끈 상태로 돈다. 켠 대화는 `prompts/consult.json` 에 0.2 키(`system_state` · `jev_front.target_option` · `jev_state.retract` · `turn_headers.consult`)가 다 있어야 시작한다.
- 프롬프트를 고쳤으면 새 대화로 본다(0.1 그대로 — 보관본으로 끝까지).
- 장부 맺는 줄의 `consult` 칸: `diff`(이번 턴 객체 변화) · `after`(턴 뒤 객체 전체 — 서버를 다시 띄우면 마지막 성공 줄의 이것으로 객체를 세운다) · `blanks` · `ask` · `options_carried`(걸린 선택지의 Jev 판정) · `retract`(거둠 판정 · `retract_ms`) · `queries`(객체 기준 조회 — `rule` · `total` · `ms` · `skipped: "walk"`). 끈 대화의 줄에는 `consult` 칸이 없다.
- 실측기: `CHAT_STATE=on uv run python -m clinic_chat.consult.probe --e5 [--only ex1,ex2]` — 입력 글은 `clinic_chat/consult/e5_inputs.json` · 장부 `CHAT_DIR/e5/turns.jsonl` · 결과 `CHAT_DIR/probe/<시각>.json`.
- 서버에 한글을 `curl` 로 보낼 때는 `curl --data-urlencode "text=…"` 를 쓴다(`-d` 로 그대로 보내면 글자가 깨진다) · 보낸 뒤 장부 시작 줄의 `question` 이 보낸 글과 같은지 본다.

### 상담 0.3 — 턴 종류 · 상담 말투 · 끝맺음 하나 · 화면 네 칸(SPEC-006 0.1.0 · WORK-014)

켠 대화(장부 시작 줄 `consult_rev: 3`)는 0.3 판으로 돈다 — 둘째 턴부터 걷기와 함께 턴 종류(`new` · `follow` · `talk`)를 Jev 로 가르고, `talk`(되물음 · 불만 · 맞장구)는 검색 없이 앞 대화로 답한다. 시스템 글은 `prompts/consult.json` 의 `system_v3`(초안)이고 답 끝은 앱이 고른 `close` 하나다. 끈 대화는 0.1 그대로 돈다. **0.2 로 시작한 켠 대화(`consult_rev` 없음)는 0.3 서버에서 끝난 대화다** — 열면 표지(`conv-outdated`)와 새 대화 길이 보이고 새 턴은 받지 않는다(장부 줄을 쓰지 않는다).

| 손잡이(env) | 기본 | 뜻 |
|---|---|---|
| `CHAT_TURN_KIND` | `on` | `off` = 가르지 않는다(모든 턴 `new` · 걷기는 0.2 와 같은 호출 순서) |
| `CHAT_TALK_MAX` | `3` | 잇단 `talk` 한도 — 넘으면 다음 턴은 가르지 않고 `new` |
| `CHAT_ALT_MAX` | `3` | 없다고 말한 뒤 잇는 다른 계열 수(`close.by` = `alt`) |
| `CHAT_VOICE_CHECK` | `on` | 금지 낱말 검사(`voice_check[]` — 장부 `consult.voice` 에 기록만 · 답은 안 고침) |
| `CHAT_DEBUG_PANEL` | `off` | 상담 객체 패널(개발용 — 턴마다 장부 `consult.after` · `diff` · `blanks` · `ask` · `turn_kind` · `close` 값 그대로) |
| `CHAT_DRAWER_MAX` | `50` | 드로어에 그리는 지난 대화 수 |

- 화면 = 네 칸: 드로어(기본 열림 · 「새 대화」 = `/` · 지난 대화 목록 = 마지막 턴 시각 내림차순 · `GET /convs` 조각) | 얼굴 + 점(위) · 시술 리스트(아래) | 채팅 | 객체 패널(`CHAT_DEBUG_PANEL=on` 일 때만). 드로어 목록은 서버가 뜰 때 장부를 한 번 훑은 메모리 색인이다.
- 얼굴 점 = 대화에 나온 고민 전부의 고민 부위(누적). 점 · 라벨을 누르면 `GET /c/<id>/list?concern=<코드>` 로 그 고민의 리스트 조각을 받고(허브만 · 장부 줄 없음), 다음 턴은 그 고민을 기준으로 답한다(폼 값 `viewing` · 한 턴만).
- 리스트는 고민 · 계열 · 시술 이름만 그린다 — 층 배지 · 「그 밖」 · 메뉴 · 패키지 이름은 장부 `list.hidden` 에만. 허브가 내려 준 시술(`descended[]`)은 계열 묶음 머리에 표지(`prompts/consult.json` `slots.mark-modality-level`)로 묶는다. 부위로 거르지 않은 응답(`site_applied` = `prefer`)이면 표지 시술을 앞에 둔다.
- 장부 맺는 줄 `consult` 칸에 더함: `turn_kind{kind, nouls, rule, requests, ms, error, walks[]}` · `follow_target` · `empty[]` · `close{by, alt, offered, said}` · `voice` · `viewing`. 답 꼬리의 `close: <답 줄>` 한 줄은 장부 `close.said` 로만 간다(화면 0).
- 실측기: `CHAT_STATE=on uv run python -m clinic_chat.consult.probe --e6 [--only c1_e3f90,sp_cheek]` — 입력 글은 `clinic_chat/consult/e6_inputs.json` · 장부 `CHAT_DIR/e6/turns.jsonl` · 결과 `CHAT_DIR/probe/<시각>.json`.
- 이 판은 **로컬 한 사람 전제**다 — 드로어에 손님 질문 원문이 보인다. 여러 사람이 쓰기 전에 대화의 주인을 가르는 길이 먼저 서야 한다.

### 상담 0.4 — 상담 입구 소넷 전환(SPEC-008 0.2.0 · WORK-017)

- 켠 상태 턴 = 목록 적재(허브 `resolve` 목록 꼴 · `gold_hash` 마다 프로세스 캐시) → **소넷 이해 호출 하나**(`claude -p --no-session-persistence --output-format json` · 답 실행기와 같은 격리 인자 · 턴 메시지는 stdin) → 검증(목록에 없는 코드 버림) → 객체 → 조회(부위 거르개 = 이번 턴 부위만) · 답 재료(걷기의 결정론 함수) → 화면 → 답(0.3 그대로). 걷기 `walk()` · Jev · `resolve` 맞춤 꼴 · 거둠 요청은 켠 턴에서 부르지 않는다.
- `CHAT_STATE` 기본 `on` · `off` 를 주어도 새 대화는 켠 상태로 연다. 옛 끈 대화 · rev 3 대화는 열기만 되고 새 턴은 `rev_changed` 로 막힌다(막는 자리 `data-slot="conv-off-closed"` — 문구 빈칸).
- 설정 아홉: `CHAT_PICK_MODEL`(sonnet) · `CHAT_PICK_EFFORT`(low) · `CHAT_PICK_THINKING`(off = `--settings {"alwaysThinkingEnabled": false}` + `MAX_THINKING_TOKENS=0` — CLI 2.1.322 실측에서 20 회 가운데 4 ~ 5 회는 그래도 thinking 이 났다 · 완전히 끄는 것은 SDK 때) · `CHAT_PICK_TIMEOUT_SEC`(10) · `CHAT_PICK_ALIASES`(on) · `CHAT_PICK_LIST_CACHE`(on) · `CHAT_PICK_CONTEXT_TURNS`(2) · `CHAT_PICK_ANSWER_CHARS`(200) · `CHAT_PICK_LIST_MAX`(30).
- 프롬프트 `prompts/consult.json` 의 `pick` 절(초안 `0.4.0-draft` — 사용자 확인 대상). 장부 `consult.understand` · `consult.site_filter` · `consult.site_index`(색인 지은 턴만) · `consult.turn_kind{kind, from}` · 패널에 같은 키.
- 실측기: `uv run python -m clinic_chat.consult.e8 --e8-0 <conversations.jsonl> --n 20 --mode a` — thinking 끄는 꼴 후보별 20 회(허브 GET 만).

벤더링한 파일(`clinic_chat/consult/static/` — 고치지 않는다 · CENTURION_DAY `backend/day/static/` dev `c5045a99` 사본):

| 파일 | 판 | 라이선스 | sha256 |
|---|---|---|---|
| `htmx.min.js` | htmx 2.0.4(본 `base.html` 의 `?v=2.0.4`) | MIT — `HTMX-LICENSE.txt` | `e209dda5c8235479f3166defc7750e1dbcd5a5c1808b7792fc2e6733768fb447` |
| `htmx-ext-sse.js` | htmx SSE 확장 2.2.4(파일 안에 판이 없다 — 본 `base.html` 의 `?v=2.2.4`) | BSD Zero Clause — `HTMX-EXT-SSE-LICENSE.txt` | `3b5992a541619babefc4c169505af474df5c3039da51e59b96ccf9241ecd61d2` |
| `PretendardVariable.woff2` | Pretendard Variable | SIL OFL 1.1 — `PRETENDARD-LICENSE.txt` | `9599f12fd42fc0bce1cd50b47a0c022e108d7aa64dd0d1bb0ed44f3282d900b4` |
| `medikit.css` | medikit × HTMX 키트(파일 머리: medikit 1.3.0 값) | `MEDIKIT-NOTICE.txt`(디자이너 전체 공개 배포 — 사용자 확인 2026-11-17) | `37ee05c8d6331facdb9ffe46689cbaaf14a54f6bc5cbab68946c3aa00514c772` |
| `medikit.js` | medikit × HTMX 공통 동작 | 같음 | `bf63fd2490b104a726fa232caf9827975771edf0acb8220cbd1d637bf8ecae40` |

라이선스 파일 sha256: `HTMX-LICENSE.txt` `23410c2d962ac773ec1bf4137debd7cd0d914a3bb654aff9250dde7c278c3da1` · `HTMX-EXT-SSE-LICENSE.txt` `a17d224ce9afdc21ec9491b76a2f5d036d19aa641ac4f85ca29c21f9f7e54fdf` · `PRETENDARD-LICENSE.txt` `b04538c9abec39a3db75108cf0af0fd9c77032fe8aa2cf38345b4d250e98e38e`.
