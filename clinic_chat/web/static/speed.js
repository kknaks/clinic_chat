// 속도 탭(WORK-004 · WORK-006 WP-6) — B · C(체크로 B 전체 `b_full`) 카드 나란히 · 답 스트리밍 · 카드 머리 = 서버 `done.speed`(열 결과에서 옮긴 값).
// 「세트 몇 문항 한꺼번에」 = 고른 문항을 차례로 같은 /api/ask 로 돌리고 표는 서버 /api/speed/table 이 기록에서 만든다.
(function () {
  const TITLE = { b: "B gold+Jev", c: "C gold MCP", b_full: "B 전체" };
  const ORDER = ["b", "c", "b_full"];
  const PARTS = [["jev_ms", "jev", "Jev"], ["hub_ms", "hub", "허브"], ["llm_ms", "llm", "LLM"]];
  const TABLE = [["click_to_first_answer_ms", "누른 순간→첫 답 글자 ms"], ["total_ms", "전체 ms"], ["first_answer_text_ms", "첫 답 글자 ms"],
    ["click_to_first_text_ms", "누른 순간→첫 글자(메모 포함) ms"], ["ttft_ms", "첫 글자(메모 포함) ms"], ["input_kb", "입력 KB"], ["input_tokens", "입력 토큰"], ["output_tokens", "출력 토큰"]];
  const $ = (id) => document.getElementById(id);
  let cards = {};
  let es = null;
  let items = [];

  function el(tag, attrs, ...kids) {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (k === "class") e.className = v;
      else if (k === "style") e.setAttribute("style", v);
      else if (v !== null && v !== undefined && v !== false) e.setAttribute(k, v === true ? "" : v);
    }
    for (const k of kids.flat()) if (k !== null && k !== undefined) e.append(k.nodeType ? k : document.createTextNode(String(k)));
    return e;
  }
  const num = (x) => (typeof x === "number" ? String(Math.round(x * 10) / 10) : "–");
  const ms = (x) => (typeof x === "number" ? `${Math.round(x)} ms` : "–");

  async function getJson(url, opts) {
    const r = await fetch(url, opts);
    const body = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(body.detail || `${r.status}`);
    return body;
  }

  // ---- 탭
  for (const b of document.querySelectorAll(".tab")) {
    b.addEventListener("click", () => {
      for (const x of document.querySelectorAll(".tab")) x.classList.toggle("on", x === b);
      $("tab-acc").hidden = b.dataset.tab !== "acc";
      $("tab-speed").hidden = b.dataset.tab !== "speed";
    });
  }

  // ---- 세트 문항
  async function loadSet() {
    try { items = (await getJson("/api/set")).items || []; } catch (e) { items = []; }
    const sel = $("s-set-id"), multi = $("s-multi");
    for (const it of items) {
      const draft = it.status === "draft";
      const label = `${it.id} · ${it.range || "–"} · ${it.status || "–"}`;
      sel.append(el("option", { value: it.id, disabled: draft, title: it.question || "" }, label));
      multi.append(el("option", { value: it.id, disabled: draft, title: it.question || "" }, label));
    }
    sel.addEventListener("change", () => {
      const it = items.find((x) => x.id === sel.value);
      $("s-q").value = it ? it.question || "" : "";
    });
    $("s-q").addEventListener("input", () => { sel.value = ""; });
  }

  // ---- 카드
  function newCard(col) {
    const c = { col, done: null, step: null, memo: [], dom: {} };
    c.dom.head = el("h3", {});
    c.dom.timing = el("div", { class: "timing" });
    c.dom.bar = el("div", { class: "bar" });
    c.dom.legend = el("div", { class: "legend" });
    c.dom.err = el("div", { class: "err" });
    c.dom.memo = el("div", { class: "memo", title: "도구 호출 사이 글 — 답 · 채점에 안 들어감", hidden: true });
    c.dom.ans = el("div", { class: "answer" });
    c.dom.extra = el("div", {});
    c.dom.node = el("div", { class: `col ${col}` }, c.dom.head, c.dom.timing, c.dom.bar, c.dom.legend, c.dom.err,
      c.dom.memo, c.dom.ans, c.dom.extra);
    return c;
  }

  // 메모 줄 — 도구 호출 앞 글(도구 호출이 오면 답 칸에 흐르던 글을 여기로 · 끝난 뒤는 기록 trace.interim_text)
  function setMemo(c, list) {
    c.memo = list || [];
    c.dom.memo.hidden = !c.memo.length;
    c.dom.memo.textContent = c.memo.length ? `메모 · ${c.memo.join(" ⏵ ")}` : "";
  }

  // front.kinds[] — 필드 이름 그대로(code · noul · decision · by)
  function kindsTable(rows) {
    return el("table", { class: "mini" },
      el("thead", {}, el("tr", {}, ["code", "noul", "decision", "by"].map((h) => el("th", {}, h)))),
      el("tbody", {}, rows.map((k) => el("tr", { class: k.decision === "read" ? "" : "drop" },
        [k.code, num(k.noul), k.decision, k.by || ""].map((v) => el("td", {}, v ?? ""))))));
  }

  function paint(c) {
    const d = c.done, sp = (d && d.speed) || null;
    c.dom.head.replaceChildren(...[TITLE[c.col],
      !d ? el("span", { class: "badge" }, c.step ? `도는 중 ${c.step}` : "대기") : null,
      d && d.end_state ? el("span", { class: "badge ok" }, d.end_state) : null,
      d && d.error ? el("span", { class: "badge bad" }, "오류") : null].filter((x) => x));
    if (!sp) {
      c.dom.timing.replaceChildren();
      c.dom.bar.replaceChildren();
      c.dom.legend.replaceChildren();
      c.dom.extra.replaceChildren();
      c.dom.err.hidden = true;
      return;
    }
    c.dom.timing.replaceChildren(
      el("span", { class: "lead", title: "누른 순간→첫 답 글자(마지막 도구 호출 뒤 · 메모 뺌)" }, ms(sp.click_to_first_answer_ms)),
      el("span", {}, `전체 ${ms(sp.total_ms)}`),
      el("span", {}, `첫 답 글자 ${ms(sp.first_answer_text_ms)}`),
      el("span", { title: "메모 포함 첫 글자 — 열 시작 기준 / 누른 순간 기준" },
        `첫 글자(메모 포함) ${ms(sp.ttft_ms)} / ${ms(sp.click_to_first_text_ms)}`),
      el("span", {}, `LLM 입력 ${num(sp.input_kb)} KB / ${sp.input_tokens ?? "–"} 토큰`),
      el("span", {}, `출력 토큰 ${sp.output_tokens ?? "–"}`),
      el("span", {}, sp.thinking === null || sp.thinking === undefined ? "추론 –" : sp.thinking ? "추론 함" : "추론 안 함"),
      el("span", {}, sp.model || d.model || "–"),
      el("span", {}, `effort ${sp.effort || d.effort || "–"}`));
    const p = sp.parts || {}, total = sp.total_ms || 0;
    c.dom.bar.replaceChildren(...PARTS.map(([k, cls, label]) => el("span", { class: cls, title: `${label} ${ms(p[k])}`,
      style: `width:${total > 0 ? Math.max(0, (p[k] || 0) / total * 100) : 0}%` })));
    c.dom.legend.replaceChildren(...PARTS.map(([k, cls, label]) =>
      el("span", {}, el("i", { class: `bar-${cls}`, style: `background:var(--part-${cls})` }), `${label} ${ms(p[k])}`)));
    c.dom.err.hidden = !d.error;
    c.dom.err.textContent = d.error ? JSON.stringify(d.error) : "";
    const extra = [];
    const kinds = sp.front_kinds;
    if (kinds && kinds.length) extra.push(el("div", { class: "muted" }, "front.kinds[]"), kindsTable(kinds));
    c.dom.extra.replaceChildren(...extra);
  }

  function layout() {
    const box = $("s-cols");
    const nodes = ORDER.filter((k) => cards[k]).map((k) => cards[k].dom.node);
    box.style.setProperty("--n", Math.max(1, nodes.length));
    box.replaceChildren(...nodes);
  }

  // ---- 한 문항 돌리기(끝나면 run_id 를 돌려준다)
  function columns() { return ["b", "c", ...($("s-b").checked ? ["b_full"] : [])]; }

  async function runOne(body, currentEl) {
    const r = await getJson("/api/ask", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...body, columns: columns(), b_on: false, effort: $("s-effort").value }) });
    cards = {};
    for (const k of r.columns) cards[k] = newCard(k);
    layout();
    for (const k of r.columns) paint(cards[k]);
    currentEl.textContent = `${r.run_id}${r.set_id ? " · " + r.set_id : ""} · ${r.question || ""}`;
    await new Promise((resolve) => {
      if (es) es.close();
      es = new EventSource(`/api/runs/${r.run_id}/events`);
      let seen = -1;
      const on = (kind, f) => es.addEventListener(kind, (e) => {
        const n = Number(e.lastEventId);
        if (e.lastEventId !== "" && Number.isInteger(n)) { if (n <= seen) return; seen = n; }
        f(JSON.parse(e.data));
      });
      on("start", (d) => { const c = cards[d.column]; if (c) { c.step = c.step || "…"; paint(c); } });
      on("step", (d) => { const c = cards[d.column]; if (c) { c.step = d.step; paint(c); } });
      on("delta", (d) => { const c = cards[d.column]; if (c) c.dom.ans.append(d.text || ""); });
      on("interim", (d) => { const c = cards[d.column]; if (c) { setMemo(c, [...c.memo, d.text || c.dom.ans.textContent]); c.dom.ans.textContent = ""; } });
      on("done", (d) => { const c = cards[d.column]; if (c && d.ran) { c.done = d; paint(c); } });
      on("end", async () => {
        es.close(); es = null;
        // 끝난 뒤 답 = 기록의 최종 답(스트리밍 글은 미리 보기)
        try {
          const got = await getJson(`/api/runs/${r.run_id}`);
          for (const k of Object.keys(cards)) {
            const rc = ((got.row || {}).columns || {})[k];
            if (rc) { cards[k].dom.ans.textContent = rc.answer || ""; setMemo(cards[k], ((rc.trace || {}).interim_text) || []); }
          }
        } catch (e) { /* 기록을 못 읽어도 스트리밍 글은 남는다 */ }
        resolve();
      });
      es.onerror = () => { if (es && es.readyState === EventSource.CLOSED) resolve(); };
    });
    return r.run_id;
  }

  async function send(ev) {
    ev.preventDefault();
    const sid = $("s-set-id").value, q = $("s-q").value.trim();
    if (!sid && !q) return;
    $("s-send").disabled = true;
    try { await runOne(sid ? { set_id: sid } : { question: q }, $("s-current")); }
    catch (e) { $("s-current").textContent = String(e.message || e); }
    $("s-send").disabled = false;
  }

  // ---- 세트 몇 문항 한꺼번에
  function renderTable(t) {
    const tab = $("s-table");
    const cols = t.columns || [];
    tab.tHead.replaceChildren(
      el("tr", {}, el("th", {}, "문항"), cols.map((k) => el("th", { colspan: TABLE.length }, TITLE[k] || k))),
      el("tr", {}, el("th", {}, ""), cols.map(() => TABLE.map(([, h]) => el("th", {}, h)))));
    tab.tBodies[0].replaceChildren(...(t.rows || []).map((r) => el("tr", {}, el("td", {}, r.set_id || r.run_id),
      cols.map((k) => TABLE.map(([x]) => el("td", {}, num(((r.columns || {})[k] || {})[x])))))));
    const st = t.stats || {};
    tab.tFoot.replaceChildren(...[["mean", "평균"], ["median", "중앙값"]].map(([m, label]) => el("tr", {},
      el("th", {}, label), cols.map((k) => TABLE.map(([x]) => el("td", {}, num(((st[k] || {})[x] || {})[m])))))));
  }

  async function sendMany() {
    const ids = [...$("s-multi").selectedOptions].map((o) => o.value).filter((x) => x);
    if (!ids.length) return;
    $("s-batch").disabled = true; $("s-send").disabled = true;
    const runIds = [];
    for (const [i, id] of ids.entries()) {
      $("s-batch-current").textContent = `${i + 1} / ${ids.length} · ${id}`;
      try { runIds.push(await runOne({ set_id: id }, $("s-current"))); }
      catch (e) { $("s-batch-current").textContent = `${id} · ${String(e.message || e)}`; }
      try {
        renderTable(await getJson("/api/speed/table", { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ run_ids: runIds, effort: $("s-effort").value }) }));
      } catch (e) { /* 표는 다음 문항 뒤 다시 */ }
    }
    $("s-batch-current").textContent = `${runIds.length} / ${ids.length}`;
    $("s-batch").disabled = false; $("s-send").disabled = false;
  }

  $("s-ask").addEventListener("submit", send);
  $("s-batch").addEventListener("click", sendMany);
  loadSet();
})();
