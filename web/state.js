// 화면 상태 — SSE 이벤트(SPEC-006 §4-3) → 상태. DOM 없음(브라우저 · node 시험 같이 씀).
(function (root) {
  const CASES = ["case1", "case2", "case3"];
  const NO_KEY = "키 없음";

  function emptyCase() {
    return { text: "", stages: [], timing: null, tokens: {}, trace: null, error: null, word_check: null,
             judge: null, done: false, stop_reason: null, loop_capped: false, refusal: false };
  }

  function initial(runId, question, disabled) {
    const cases = {};
    for (const c of CASES) {
      cases[c] = emptyCase();
      if (disabled && disabled[c]) cases[c].error = disabled[c];
    }
    return { run_id: runId || null, question: question || "", cases, judge_order: [], judge_error: null,
             done: false, errors: [] };
  }

  function reduce(s, type, d) {
    const c = d && d.case ? s.cases[d.case] : null;
    switch (type) {
      case "stage": {
        const i = c.stages.findIndex((x) => x.name === d.name);
        const st = { name: d.name, start_ms: d.start_ms, end_ms: d.end_ms, tokens: d.tokens };
        if (i >= 0) c.stages[i] = st; else c.stages.push(st);
        break;
      }
      case "text": c.text += d.delta; break;
      case "trace": { const { case: _c, ...rest } = d; c.trace = rest; break; }
      case "case_done":
        c.done = true; c.timing = d.timing; c.tokens = d.tokens || {};
        if (d.timing && d.timing.stages) c.stages = d.timing.stages;
        if (d.error) c.error = d.error;
        c.stop_reason = d.stop_reason || null; c.loop_capped = !!d.loop_capped; c.refusal = !!d.refusal;
        break;
      case "word_check": { const { case: _c, ...rest } = d; c.word_check = rest; break; }
      case "judge":
        s.judge_order = d.order || [];
        s.judge_error = d.error || null;
        for (const k of Object.keys(d.cases || {})) s.cases[k].judge = d.cases[k];
        break;
      case "error":
        if (c) c.error = d.message; else s.errors.push(d.message);
        break;
      case "run_done": s.done = true; break;
    }
    return s;
  }

  function fromRecord(rec) {
    const s = initial(rec.run_id, rec.question);
    for (const c of CASES) {
      const r = (rec.cases || {})[c] || {};
      Object.assign(s.cases[c], {
        text: r.answer || "", stages: (r.timing || {}).stages || [], timing: r.timing || null, tokens: r.tokens || {},
        trace: r.trace || null, error: r.error || null, done: true, stop_reason: r.stop_reason || null,
        loop_capped: !!r.loop_capped, refusal: !!r.refusal,
        word_check: r.word_check && Object.keys(r.word_check).length ? r.word_check : null,
        judge: r.judge && Object.keys(r.judge).length ? r.judge : null,
      });
    }
    s.judge_order = rec.judge_order || [];
    s.done = true;
    s.versions = rec.versions;
    return s;
  }

  // 단계 막대 눈금 — 세 열 같은 눈금(가장 늦게 끝난 단계)
  function scale(s) {
    let m = 0;
    for (const c of CASES) {
      for (const st of s.cases[c].stages) m = Math.max(m, st.end_ms || st.start_ms || 0);
      const t = s.cases[c].timing;
      if (t && t.total_ms) m = Math.max(m, t.total_ms);
    }
    return m || 1;
  }

  function median(xs) {
    const v = xs.filter((x) => typeof x === "number").sort((a, b) => a - b);
    if (!v.length) return null;
    const h = Math.floor(v.length / 2);
    return v.length % 2 ? v[h] : (v[h - 1] + v[h]) / 2;
  }

  // 같은 질문 다시 ×3 — 열마다 ttft · total 중간값
  function medians(states) {
    const out = {};
    for (const c of CASES) {
      const ts = states.map((s) => s.cases[c].timing).filter(Boolean);
      out[c] = { n: ts.length, ttft_ms: median(ts.map((t) => t.ttft_ms)), total_ms: median(ts.map((t) => t.total_ms)) };
    }
    return out;
  }

  function notFound(c) { return c.word_check && typeof c.word_check.not_found === "number" ? c.word_check.not_found : null; }

  const api = { CASES, NO_KEY, initial, reduce, fromRecord, scale, median, medians, notFound };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.ClinicState = api;
})(typeof window !== "undefined" ? window : globalThis);
