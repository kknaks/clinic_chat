<!-- 단계 ①-2 맨 앞 판단 — 한 요청에 `target_{i}`(후보마다) · `kind_{k}`(14) · `gap`(1). 영어 instructions(D10). KD8: 가리키는 것의 이름을 글자로 함께 적는다. `{i}` · `{k}` = 색인 · `{name}` · `{id}` = 앱 · 허브가 gold 에서 낸 이름 · `표:코드`. 초안 — e2e 보고 때 사용자 확인(C-1) -->
## target
Does `question` ask about the candidate "{name}" (`candidates[{i}]`, {id}) — the thing itself, not a word that merely contains its name? `candidates[{i}].rows` tells where data exists (own / lower layer / inherited); a candidate with only lower-layer rows can still be the thing asked about.
## kind
Is the information kind "{name}" (`kinds_catalog[{k}]`) needed to answer `question`? Say yes only if the question asks for this kind of information; do not count information that would merely be nice to have.
## gap
Does `question` ask for something none of `kinds_catalog` covers (for example ranking or comparison of options, literature, translation, popularity, or price range by age)?
