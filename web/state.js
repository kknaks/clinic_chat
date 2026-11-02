// 화면 상태 — SSE 이벤트(SPEC-006 §4-3) → 상태. DOM 없음(브라우저 · node 시험 같이 씀).
(function (root) {
  const CASES = ["case1", "case2", "case3"];
  const NO_KEY = "키 없음";

  function emptyCase() {
    return { text: "", stages: [], timing: null, tokens: {}, trace: null, error: null, word_check: null, cost: null,
             grading: false, quality_error: null,
             judge: null, done: false, stop_reason: null, loop_capped: false, refusal: false };
  }

  function initial(runId, question, disabled) {
    const cases = {};
    for (const c of CASES) {
      cases[c] = emptyCase();
      if (disabled && disabled[c]) cases[c].error = disabled[c];
    }
    return { run_id: runId || null, question: question || "", cases, judge_order: [], judge_error: null,
             done: false, errors: [], cost: null, provider: null,
             answers_done: false, grading: false, graded: false, quality_error: null, stream_lost: false };
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
        c.cost = d.cost || null;
        break;
      case "cost": s.cost = d; break;
      case "provider": s.provider = d; break;
      case "quality_cost": s.cost = { ...(s.cost || {}), quality_usd: d.cost_usd }; break;
      case "word_check": { const { case: _c, ...rest } = d; c.word_check = rest; break; }
      case "judge":
        if (c) { const { case: _c, ...rest } = d; c.judge = rest; s.graded = true; break; }  // 열마다(절대 채점)
        s.judge_order = d.order || [];  // 옛 꼴(세 답 한꺼번에) — 기록 다시 그리기용
        s.judge_error = d.error || null;
        for (const k of Object.keys(d.cases || {})) s.cases[k].judge = d.cases[k];
        break;
      case "case_quality_start": c.grading = true; s.grading = true; break;
      case "case_quality_done":
      case "case_quality_error":
        c.grading = false;
        if (type === "case_quality_error") c.quality_error = d.message || "채점 실패";
        s.grading = CASES.some((k) => s.cases[k].grading);
        break;
      case "error":
        if (c) c.error = d.message; else s.errors.push(d.message);
        break;
      case "answers_done": s.answers_done = true; s.grading = !!d.auto_quality; break;
      case "quality_start": s.grading = true; break;
      case "quality_done": s.grading = false; s.graded = s.graded || CASES.some((k) => s.cases[k].judge); break;
      case "quality_error": s.grading = false; s.quality_error = d.message || "채점 실패"; break;
      case "run_done": s.done = true; s.grading = false; for (const k of CASES) s.cases[k].grading = false; break;
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
        cost: r.cost || null,
        loop_capped: !!r.loop_capped, refusal: !!r.refusal,
        word_check: r.word_check && Object.keys(r.word_check).length ? r.word_check : null,
        judge: r.judge && Object.keys(r.judge).length ? r.judge : null,
      });
    }
    s.judge_order = rec.judge_order || [];
    s.graded = (rec.judge_order || []).length > 0 || (rec.graded_cases || []).length > 0;
    s.answers_done = true;
    s.cost = rec.cost || null;
    const v = rec.versions || {};
    s.provider = { provider: rec.provider || "sdk", model: v.answer_model, effort: v.answer_effort,
                   auth: v.auth_kind ? { kind: v.auth_kind } : null };
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

  // 단계 색 종류 — 막대 · 범례 같은 색
  function stageKind(name) {
    if (name.startsWith("(측정 밖)")) return "gap";
    if (name.startsWith("claude") || name.startsWith("codex")) return "claude";
    if (name === "cli_boot") return "boot";
    if (name.startsWith("tool:")) return "tool";
    if (name === "jev" || name.startsWith("jev#")) return "jev";
    return "hub";
  }

  // 단계 시간 — 1초 미만은 ms(정수) · 이상은 s(소수 1자리)
  function fmtDur(ms) {
    if (typeof ms !== "number" || !isFinite(ms)) return "–";
    return ms < 1000 ? `${Math.round(ms)}ms` : `${(ms / 1000).toFixed(1)}s`;
  }

  // 범례 — 단계마다(같은 이름이 여러 번이면 순서대로 각각) 색 · 이름 · 시간
  function legendItems(stages) {
    return (stages || []).map((st) => {
      const end = st.end_ms == null ? null : st.end_ms;
      const label = st.name.replace(/^tool:/, "").replace(/^hub_resolve$/, "hub").replace(/^hub_path$/, "hub").replace(/^hub_peek#/, "peek#").replace(/^hub_expand#/, "expand#")
        .replace(/^\(측정 밖\)#\d+$/, "(측정 밖)").replace(/^cli_boot$/, "기동").replace(/^cli_queue$/, "대기");
      const think = st.thinking ? (end == null ? " · 생각 중" : st.thinking_ms != null ? ` · 생각 ${fmtDur(st.thinking_ms)}` : "") : "";
      return { kind: stageKind(st.name), label: label + think, dur: end == null ? "…" : fmtDur(end - (st.start_ms || 0)) };
    });
  }

  // 보내기 버튼 — 답 세 열이 끝나면(answers_done) 바로 다음 질문 · 채점은 백그라운드 · 끊김 · 오류도 풀림
  function sendState(s, requesting) {
    if (requesting) return { disabled: true, label: "보내는 중…", title: "질문을 서버에 보내는 중" };
    const busy = s && s.run_id && !s.answers_done && !s.done && !s.stream_lost;
    if (busy) return { disabled: true, label: "진행 중 · 답 받는 중", title: "세 열 답이 끝나면 다음 질문을 보낼 수 있음" };
    return { disabled: false, label: "보내기",
             title: s && s.grading ? "이전 질문 채점은 백그라운드로 계속됨(기록 표에 채워짐)" : "" };
  }

  // 열 머리 provider 줄
  function providerLabel(p) {
    if (!p) return "";
    return [p.provider, p.model, p.effort ? `effort ${p.effort}` : null].filter(Boolean).join(" · ");
  }

  // 비용 표시 — 1센트 미만은 소수 4자리
  function fmtUsd(x) {
    if (typeof x !== "number" || !isFinite(x)) return "–";
    return x < 0.01 ? `$${x.toFixed(4)}` : `$${x.toFixed(3)}`;
  }

  // 답 영역 스크롤 — 바닥 근처(px 여유)면 스트리밍을 따라감 · 위로 올리면 멈춤
  function atBottom(scrollTop, clientHeight, scrollHeight, slack) {
    return scrollHeight - (scrollTop + clientHeight) <= (slack == null ? 24 : slack);
  }

  // case3 걸음 줄(SPEC-006 v2.4 §6 · §3-4-4) — 걸음 k · phase(첫 · 대기열 · 돌아옴) · 노드 · 고른 것 · 신뢰도 · 바이트 · 멈춤 사유 ·
  // 이웃 fact 도달 · 원문(⑤ 받은 cut 줄) / 한 줄(못 받은 cut 줄)
  const PHASE = { first: "첫", queue: "대기열", return: "돌아옴" };
  function walkRows(w) {
    if (!w) return null;
    const steps = (w.steps || []).map((s) => {
      const j = s.jev || {}, e = s.expand || null;
      return {
        k: s.k, node: s.node, phase: PHASE[s.phase] || null, picked: j.picked || [], enough: j.enough ?? null,
        confidence: j.confidence ?? null, bytes: e ? e.bytes : null, excluded: !!(e && e.excluded),
        facts: e ? (e.fact_ids || []).length : 0, peek_bytes: s.peek_bytes ?? null, peek_reused: !!s.peek_reused,
        by_unit: s.by_unit_section || null, error: s.error || null,
      };
    });
    const f = w.full || null;
    const head = `걸음 ${w.steps_n} · 멈춤 ${w.stop || "–"} · ${w.bytes_total} B${w.budget_hit ? " (예산)" : ""} · fact ${(w.fact_ids || []).length}` +
      (w.neighbor_reached != null ? ` · 이웃 fact ${w.neighbor_reached}` : "") +
      (f ? ` · 원문 ${(f.fact_ids || []).length} / 한 줄 ${(f.cut_unfetched_ids || []).length}` : "") +
      ` · Jev ${w.jev_calls}회` + (w.fallback ? ` · 되돌아감(${w.fallback_reason})` : "");
    return { head, steps, fallback: !!w.fallback };
  }

  // 도구 호출 표시 — 잘림(마커) · intent(값 · 넘김 여부) · 격리 밖
  function callMarks(x) {
    const out = [];
    if (x.truncated === true) out.push("✂ 잘림");
    if (x.intent_passed === true) out.push(`intent ${(x.input || {}).intent}`);
    else if (x.intent_passed === false) out.push((x.input || {}).intent ? `intent ${(x.input || {}).intent} ✕` : "intent 없음");
    if (x.stray) out.push(x.stray_kind === "bare_hub" ? "이름 틀림" : "격리 밖");
    return out;
  }

  const api = { CASES, NO_KEY, initial, reduce, fromRecord, scale, median, medians, notFound, atBottom, stageKind, fmtDur, legendItems, fmtUsd, providerLabel, sendState, walkRows, callMarks };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.ClinicState = api;
})(typeof window !== "undefined" ? window : globalThis);
