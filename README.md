# clinic_chat

상담 질문 하나를 세 갈래(원장 MCP + Claude · 골드 MCP + Claude · 골드 + Jev + Claude)로 동시에 돌려 속도와 답 품질을 비교하는 로컬 실험 앱.

- 키 · 원장 · 실버 · 골드 데이터 · 실험 기록은 이 레포에 커밋하지 않는다. 값은 `.env`(git 제외)에만 둔다 — `.env.example` 참고.
- 환자 정보를 질문에 넣지 않는다.
