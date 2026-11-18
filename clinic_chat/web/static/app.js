// 시험 화면(WORK-003) — 서버 SSE(start · delta · step · done · end)를 열 카드로 그린다. 빌드 없는 정적 페이지.
// 끝나면 카드 답은 기록(runs)의 최종 답으로 갈아 끼운다 — 스트리밍 글은 미리 보기일 뿐.
// 걷기 · 답 · 판정은 서버의 runner 가 한다 — 여기는 받은 값을 보여 주기만.
(function () {
  const TITLE = { a: "A 원장", b: "B gold+Jev", b_on: "B 켬", c: "C gold MCP" };
  const ORDER = ["a", "b", "b_on", "c"];
  const STEPS = ["①", "②", "③", "④", "⑤"];
  const $ = (id) => document.getElementById(id);
  let meta = {};
  let cards = {};
  let row = null;
  let quality = [];
  let es = null;

  function el(tag, attrs, ...kids) {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (k === "class") e.className = v;
      else if (k === "on") for (const [ev, f] of Object.entries(v)) e.addEventListener(ev, f);
      else if (v !== null && v !== undefined && v !== false) e.setAttribute(k, v === true ? "" : v);
    }
    for (const k of kids.flat()) if (k !== null && k !== undefined) e.append(k.nodeType ? k : document.createTextNode(String(k)));
    return e;
  }
  const ms = (x) => (typeof x === "number" ? `${Math.round(x)} ms` : "–");
  const pre = (o) => el("pre", { class: "mini" }, JSON.stringify(o ?? null, null, 2));
  const sec = (label, ...kids) => el("details", {}, el("summary", {}, label), ...kids);

  async function getJson(url, opts) {
    const r = await fetch(url, opts);
    const body = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(body.detail || `${r.status}`);
    return body;
  }

  // ---- 상단 고정 표시
  async function loadMeta() {
    try { meta = await getJson("/api/meta"); } catch (e) { meta = {}; }
    const p = $("pin-prompts");
    p.hidden = !meta.prompts_mark;
    p.textContent = meta.prompts_mark || "";
    $("pin-reasoning").textContent = `추론 끔 = effort ${meta.lowest_effort || "–"}`;
    $("pin-gold").textContent = `gold_hash ${(meta.gold_hash || "–").slice(0, 8)}`;
  }

  // ---- 세트 문항
  async function loadSet() {
    let items = [];
    try { items = (await getJson("/api/set")).items || []; } catch (e) { items = []; }
    const sel = $("set-id");
    for (const it of items) {
      const draft = it.status === "draft";
      sel.append(el("option", { value: it.id, disabled: draft, title: it.question || "" },
        `${it.id} · ${it.range || "–"} · ${it.status || "–"}`));
    }
    sel.addEventListener("change", () => {
      const it = items.find((x) => x.id === sel.value);
      $("q").value = it ? it.question || "" : "";
    });
    // 세트 문장을 고치면 자유 질문 — set_id 를 뗀다(세트를 다시 고르면 세트 질문)
    $("q").addEventListener("input", () => { sel.value = ""; });
  }

  // ---- 열 카드
  function newCard(col) {
    return { col, answer: "", memo: [], step: null, stepsSeen: new Set(), done: null, effort: null, model: null,
      started: false, dom: null };
  }

  function callsLine(c) {
    const k = c || {};
    return `llm ${k.llm_round_trips ?? "–"} · tool ${k.tool_calls ?? "–"} · hub ${k.hub_calls ?? "–"} · jev ${k.jev_calls ?? "–"}`;
  }

  function traceView(t) {
    if (!t) return null;
    const entry = t.entry || {};
    const fr = t.front || {};  // 옛 줄(front 없음 · range_choice 있음)은 비워 둔다
    const breadth = t.breadth || [];
    const rowsRead = breadth.reduce((a, b) => a + (b.read || 0), 0);
    const btab = el("table", { class: "mini" },
      el("thead", {}, el("tr", {}, ["tool", "kind", "purpose", "range", "total", "read", "pages", "complete", "error"].map((h) => el("th", {}, h)))),
      el("tbody", {}, breadth.map((b) => el("tr", {}, [b.tool, [].concat(b.kind || []).join(","), b.purpose ?? "",
        [].concat(b.range || []).join(","), b.total, b.read, b.pages, String(b.complete),
        b.error ? JSON.stringify(b.error) : ""].map((v) => el("td", {}, v ?? ""))))));
    const gates = t.gates || {};
    return sec("trace",
      el("div", { class: "muted" }, `end_reason ${t.end_reason ?? "–"}`),
      sec("front(대상 · 종류 · gap · 범위 · required)", pre({ targets: fr.targets, dropped: fr.dropped, kinds: fr.kinds,
        kinds_by: fr.kinds_by, gap: fr.gap, scope_gap: fr.scope_gap, ranges: fr.ranges,
        required: (fr.required || []).map((r) => ({ kind: r.kind, target: r.target, met_by: r.met_by })),
        picked: entry.picked, overlaps: entry.overlaps, entry_pick: t.entry_pick })),
      el("div", { class: "muted" }, `verified_lines ${(t.verified_lines || []).length} · answer_lines ${t.answer_lines ? "있음" : "없음"}`),
      sec(`읽은 폭(도구 호출 · 행 수) — ${breadth.length} · ${rowsRead}`, el("div", { class: "table-wrap" }, btab)),
      sec("깊이 판단(Noul 결정)", pre({ depth_decisions: t.depth_decisions, steps: t.steps, required: t.required,
        depth_reads: t.depth_reads })),
      sec(`검사(gate) 결과 — ${gates.ok === undefined ? "–" : gates.ok ? "ok" : (gates.fails || []).join(",")}`, pre(gates)),
      sec(`entry.unused[] — ${(entry.unused || []).length}`, pre(entry.unused || [])),
    );
  }

  // 카드 노드는 한 번만 만든다 — delta 는 답 칸에 붙이기만, 나머지는 머리 · 시간 · 단계 · 오류 · trace 칸만 다시 그린다
  function mount(c) {
    if (c.dom) return c.dom;
    const dom = { head: el("h3", {}), timing: el("div", { class: "timing" }), steps: el("div", { class: "steps" }),
      err: el("div", { class: "err" }), memo: el("div", { class: "memo", title: "도구 호출 사이 글 — 답 · 채점에 안 들어감" }),
      ans: el("div", { class: "answer" }, c.answer), extra: el("div", {}) };
    dom.node = el("div", { class: `col ${c.col}` }, dom.head, dom.timing, c.col === "b" ? dom.steps : null,
      dom.err, dom.memo, dom.ans, dom.extra);
    c.dom = dom;
    setMemo(c, c.memo);
    return dom;
  }

  // 메모 줄 — 도구 호출 앞 글(답 칸에 흐르던 글을 도구 호출이 오면 여기로 옮긴다 · 끝난 뒤는 기록 trace.interim_text)
  function setMemo(c, list) {
    c.memo = list || [];
    const n = c.dom && c.dom.memo;
    if (!n) return;
    n.hidden = !c.memo.length;
    n.textContent = c.memo.length ? `메모 · ${c.memo.join(" ⏵ ")}` : "";
  }

  function paint(c) {
    const dom = mount(c);
    const d = c.done;
    const end = d && d.end_state;
    dom.head.replaceChildren(...[TITLE[c.col],
      !d && c.started ? el("span", { class: "badge" }, "도는 중") : null,
      !d && !c.started ? el("span", { class: "badge muted" }, "대기") : null,
      end ? el("span", { class: "badge ok" }, end) : null,
      d && d.error ? el("span", { class: "badge bad" }, "오류") : null].filter((x) => x));
    dom.timing.replaceChildren(
      el("span", { class: "lead", title: "누른 순간→첫 답 글자(마지막 도구 호출 뒤 · 메모 뺌)" }, ms(d && d.click_to_first_answer_ms)),
      el("span", {}, `전체 ${ms(d && d.total_ms)}`),
      el("span", {}, `첫 답 글자 ${ms(d && d.first_answer_text_ms)}`),
      el("span", { title: "메모 포함 첫 글자 — 열 시작 기준 / 누른 순간 기준" },
        `첫 글자(메모 포함) ${ms(d && d.ttft_ms)} / ${ms(d && d.click_to_first_text_ms)}`),
      el("span", {}, callsLine(d && d.calls)),
      el("span", {}, (d && d.model) || c.model || "–"),
      el("span", {}, `effort ${(d && d.effort) || c.effort || "–"}`));
    if (c.col === "b") {
      dom.steps.replaceChildren(...STEPS.map((s) =>
        el("span", { class: c.step === s ? "on" : c.stepsSeen.has(s) ? "" : "muted" }, s)));
    }
    dom.err.hidden = !(d && d.error);
    dom.err.textContent = d && d.error ? JSON.stringify(d.error) : "";
    const extra = [];
    if (c.col === "b" && row) extra.push(traceView(((row.columns || {}).b || {}).trace));
    const q = quality.map((x) => (x.columns || {})[c.col]).filter((x) => x);
    if (q.length) extra.push(sec("채점", pre(q)));
    dom.extra.replaceChildren(...extra.filter((x) => x));
  }

  // 사용자가 위로 올려 읽는 중이면 내리지 않는다
  const atBottom = (n) => n.scrollHeight - n.scrollTop - n.clientHeight < 8;

  function appendAnswer(c, text) {
    if (!text) return;
    c.answer += text;
    const n = mount(c).ans;
    const stick = atBottom(n);
    n.append(text);
    if (stick) n.scrollTop = n.scrollHeight;
  }

  function setAnswer(c, text) {
    c.answer = text;
    const n = mount(c).ans;
    const stick = atBottom(n);
    n.textContent = text;
    if (stick) n.scrollTop = n.scrollHeight;
  }

  // 카드 자리(A · B · B 켬 · C) — 바뀐 때만 다시 놓는다
  function layout() {
    const box = $("cols");
    const nodes = ORDER.filter((k) => cards[k]).map((k) => mount(cards[k]).node);
    box.style.setProperty("--n", Math.max(1, nodes.length));
    const same = nodes.length === box.children.length && nodes.every((n, i) => box.children[i] === n);
    if (!same) box.replaceChildren(...nodes);
  }

  function render() {
    layout();
    for (const k of ORDER) if (cards[k]) paint(cards[k]);
  }

  function current(text) { $("current").textContent = text || ""; }

  // ---- 보내기 · SSE
  async function send(ev) {
    ev.preventDefault();
    const columns = [...document.querySelectorAll("input[name=col]:checked")].map((x) => x.value);
    const bOn = $("b-on").checked;
    const sid = $("set-id").value;
    const q = $("q").value.trim();
    const body = sid ? { set_id: sid, columns, b_on: bOn } : { question: q, columns, b_on: bOn };
    if (!columns.length || (!sid && !q)) return;
    $("send").disabled = true;
    if (es) es.close();
    row = null; quality = [];
    try {
      const r = await getJson("/api/ask", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body) });
      cards = {};
      for (const k of r.columns) cards[k] = newCard(k);
      if (r.b_on) cards.b_on = newCard("b_on");
      current(`${r.run_id}${r.set_id ? " · " + r.set_id : ""} · ${r.question || ""}`);
      render();
      listen(r.run_id);
    } catch (e) {
      current(String(e.message || e));
      $("send").disabled = false;
    }
  }

  function listen(runId) {
    es = new EventSource(`/api/runs/${runId}/events`);
    let seen = -1;  // 받은 이벤트 번호 — 다시 붙어 같은 번호가 오면 버린다(답 중복 0)
    const on = (kind, f) => es.addEventListener(kind, (e) => {
      const n = Number(e.lastEventId);
      if (Number.isInteger(n) && e.lastEventId !== "") { if (n <= seen) return; seen = n; }
      f(JSON.parse(e.data));
    });
    const card = (k) => {
      if (!cards[k]) { cards[k] = newCard(k); layout(); }
      return cards[k];
    };
    on("start", (d) => { const c = card(d.column); c.started = true; c.effort = d.effort; paint(c); });
    on("delta", (d) => { appendAnswer(card(d.column), d.text || ""); });
    on("interim", (d) => { const c = card(d.column); setMemo(c, [...c.memo, d.text || c.answer]); setAnswer(c, ""); });
    on("step", (d) => { const c = card(d.column); c.step = d.step; c.stepsSeen.add(d.step); paint(c); });
    on("done", (d) => {
      if (!d.ran) { delete cards[d.column]; layout(); return; }
      const c = card(d.column); c.done = d; paint(c);
    });
    on("end", async (d) => {
      es.close(); es = null;
      $("send").disabled = false;
      if (d.error) current(`${runId} · ${JSON.stringify(d.error)}`);
      await openRun(runId, false);
      loadRuns();
    });
    es.onerror = () => { if (es && es.readyState === EventSource.CLOSED) $("send").disabled = false; };
  }

  // ---- 기록 다시 보기
  function effortOf(r, k) {
    const st = r.settings || {};
    if (k === "b") return st.answer_reasoning === "on" ? st.effort : st.lowest_effort;
    return st.effort;
  }

  async function openRun(runId, rebuild = true) {
    let got;
    try { got = await getJson(`/api/runs/${runId}`); } catch (e) { current(String(e.message || e)); return; }
    row = got.row; quality = got.quality || [];
    if (rebuild) {
      cards = {};
      for (const k of ORDER) {
        const c = (row.columns || {})[k];
        if (!c) continue;
        const card = newCard(k);
        card.answer = c.answer || "";
        card.memo = ((c.trace || {}).interim_text) || [];
        card.done = { column: k, ran: true, end_state: c.end_state, total_ms: (c.timing || {}).total_ms, ttft_ms: (c.timing || {}).ttft_ms,
          click_to_first_text_ms: (c.timing || {}).click_to_first_text_ms,
          click_to_first_answer_ms: (c.timing || {}).click_to_first_answer_ms,
          first_answer_text_ms: (c.timing || {}).first_answer_text_ms,
          calls: c.calls, model: (row.models || {}).answer, effort: effortOf(row, k), error: c.error };
        if (k === "b") STEPS.forEach((s) => card.stepsSeen.add(s));
        cards[k] = card;
      }
      current(`${row.run_id}${row.set_id ? " · " + row.set_id : ""} · ${row.question || ""}`);
      layout();
    } else {
      // 끝난 뒤 카드 답 = 기록의 최종 답(쪽 구분 · 오류 때 부분 답 정리) · 종료 상태 · 오류도 기록 쪽
      for (const k of ORDER) {
        const c = cards[k], rc = (row.columns || {})[k];
        if (!c || !rc) continue;
        setAnswer(c, rc.answer || "");
        setMemo(c, ((rc.trace || {}).interim_text) || []);
        c.done = { ...(c.done || { column: k, ran: true, model: (row.models || {}).answer, effort: effortOf(row, k) }),
          end_state: rc.end_state, total_ms: (rc.timing || {}).total_ms, ttft_ms: (rc.timing || {}).ttft_ms,
          click_to_first_text_ms: (rc.timing || {}).click_to_first_text_ms,
          click_to_first_answer_ms: (rc.timing || {}).click_to_first_answer_ms,
          first_answer_text_ms: (rc.timing || {}).first_answer_text_ms, calls: rc.calls, error: rc.error };
      }
    }
    render();
  }

  async function loadRuns() {
    let runs = [];
    try { runs = (await getJson("/api/runs?limit=50")).runs || []; } catch (e) { runs = []; }
    const tb = document.querySelector("#runs tbody");
    tb.replaceChildren(...runs.map((r) => {
      const cell = (k) => {
        const c = (r.columns || {})[k];
        if (!c) return el("td", {}, "–");
        return el("td", { class: c.error ? "err" : "" }, `${ms(c.total_ms)}${c.end_state ? " · " + c.end_state : ""}`);
      };
      return el("tr", { on: { click: () => openRun(r.run_id) } },
        el("td", {}, r.run_id), el("td", {}, (r.asked_at || "").replace("T", " ").slice(0, 19)),
        el("td", {}, r.set_id || "–"), el("td", { class: "qcell" }, r.question || ""),
        cell("a"), cell("b"), cell("b_on"), cell("c"), el("td", {}, r.graded ? "✓" : "–"));
    }));
  }

  $("ask").addEventListener("submit", send);
  loadMeta(); loadSet(); loadRuns();
})();
