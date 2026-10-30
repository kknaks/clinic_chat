// 화면 — SSE 계약(SPEC-006 §4-3)을 state.js 로 줄여 3열을 그린다. 빌드 없는 정적 페이지(R-1).
(function () {
  const S = window.ClinicState;
  const TITLE = { case1: "case1 원장 MCP", case2: "case2 골드 MCP", case3: "case3 골드 + Jev" };
  const ITEMS = { accuracy: "정확성", safety: "안전 정보", candidates: "후보 처리", uncertainty: "불확실 표시", on_topic: "질문에 답함" };
  let disabled = {};
  let cur = S.initial();
  let lastRunId = null;
  let es = null;
  const $ = (id) => document.getElementById(id);

  function el(tag, attrs, ...kids) {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (k === "class") e.className = v; else if (k === "on") for (const [ev, f] of Object.entries(v)) e.addEventListener(ev, f);
      else if (v !== null && v !== undefined) e.setAttribute(k, v);
    }
    for (const k of kids.flat()) if (k !== null && k !== undefined) e.append(k.nodeType ? k : document.createTextNode(String(k)));
    return e;
  }
  const ms = (x) => (typeof x === "number" ? `${Math.round(x)} ms` : "–");
  const stageClass = (n) => (n.startsWith("claude") ? "s-claude" : n.startsWith("tool:") ? "s-tool" : n === "jev" ? "s-jev" : "s-hub");

  function stageBar(c, scale) {
    const bar = el("div", { class: "stagebar" });
    for (const st of c.stages) {
      const a = st.start_ms || 0, b = st.end_ms == null ? a : st.end_ms;
      bar.append(el("span", { class: stageClass(st.name), title: `${st.name} ${ms(a)} → ${ms(b)}`,
        style: `left:${(a / scale) * 100}%;width:${Math.max(((b - a) / scale) * 100, 0.4)}%` }));
    }
    return bar;
  }

  function traceView(name, c) {
    const t = c.trace || {};
    if (name === "case3") {
      const j = t.jev || {}, h = t.hub || {};
      const rows = [];
      if (j.intent) rows.push(el("li", { class: j.intent.low_confidence ? "low" : "" },
        `의도 ${j.intent.choice} · conf ${j.intent.confidence}${j.intent.low_confidence ? " ⚠ 낮음" : ""}`));
      for (const [k, v] of Object.entries(j.slots || {})) {
        if (v.skipped) continue;
        rows.push(el("li", { class: v.low_confidence ? "low" : "" },
          `${k}: ${v.value ?? "–"} · conf ${v.confidence}${v.low_confidence ? " ⚠ 낮음" : ""}${v.reduced ? " · ①로 줄임" : ""}`));
      }
      return [
        el("details", {}, el("summary", {}, `Jev 의도 · 슬롯 (${j.model || "–"} · 입력 토큰 ${(j.usage || {}).input_tokens ?? "–"})`),
          el("ul", {}, rows)),
        el("details", {}, el("summary", {}, `허브 경로 ${h.path || "–"} → ${h.tool || "(도구 없음)"} · 노드 ${(h.node_ids || []).length}`),
          el("pre", { class: "mini" }, JSON.stringify({ input: h.input, missing: h.missing, resolve: h.resolve, node_ids: h.node_ids }, null, 1))),
      ];
    }
    const calls = t.tool_calls || [];
    const label = name === "case1" ? "원장 도구" : "도구 호출";
    const tok = Object.values(c.tokens || {}).reduce((a, x) => a + (x.input_tokens || 0) + (x.output_tokens || 0), 0);
    return [el("details", {}, el("summary", {}, `${label} ${calls.length} · 토큰 ${tok}`),
      el("ul", {}, calls.map((x) => el("li", { class: x.is_error ? "bad" : "" },
        el("code", {}, `${x.tool} ${JSON.stringify(x.input)}`), ` · ${x.output_bytes} B · ${ms(x.ms)}`,
        name === "case2" && x.node_ids && x.node_ids.length ? el("div", { class: "muted" }, x.node_ids.join(", ")) : null))))];
  }

  function q1View(c) {
    const w = c.word_check;
    if (!w) return el("div", { class: "muted" }, "① 대기");
    if (w.error) return el("div", { class: "bad" }, `① 오류: ${w.error}`);
    return el("details", {}, el("summary", {}, el("span", { class: w.not_found ? "bad" : "ok" }, `① 없는 이름 ${w.not_found}`),
      ` / ${w.names.length}`),
      el("ul", { class: "q1" }, w.names.map((n) => el("li", { class: n.found ? "ok" : "bad" },
        `${n.found ? "✓" : "✕"} ${n.name}${n.via ? ` (${n.via})` : ""}`))));
  }

  function q2View(c) {
    const j = c.judge;
    if (!j) return el("div", { class: "muted" }, "② 대기");
    if (j.error) return el("div", { class: "bad" }, `② 오류: ${j.error}`);
    const mark = (v) => (v === "통과" ? "●" : v === "실패" ? "✕" : "–");
    return el("div", { class: "q2" },
      el("div", {}, `② ${j.label ? `(${j.label}) ` : ""}통과 ${j.pass_count}/5 `,
        ...Object.keys(ITEMS).map((k) => el("span", { class: (j.items || {})[k] === "실패" ? "bad" : (j.items || {})[k] === "통과" ? "ok" : "muted", title: ITEMS[k] }, mark((j.items || {})[k]) + " "))),
      (j.failures || []).length ? el("ul", {}, j.failures.map((f) => el("li", { class: f.status === "invalid" ? "invalid" : "bad" },
        `${ITEMS[f.item] || f.item}: ${f.claim}${f.status === "invalid" ? " (근거 무효)" : ""}`,
        f.evidence ? el("div", { class: "muted" }, `${f.evidence.path} — 「${f.evidence.quote}」`) : null))) : null);
  }

  function humanView(name) {
    const note = el("input", { type: "text", placeholder: "메모" });
    const send = async (verdict, btn) => {
      if (!cur.run_id) return;
      const r = await fetch(`/api/runs/${cur.run_id}/human_check`, { method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ case: name, verdict, note: note.value }) });
      if (r.ok) { btn.classList.add("sel"); loadRuns(); }
    };
    const ok = el("button", { type: "button", title: "맞음" }, "✓ 맞음");
    const bad = el("button", { type: "button", title: "틀림" }, "✗ 틀림");
    ok.addEventListener("click", () => send("맞음", ok));
    bad.addEventListener("click", () => send("틀림", bad));
    return el("div", { class: "human" }, "사람 확인", ok, bad, note);
  }

  function render() {
    const cols = $("cols");
    cols.replaceChildren();
    const scale = S.scale(cur);
    for (const name of S.CASES) {
      const c = cur.cases[name];
      const badges = [];
      if (c.error) badges.push(el("span", { class: "badge bad" }, c.error === S.NO_KEY ? S.NO_KEY : "오류"));
      if (c.refusal) badges.push(el("span", { class: "badge bad" }, "거절"));
      if (c.loop_capped) badges.push(el("span", { class: "badge" }, "도구 상한"));
      if (!c.done && cur.run_id && !c.error) badges.push(el("span", { class: "badge" }, "도는 중"));
      const t = c.timing || {};
      cols.append(el("section", { class: "col", "data-case": name },
        el("h3", {}, TITLE[name], ...badges),
        el("div", { class: "timing" }, `⏱ 첫 글자 ${ms(t.ttft_ms)} / 전체 ${ms(t.total_ms)} · 왕복 ${t.llm_round_trips ?? "–"}`),
        stageBar(c, scale),
        el("div", { class: "legend" }, name === "case3" ? "▏hub ▓ jev ▏hub ▓ claude" : "▓ claude ▓ 도구"),
        el("div", { class: "answer" }, c.text || (c.error && c.error !== S.NO_KEY ? c.error : "")),
        ...traceView(name, c), q1View(c), q2View(c), cur.run_id ? humanView(name) : null));
    }
    $("rerun").disabled = !(cur.done && cur.run_id);
  }

  function subscribe(runId, onState) {
    return new Promise((resolve) => {
      const st = S.initial(runId, "", disabled);
      const src = new EventSource(`/api/runs/${runId}/events`);
      for (const type of ["stage", "text", "trace", "case_done", "word_check", "judge", "error", "run_done"]) {
        src.addEventListener(type, (ev) => {
          S.reduce(st, type, JSON.parse(ev.data));
          if (onState) onState(st);
          if (type === "run_done") { src.close(); resolve(st); }
        });
      }
      src.onerror = () => { src.close(); resolve(st); };
      es = src;
    });
  }

  async function ask(question) {
    if (es) es.close();
    $("send").disabled = true;
    $("rerun-out").textContent = "";
    const r = await fetch("/api/ask", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ question }) });
    const { run_id } = await r.json();
    lastRunId = run_id;
    cur = S.initial(run_id, question, disabled);
    render();
    await subscribe(run_id, (st) => { st.question = question; cur = st; render(); });
    $("send").disabled = false;
    loadRuns();
  }

  async function rerun() {
    const base = cur.run_id;
    $("rerun").disabled = true;
    const r = await fetch(`/api/runs/${base}/rerun`, { method: "POST" });
    const { run_ids, question } = await r.json();
    const done = [];
    for (const [i, id] of run_ids.entries()) {
      $("rerun-out").textContent = `다시 ${i + 1}/${run_ids.length} 도는 중…`;
      done.push(await subscribe(id, (st) => { st.question = question; cur = st; render(); }));
    }
    const m = S.medians(done);
    $("rerun-out").textContent = "중간값 — " + S.CASES.map((c) => `${c}: 첫 글자 ${ms(m[c].ttft_ms)} · 전체 ${ms(m[c].total_ms)}`).join(" | ");
    loadRuns();
  }

  async function loadRuns() {
    const r = await fetch("/api/runs?limit=100");
    const { runs } = await r.json();
    const tb = document.querySelector("#runs tbody");
    tb.replaceChildren(...runs.map((x) => {
      const cs = x.cases || {};
      const nf = S.CASES.map((c) => (cs[c] || {}).not_found ?? "–").join(" / ");
      const jp = S.CASES.map((c) => (cs[c] || {}).judge_pass ?? "–").join(" / ");
      const hu = S.CASES.map((c) => ({ 맞음: "✓", 틀림: "✗" })[(cs[c] || {}).human] || "–").join(" ");
      const tr = el("tr", {}, el("td", {}, (x.asked_at || "").replace("T", " ").slice(0, 19)),
        el("td", { class: "qcell" }, x.question, x.rerun_of ? el("span", { class: "muted" }, " (다시)") : null),
        ...S.CASES.map((c) => el("td", {}, (cs[c] || {}).error ? "오류" : ms((cs[c] || {}).total_ms))),
        el("td", {}, nf), el("td", {}, jp), el("td", {}, hu), el("td", {}, x.gold || "–"));
      tr.addEventListener("click", () => showRecord(x.run_id));
      return tr;
    }));
  }

  async function showRecord(runId) {
    const r = await fetch(`/api/runs/${runId}`);
    if (!r.ok) return;
    const { record } = await r.json();
    if (es) es.close();
    cur = S.fromRecord(record);
    $("q").value = record.question;
    render();
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  async function loadStatus() {
    try {
      const st = await (await fetch("/api/status")).json();
      disabled = {};
      for (const [c, v] of Object.entries(st.cases || {})) if (v) disabled[c] = v;
      const k = st.keys || {};
      $("status").textContent = `Anthropic 키 ${k.anthropic ? "있음" : "없음"} · TypeSafe 키 ${k.typesafe ? "있음" : "없음"} · ` +
        `골드 MCP ${st.gold_mcp ?? "–"} · 원장 MCP ${st.raw_mcp ?? "–"} · vocab ${typeof st.vocab === "object" ? Object.values(st.vocab).reduce((a, b) => a + b, 0) : st.vocab ?? "–"}`;
      cur = S.initial(null, "", disabled);
      render();
    } catch (e) { $("status").textContent = "상태 확인 실패"; }
  }

  $("ask").addEventListener("submit", (e) => { e.preventDefault(); const q = $("q").value.trim(); if (q) ask(q); });
  $("rerun").addEventListener("click", rerun);
  loadStatus().then(loadRuns).then(() => {
    const q = new URLSearchParams(location.search).get("q");  // ?q= 로 열면 바로 보냄
    if (q) { $("q").value = q; ask(q); }
  });
})();
