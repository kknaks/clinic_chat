// 화면 — SSE 계약(SPEC-006 §4-3)을 state.js 로 줄여 3열을 그린다. 빌드 없는 정적 페이지(R-1).
(function () {
  const S = window.ClinicState;
  const TITLE = { case1: "case1 원장 MCP", case2: "case2 골드 MCP", case3: "case3 골드 + Jev" };
  const ITEMS = { accuracy: "정확성", safety: "안전 정보", candidates: "후보 처리", uncertainty: "불확실 표시", on_topic: "질문에 답함" };
  let disabled = {};
  let cur = S.initial();
  let lastRunId = null;
  let es = null;
  const grading = new Set();
  let requesting = false;
  const EVENTS = ["provider", "stage", "text", "trace", "case_done", "cost", "answers_done", "quality_start",
                  "case_quality_start", "word_check", "judge", "case_quality_done", "case_quality_error",
                  "quality_cost", "quality_done", "quality_error", "error", "run_done"];
  // 화면 보기 상태 — 다시 그려도 유지(열마다 따라가기 · 스크롤 위치 · 펼친 칸)
  let follow = {};
  let opened = new Set();
  function resetView() {
    follow = Object.fromEntries(S.CASES.map((c) => [c, true]));
    opened = new Set();
  }
  resetView();
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
  const stageClass = (n) => `s-${S.stageKind(n)}`;

  function legend(c) {
    const items = S.legendItems(c.stages);
    if (!items.length) return el("div", { class: "legend muted" }, "단계 –");
    return el("div", { class: "legend" }, items.map((x) => el("span", { class: "lg" },
      el("i", { class: `dot s-${x.kind}` }), `${x.label} ${x.dur}`)));
  }

  function stageBar(c, scale) {
    const bar = el("div", { class: "stagebar" });
    // 긴 단계부터 그려 짧은 단계(허브 3ms 등)가 위에 오게 — 최소 폭 3px 이 가려지지 않음
    const dur = (st) => (st.end_ms == null ? 0 : st.end_ms - (st.start_ms || 0));
    for (const st of [...c.stages].sort((x, y) => dur(y) - dur(x))) {
      const a = st.start_ms || 0, b = st.end_ms == null ? a : st.end_ms;
      bar.append(el("span", { class: stageClass(st.name), title: `${st.name} ${ms(a)} → ${ms(b)}`,
        style: `left:${(a / scale) * 100}%;width:${((b - a) / scale) * 100}%` }));
    }
    return bar;
  }

  function traceView(name, c) {
    const t = c.trace || {};
    if (name === "case3") {
      const j = t.jev || {}, h = t.hub || {};
      const wr = S.walkRows(t.walk);
      const walkView = wr ? [el("details", { "data-key": "case3:walk" }, el("summary", { class: wr.fallback ? "low" : "" }, wr.head),
        el("ul", {}, wr.steps.map((s) => el("li", { class: s.error ? "bad" : s.excluded ? "low" : "" },
          `걸음 ${s.k}${s.phase ? `(${s.phase})` : ""} · ${s.node}` + (s.by_unit ? ` · 단위 ${s.by_unit}` : "") + (s.picked.length ? ` · 고름 ${s.picked.join(", ")}` : "") +
          (s.confidence != null ? ` · conf ${s.confidence}` : "") + (s.enough != null ? ` · 충분 ${s.enough}` : "") +
          (s.peek_bytes != null ? ` · peek ${s.peek_bytes} B` : s.peek_reused ? " · peek 재사용" : "") +
          (s.bytes != null ? ` · expand ${s.bytes} B${s.excluded ? " (예산 넘어 뺌)" : ""} · fact ${s.facts}` : "") +
          (s.error ? ` · 오류 ${s.error}` : "")))))] : [];
      if (wr && !wr.fallback) return walkView;
      const rows = [];
      if (j.intent) rows.push(el("li", { class: j.intent.low_confidence ? "low" : "" },
        `의도 ${j.intent.choice} · conf ${j.intent.confidence}${j.intent.low_confidence ? " ⚠ 낮음" : ""}`));
      for (const [k, v] of Object.entries(j.slots || {})) {
        if (v.skipped) continue;
        rows.push(el("li", { class: v.low_confidence ? "low" : "" },
          `${k}: ${v.value ?? "–"} · conf ${v.confidence}${v.low_confidence ? " ⚠ 낮음" : ""}${v.reduced ? " · ①로 줄임" : ""}`));
      }
      return [
        ...walkView,
        el("details", { "data-key": "case3:jev" }, el("summary", {}, `Jev 의도 · 슬롯 (${j.model || "–"} · 입력 토큰 ${(j.usage || {}).input_tokens ?? "–"})`),
          el("ul", {}, rows)),
        el("details", { "data-key": "case3:hub" }, el("summary", {}, `허브 경로 ${h.path || "–"} → ${h.tool || "(도구 없음)"} · 노드 ${(h.node_ids || []).length}`),
          el("pre", { class: "mini" }, JSON.stringify({ input: h.input, missing: h.missing, resolve: h.resolve, node_ids: h.node_ids }, null, 1))),
      ];
    }
    const calls = t.tool_calls || [];
    const label = name === "case1" ? "원장 도구" : "도구 호출";
    const tok = Object.values(c.tokens || {}).reduce((a, x) => a + (x.input_tokens || 0) + (x.output_tokens || 0), 0);
    const ir = t.intent_ratio != null ? ` · intent ${Math.round(100 * t.intent_ratio)}%` : "";
    const cut = calls.filter((x) => x.truncated === true).length;
    const sk = t.stray_by_kind || null;
    const strayTxt = !t.stray_tools ? "" : sk ? ` · 이름 틀림 ${sk.bare_hub} · 격리 밖 ${sk.foreign}` : ` · 격리 밖 ${t.stray_tools}`;
    const extra = ir + (cut ? ` · 잘림 ${cut}` : "") + strayTxt;
    return [el("details", { "data-key": `${name}:tools` }, el("summary", {}, `${label} ${calls.length} · 토큰 ${tok}${extra}`),
      el("ul", {}, calls.map((x) => el("li", { class: x.is_error ? "bad" : "" },
        el("code", {}, `${x.tool} ${JSON.stringify(x.input)}`), ` · ${x.delivered_bytes ?? x.output_bytes} B · ${ms(x.ms)}`,
        ...S.callMarks(x).map((m) => el("span", { class: m.startsWith("✂") || m.endsWith("✕") || m === "격리 밖" || m === "이름 틀림" ? "bad" : "muted" }, ` · ${m}`)),
        name === "case2" && x.node_ids && x.node_ids.length ? el("div", { class: "muted" }, x.node_ids.join(", ")) : null))))];
  }

  function q1View(name, c) {
    const w = c.word_check;
    if (!w) return el("div", { class: "muted" }, c.grading ? "① 채점 중…" : "① 대기");
    if (w.error) return el("div", { class: "bad" }, `① 오류: ${w.error}`);
    return el("details", { "data-key": `${name}:q1` }, el("summary", {}, el("span", { class: w.not_found ? "bad" : "ok" }, `① 없는 이름 ${w.not_found}`),
      ` / ${w.names.length}`),
      el("ul", { class: "q1" }, w.names.map((n) => el("li", { class: n.found ? "ok" : "bad" },
        `${n.found ? "✓" : "✕"} ${n.name}${n.via ? ` (${n.via})` : ""}`))));
  }

  function q2View(c) {
    const j = c.judge;
    if (!j) return el("div", { class: "muted" }, c.grading ? "② 채점 중…" : "② 대기");
    if (j.error) return el("div", { class: "bad" }, `② 오류: ${j.error}`);
    const mark = (v) => (v === "통과" ? "●" : v === "실패" ? "✕" : "–");
    return el("div", { class: "q2" },
      el("div", {}, `② ${j.label ? `(${j.label}) ` : ""}통과 ${j.pass_count}/5 ${j.mode === "absolute" ? "(절대) " : ""}`,
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
    const scrolls = {};
    for (const a of cols.querySelectorAll(".answer")) scrolls[a.dataset.case] = a.scrollTop;
    for (const d of cols.querySelectorAll("details[data-key]")) {
      if (d.open) opened.add(d.dataset.key); else opened.delete(d.dataset.key);
    }
    cols.replaceChildren();
    const k = cur.cost || {};
    const sub = (k.billed || []).includes("subscription") ? "추정(구독 — 청구 아님) " : "예상 비용 ";
    const costLine = typeof k.answers_usd === "number"
      ? `  ·  ${sub}답 ${S.fmtUsd(k.answers_usd)}` + (typeof k.quality_usd === "number" ? ` + 채점 ${S.fmtUsd(k.quality_usd)} = ${S.fmtUsd(k.total_usd)}` : "")
      : "";
    const prov = cur.provider ? `  ·  ${S.providerLabel(cur.provider)}` : "";
    $("current").textContent = cur.question ? `질문: ${cur.question}${prov}${costLine}` : "";
    const scale = S.scale(cur);
    for (const name of S.CASES) {
      const c = cur.cases[name];
      const badges = [];
      if (c.error) badges.push(el("span", { class: "badge bad" }, c.error === S.NO_KEY ? S.NO_KEY : "오류"));
      if (c.refusal) badges.push(el("span", { class: "badge bad" }, "거절"));
      if (c.loop_capped) badges.push(el("span", { class: "badge" }, "도구 상한"));
      if (!c.done && cur.run_id && !c.error && !cur.stream_lost) badges.push(el("span", { class: "badge" }, "도는 중"));
      if (c.grading) badges.push(el("span", { class: "badge" }, "채점 중"));
      if (c.quality_error) badges.push(el("span", { class: "badge bad", title: c.quality_error }, "채점 실패"));
      if (cur.stream_lost && !c.done) badges.push(el("span", { class: "badge bad" }, "연결 끊김 — 기록 표에서 보기"));
      const t = c.timing || {};
      cols.append(el("section", { class: "col", "data-case": name },
        el("h3", {}, TITLE[name], ...badges),
        cur.provider ? el("div", { class: "prov muted" }, S.providerLabel(cur.provider)) : null,
        el("div", { class: "timing" },
          (typeof t.cli_boot_ms === "number" ? `⏱ 기동 ${ms(t.cli_boot_ms)} / ` : "⏱ ") +
          `첫 글자 ${ms(t.ttft_ms)}${t.ttft_kind === "message" ? "(메시지)" : ""} / 전체 ${ms(t.total_ms)} · 왕복 ${t.llm_round_trips ?? "–"}`,
          el("span", { class: "usd", title: (c.cost || {}).billed === "subscription" ? "CLI 추정 — 구독이라 청구 아님" : "이 열 예상 비용(Sonnet · Jev · 캐시 포함)" },
            ` · ${S.fmtUsd((c.cost || {}).usd)}${(c.cost || {}).billed === "subscription" ? " 추정" : ""}`)),
        stageBar(c, scale),
        legend(c),
        el("div", { class: "answer", "data-case": name }, c.text || (c.error && c.error !== S.NO_KEY ? c.error : "")),
        el("div", { class: "below" }, ...traceView(name, c), q1View(name, c), q2View(c),
          cur.run_id ? humanView(name) : null)));
    }
    for (const d of cols.querySelectorAll("details[data-key]")) d.open = opened.has(d.dataset.key);
    for (const a of cols.querySelectorAll(".answer")) {
      const c = a.dataset.case;
      a.scrollTop = follow[c] ? a.scrollHeight : (scrolls[c] || 0);
      a.addEventListener("scroll", () => { follow[c] = S.atBottom(a.scrollTop, a.clientHeight, a.scrollHeight); });
    }
    const ready = cur.run_id && (cur.answers_done || cur.done);
    $("rerun").disabled = !ready || requesting;
    const graded = cur.graded || (cur.judge_order || []).length > 0;
    const busyGrade = cur.grading || grading.has(cur.run_id);
    $("grade").disabled = !ready || graded || busyGrade;
    $("grade").textContent = graded ? "채점 끝(①②)" : busyGrade ? "채점 중…" : "채점 ①②";
    const sb = S.sendState(cur, requesting);
    $("send").disabled = sb.disabled;
    $("send").textContent = sb.label;
    $("send").title = sb.title;
  }

  // SSE — 답 세 열이 끝나면(answers_done) 약속을 풂(다음 질문 가능) · 채점 이벤트는 run_done 까지 계속 받음
  function subscribe(runId, onState) {
    return new Promise((resolve) => {
      const st = S.initial(runId, "", disabled);
      const src = new EventSource(`/api/runs/${runId}/events`);
      let settled = false;
      const settle = () => { if (!settled) { settled = true; resolve(st); } };
      for (const type of EVENTS) {
        src.addEventListener(type, (ev) => {
          if (type === "error" && !ev.data) return;  // 연결 오류(이름이 같음) — onerror 에서
          let data = {};
          try { data = JSON.parse(ev.data); } catch (e) { return; }
          S.reduce(st, type, data);
          if (onState) onState(st);
          if (type === "answers_done") settle();
          if (type === "run_done") { src.close(); settle(); if (st.graded) loadRuns(); }
          if (type === "quality_done" || type === "quality_error") loadRuns();
        });
      }
      src.onerror = () => {  // 끊김 · 서버 재시작 · 시간 초과 — 상태를 풀고 기록 표로
        if (src.readyState === EventSource.CLOSED || !st.done) {
          st.stream_lost = !st.done;
          src.close();
          if (onState) onState(st);
          settle();
        }
      };
      es = src;
    });
  }

  async function ask(question) {
    if (es) es.close();  // 이전 질문의 채점은 서버에서 계속 — 기록 표에 채워짐
    requesting = true;
    $("rerun-out").textContent = "";
    $("q").value = "";  // 보낸 뒤 비움(질문은 기록 표 · 열 위 줄에 남음)
    resetView();
    cur = S.initial(null, question, disabled);  // 이전 결과를 바로 비움
    render();
    try {
      const provider = $("provider").value || null;
      const r = await fetch("/api/ask", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ question, provider }) });
      const d = await r.json().catch(() => ({}));
      if (!r.ok || !d.run_id) throw new Error(d.detail || `보내기 실패 ${r.status}`);
      lastRunId = d.run_id;
      cur = S.initial(d.run_id, question, disabled);
      requesting = false;
      render();
      loadRuns();
      await subscribe(d.run_id, (st) => { st.question = question; cur = st; render(); });
    } catch (e) {
      cur.errors.push(String(e.message || e));
      $("current").textContent = `질문: ${question}  ·  오류: ${e.message || e}`;
    } finally {
      requesting = false;
      render();
      loadRuns();
    }
  }

  async function rerun() {
    const base = cur.run_id;
    $("rerun").disabled = true;
    const r = await fetch(`/api/runs/${base}/rerun`, { method: "POST" });
    const { run_ids, question } = await r.json();
    const done = [];
    for (const [i, id] of run_ids.entries()) {
      resetView();
      $("rerun-out").textContent = `다시 ${i + 1}/${run_ids.length} 도는 중…`;
      done.push(await subscribe(id, (st) => { st.question = question; cur = st; render(); }));
    }
    const m = S.medians(done);
    $("rerun-out").textContent = "중간값 — " + S.CASES.map((c) => `${c}: 첫 글자 ${ms(m[c].ttft_ms)} · 전체 ${ms(m[c].total_ms)}`).join(" | ");
    loadRuns();
  }

  // 「채점」 — ① 이름 뽑기 · ② Opus 채점(자동 아님 · Opus 비용)
  async function grade(runId) {
    if (!runId || grading.has(runId)) return;
    grading.add(runId);
    if (cur.run_id === runId) render();
    loadRuns();
    try {
      const r = await fetch(`/api/runs/${runId}/quality`, { method: "POST" });
      if (r.ok) {
        const { record } = await r.json();
        if (cur.run_id === runId) { cur = S.fromRecord(record); }
      }
    } finally {
      grading.delete(runId);
      if (cur.run_id === runId) render();
      loadRuns();
    }
  }

  let runsTimer = null;
  async function loadRuns() {
    let runs = [];
    try {
      const r = await fetch("/api/runs?limit=100000");  // 전부(10-31 사용자) · 번호 검색은 화면에서
      if (!r.ok) return;
      runs = (await r.json()).runs || [];
    } catch (e) { return; }
    clearTimeout(runsTimer);
    if (runs.some((x) => x.grading)) runsTimer = setTimeout(loadRuns, 4000);  // 백그라운드 채점이 끝나면 채움
    const tb = document.querySelector("#runs tbody");
    const qf = ($("runs-q").value || "").trim().toUpperCase();
    if (qf) runs = runs.filter((x) => (x.question_no || "").toUpperCase().includes(qf) || (x.question || "").includes($("runs-q").value.trim()));
    $("runs-count").textContent = `${runs.length}줄`;
    tb.replaceChildren(...runs.map((x) => {
      const cs = x.cases || {};
      const nf = S.CASES.map((c) => (cs[c] || {}).not_found ?? "–").join(" / ");
      const jp = S.CASES.map((c) => (cs[c] || {}).judge_pass ?? "–").join(" / ");
      const hu = S.CASES.map((c) => ({ 맞음: "✓", 틀림: "✗" })[(cs[c] || {}).human] || "–").join(" ");
      const tr = el("tr", {}, el("td", { title: x.topic || "" }, x.question_no || "–"),
        el("td", {}, (x.asked_at || "").replace("T", " ").slice(0, 19)),
        el("td", { class: "qcell" }, x.question, x.rerun_of ? el("span", { class: "muted" }, " (다시)") : null,
          x.batch_id ? el("span", { class: "muted" }, ` (일괄 ${x.batch_id})`) : null),
        el("td", { title: [x.model, x.effort].filter(Boolean).join(" · ") }, x.provider || "sdk"),
        ...S.CASES.map((c) => el("td", {}, (cs[c] || {}).error ? "오류" : ms((cs[c] || {}).total_ms))),
        el("td", {}, nf), el("td", {}, jp), el("td", {}, hu),
        el("td", { title: "답 합계(+채점)" }, S.fmtUsd((x.cost || {}).total_usd ?? (x.cost || {}).answers_usd)),
        el("td", {}, x.grading ? "채점 중…" : x.graded ? "끝" : (() => {
          const b = el("button", { type: "button", class: "mini" }, grading.has(x.run_id) ? "채점 중…" : "채점");
          b.disabled = grading.has(x.run_id);
          b.addEventListener("click", (e) => { e.stopPropagation(); grade(x.run_id); });
          return b;
        })()),
        el("td", {}, x.gold || "–"));
      tr.addEventListener("click", () => showRecord(x.run_id));
      return tr;
    }));
  }

  async function showRecord(runId) {
    const r = await fetch(`/api/runs/${runId}`);
    if (!r.ok) return;
    const { record } = await r.json();
    if (es) es.close();
    resetView();
    cur = S.fromRecord(record);
    render();
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  async function loadProviders() {
    try {
      const d = await (await fetch("/api/providers")).json();
      const sel = $("provider");
      sel.replaceChildren(...d.providers.map((p) => {
        const o = el("option", { value: p.provider, title: p.reason || "" },
          `${S.providerLabel(p)}${p.provider === "sdk" ? " · 유료" : " · 구독"}${p.available ? "" : ` (${p.reason || "못 씀"})`}`);
        o.disabled = !p.available;
        return o;
      }));
      const def = d.providers.find((p) => p.provider === d.default && p.available) || d.providers.find((p) => p.available);
      if (def) sel.value = def.provider;
      $("judge-line").textContent = `채점(①②): ${d.judge.provider} · ${d.judge.model} — CLI 판은 자동 · sdk 판은 버튼`;
    } catch (e) { /* 상태 줄에 이미 표시 */ }
  }

  // 일괄 실행 패널 — 계획 · 시작 · 멈춤 · 진행(실행은 사용자 · 코디)
  let batchId = null, batchTimer = null;
  async function loadBatchPanel() {
    const d = await (await fetch("/api/batches")).json();
    $("batch-config").textContent = d.config_missing.length ? `설정 필요: ${d.config_missing.join(" · ")} — 비면 시작하지 않음` : "설정 됨";
    if (d.batches[0]) { batchId = d.batches[0].batch_id; showBatch(); }
  }
  async function showBatch() {
    if (!batchId) return;
    const r = await fetch(`/api/batches/${batchId}`);
    if (!r.ok) return;
    const b = await r.json();
    const p = b.progress || {};
    $("batch-status").textContent = `${b.batch_id} · ${b.state} · ${p.done}/${p.total} 끝 · 실패 ${p.failed}` +
      (b.now ? ` · 지금 #${b.now.n} ${b.now.provider}` : "") +
      (Object.keys(b.paused || {}).length ? ` · 한도 멈춤 ${JSON.stringify(b.paused)}` : "");
    clearTimeout(batchTimer);
    if (["running", "sleeping"].includes(b.state)) batchTimer = setTimeout(() => { showBatch(); loadRuns(); }, 5000);
  }
  async function batchPlan() {
    const providers = [...document.querySelectorAll("input[name=bp]:checked")].map((x) => x.value);
    const qs = $("batch-q").value.split(",").map((x) => parseInt(x, 10)).filter((x) => !Number.isNaN(x));
    const r = await fetch("/api/batches", { method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ providers, questions: qs.length ? qs : null }) });
    const d = await r.json();
    if (!r.ok) { $("batch-status").textContent = d.detail; return; }
    batchId = d.batch_id; showBatch();
  }
  async function batchStart() {
    if (!batchId) return;
    const r = await fetch(`/api/batches/${batchId}/start`, { method: "POST" });
    const d = await r.json();
    if (!r.ok) { $("batch-status").textContent = d.detail; return; }
    showBatch();
  }
  async function batchStop() {
    if (batchId) await fetch(`/api/batches/${batchId}/stop`, { method: "POST" });
    showBatch();
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
  $("runs-q").addEventListener("input", () => loadRuns());
  $("grade").addEventListener("click", () => grade(cur.run_id));
  $("batch-plan").addEventListener("click", batchPlan);
  $("batch-start").addEventListener("click", batchStart);
  $("batch-stop").addEventListener("click", batchStop);
  loadStatus().then(loadProviders).then(loadRuns).then(loadBatchPanel).then(() => {
    const q = new URLSearchParams(location.search).get("q");  // ?q= 로 열면 바로 보냄
    if (q) { $("q").value = q; ask(q); }
  });
})();
