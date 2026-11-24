/* 상담 화면 동작 — 이벤트 위임 + data-* 만(인라인 스크립트 · eval 없음 · DAY 규칙).
 *   form[data-cs-form]     Enter 전송 · Shift+Enter 줄바꿈 · 보내는 중 잠금(답 줄기가 닫힐 때까지) · 보낸 뒤 비움 · 포커스 유지
 *   [data-cs-scroll]       새 조각 · 답 글자가 오면 끝으로 스크롤(사용자가 위로 올려 읽는 중이면 두지 않는다)
 *   [data-cs-live]         도는 턴 — 줄기가 닫히면(close · 오류) 잠금 풂 · 0.3 드로어 목록을 한 번 다시 받음(SV6)
 *   [data-cs-drawer-toggle] 0.3 드로어 접기 · 펴기(SV2 · 기본 열림)
 *   #consult-state-turns   0.3 객체 패널 — 새 턴 묶음이 위에 붙으면 앞 묶음은 접는다(SV8)
 *   [data-cs-pick]         0.3 점 · 라벨 누르기(FD4 · FD6 · FD11) — 조각은 htmx 속성이 부르고, 여기는 누른 표시(aria-pressed)와
 *                          다음 턴 폼의 숨은 칸 viewing 에 고민 코드 싣기만 · POST 한 번 뒤 비운다(W-2)
 */
(() => {
  "use strict";

  const NEAR_BOTTOM = 80;
  let stick = true;

  const logs = () => document.querySelectorAll("[data-cs-scroll]");
  const form = () => document.querySelector("form[data-cs-form]");
  const input = () => document.querySelector("[data-cs-input]");

  const scrollLog = (force) => {
    logs().forEach((el) => {
      if (force || stick) el.scrollTop = el.scrollHeight;
    });
  };

  const setBusy = (on) => {
    const f = form();
    if (!f) return;
    f.classList.toggle("is-busy", on);
    if (on) f.dataset.busy = "1";
    else delete f.dataset.busy;
  };
  const liveRunning = () => document.querySelector("[data-cs-live]:not([data-cs-closed])") !== null;

  document.addEventListener(
    "scroll",
    (event) => {
      const el = event.target;
      if (el && el.matches && el.matches("[data-cs-scroll]")) {
        stick = el.scrollHeight - el.scrollTop - el.clientHeight < NEAR_BOTTOM;
      }
    },
    true,
  );

  document.addEventListener("keydown", (event) => {
    const el = event.target;
    if (!el || !el.matches || !el.matches("[data-cs-input]")) return;
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing && event.keyCode !== 229) {
      event.preventDefault();
      const f = el.closest("form");
      if (f && !f.dataset.busy && el.value.trim()) f.requestSubmit();
    }
  });

  document.addEventListener("htmx:beforeRequest", (event) => {
    const f = event.detail?.elt;
    if (!f || !f.matches || !f.matches("form[data-cs-form]")) return;
    const box = input();
    if (f.dataset.busy || !box || !box.value.trim()) {
      event.preventDefault();
      return;
    }
    setBusy(true);
  });

  document.addEventListener("htmx:afterRequest", (event) => {
    const f = event.detail?.elt;
    if (!f || !f.matches || !f.matches("form[data-cs-form]")) return;
    if (event.detail.successful) {
      f.reset();
      const v = f.querySelector("[data-cs-viewing]");
      if (v) v.value = ""; /* W-2 — viewing 은 누른 다음 한 턴만 */
      stick = true;
      scrollLog(true);
    }
    if (!liveRunning()) setBusy(false);
    const box = input();
    if (box) box.focus();
  });

  const app = () => document.querySelector("[data-cs-app]");

  const refreshDrawer = () => {
    const list = document.getElementById("conv-drawer-list");
    const a = app();
    if (!list || !a || !window.htmx) return;
    const url = "/convs?current=" + encodeURIComponent(a.dataset.convId || "");
    window.htmx.ajax("GET", url, { target: "#conv-drawer-list", swap: "outerHTML" });
  };

  const closed = (event) => {
    const live = event.target?.closest ? event.target.closest("[data-cs-live]") : null;
    if (live && !live.dataset.csClosed) {
      live.dataset.csClosed = "1";
      refreshDrawer();
    }
    if (!liveRunning()) setBusy(false);
  };

  document.addEventListener("click", (event) => {
    const btn = event.target?.closest ? event.target.closest("[data-cs-drawer-toggle]") : null;
    if (!btn) return;
    const open = btn.getAttribute("aria-expanded") !== "false";
    btn.setAttribute("aria-expanded", open ? "false" : "true");
    const a = app();
    if (a) a.classList.toggle("is-drawer-closed", open);
  });

  const pick = (el) => {
    document.querySelectorAll("[data-cs-pick][aria-pressed='true']").forEach((x) => x.setAttribute("aria-pressed", "false"));
    const code = el.dataset.siteCode;
    document.querySelectorAll("[data-cs-pick]").forEach((x) => {
      if (x.dataset.siteCode === code) x.setAttribute("aria-pressed", "true");
    });
    const v = document.querySelector("[data-cs-viewing]");
    if (v) v.value = el.dataset.concernCodes || "";
  };
  document.addEventListener("click", (event) => {
    const el = event.target?.closest ? event.target.closest("[data-cs-pick]") : null;
    if (el) pick(el);
  });
  document.addEventListener("keydown", (event) => {
    const el = event.target;
    if (!el || !el.matches || !el.matches("[data-cs-pick]") || (event.key !== "Enter" && event.key !== " ")) return;
    event.preventDefault();
    pick(el);
    if (window.htmx) window.htmx.trigger(el, "click");
  });

  document.addEventListener("htmx:afterSwap", (event) => {
    const box = event.detail?.target;
    if (!box || box.id !== "consult-state-turns") return;
    box.querySelectorAll("details.cs-state__turn").forEach((d, i) => {
      if (i > 0) d.open = false;
    });
  });
  document.addEventListener("htmx:sseClose", closed);
  document.addEventListener("htmx:sseError", (event) => {
    const src = event.detail?.source;
    /* 다시 붙지 않는 줄기(204 등)만 */
    if (src && src.readyState === 2) closed(event);
  });

  document.addEventListener("htmx:afterSwap", () => scrollLog(false));
  document.addEventListener("htmx:sseMessage", () => scrollLog(false));
  document.addEventListener("DOMContentLoaded", () => {
    scrollLog(true);
    if (liveRunning()) setBusy(true);
    const box = input();
    if (box) box.focus();
  });
})();

/* 0.5 화면 틀(SPEC-010 §4 · 시안 design-consult-20261120.html 줄 925 ~ 1083 의 꼴만 — 의학 글 예시 상수는 옮기지 않음 · ST4).
 *   .cs-pcard              카드 누름 = 강조(aria-pressed) · 얼굴 점 링(g.cs-pick — data-points) · 몸 이름 is-pick(data-body-sites)
 *   [data-cs-explain]      시술 설명 모달 — `$J` 칸만 카드 data-* 에서 채움(허브 칸 · 그림은 WP-4 조각)
 *   .cs-pm__tab            목차 탭 — 누르면 그 칸으로 · 스크롤하면 탭 표시(시안 그대로)
 *   [data-cs-cart] 등       장바구니 — WP-5 서버 장바구니: 담기 · 빼기 · 비우기 · 옵션 줄 넣기 · 지우기 = POST /c/<id>/cart(코드는 폼 값 — RT1) →
 *                          #cs-cart-body 조각(진료항목 · 옵션 줄 · 최저가 카드) · 「저장」 = POST /cart/save. 글자는 data-on · data-off · data-save · data-saved
 *   [data-opt-open]        「옵션 추가 +」 → 드롭다운 · 「추가」 자리 열기(넣기는 WP-5)
 */
(() => {
  "use strict";
  const NS = "http://www.w3.org/2000/svg";
  const modal = () => document.getElementById("cs-proc-modal");
  const drawer = () => document.getElementById("cs-cart");
  const convId = () => document.querySelector("[data-cs-app]")?.dataset.convId || "";
  /* 0.5 WP-5 — 장바구니는 서버가 든다(POST /c/<id>/cart → #cs-cart-body 조각). 여기 `cart` 는 조각의 data-cart-codes 를 읽은 것뿐 */
  let cart = new Set();
  const readCart = () => {
    const st = document.querySelector("#cs-cart-body [data-cart-state]");
    cart = new Set(((st && st.dataset.cartCodes) || "").split("|").filter(Boolean));
  };
  const card = (code) => [...document.querySelectorAll(".cs-pcard")].find((c) => c.dataset.code === code) || null;

  const drawPick = (c) => {
    const svg = document.querySelector("#face-marks .cs-marks__svg");
    if (svg) {
      svg.querySelectorAll(".cs-pick").forEach((g) => g.remove());
      (c?.dataset.points || "").split(";").filter(Boolean).forEach((p) => {
        const [x, y] = p.split(",");
        const g = document.createElementNS(NS, "g");
        g.setAttribute("class", "cs-pick");
        const ring = document.createElementNS(NS, "circle");
        ring.setAttribute("class", "cs-dot__ring");
        const dot = document.createElementNS(NS, "circle");
        dot.setAttribute("class", "cs-dot cs-dot--pick");
        [ring, dot].forEach((el) => { el.setAttribute("cx", x); el.setAttribute("cy", y); });
        ring.setAttribute("r", 16); dot.setAttribute("r", 10);
        g.append(ring, dot);
        svg.append(g);
      });
    }
    const body = (c?.dataset.bodySites || "").split(",").filter(Boolean);
    document.querySelectorAll("#face-marks .cs-label[data-region='body']").forEach((l) => {
      l.classList.toggle("is-pick", body.includes(l.dataset.siteCode));
    });
  };

  const setPm = (k, v) => { const el = modal()?.querySelector(`[data-pm="${k}"]`); if (el) el.textContent = v || ""; };
  const syncModalCart = () => {
    const m = modal(); const b = m?.querySelector("[data-pm-cart]");
    if (!b) return;
    const on = cart.has(m.dataset.code);
    b.textContent = on ? b.dataset.on : b.dataset.off;
    b.classList.toggle("ms-btn--primary", !on); b.classList.toggle("ms-btn--filled", on);
  };
  const pick = (c) => {
    if (!c) return;
    document.querySelectorAll(".cs-pcard[aria-pressed='true']").forEach((x) => x.setAttribute("aria-pressed", "false"));
    c.setAttribute("aria-pressed", "true");
    drawPick(c);
    const d = c.dataset;
    const m = modal();
    if (!m) return;
    setPm("kind", d.kind); setPm("name", c.querySelector(".cs-pcard__name")?.textContent); setPm("code", d.code);
    setPm("kind2", d.kind); setPm("site", d.site); setPm("drug", c.querySelector(".cs-pcard__drug")?.textContent);
    setPm("device", d.device);
    const pub = m.querySelector("[data-pm='published']");
    if (pub) pub.hidden = !("published" in d);
    m.dataset.code = d.code;
    syncModalCart();
  };
  const openModal = async (c) => {
    const m = modal();
    if (!c || !m) return;
    pick(c);
    /* 0.5 WP-4 MD4 — 모달 안 = 서버 조각(허브 칸 · 그림 · `$J` 칸) · 코드는 쿼리 값(RT1) */
    const r = await fetch(`/c/${encodeURIComponent(convId())}/item?code=${encodeURIComponent(c.dataset.code)}`);
    if (r.ok) {
      m.innerHTML = await r.text();
      m.dataset.code = c.dataset.code;
      syncModalCart();
    }
    if (!m.open) m.showModal();
    const body = m.querySelector("[data-pm-body]");
    if (body) body.scrollTop = 0;
    m.querySelectorAll(".cs-pm__tab").forEach((x, i) => x.classList.toggle("is-active", i === 0));
  };

  let tabLock = 0;
  document.addEventListener("click", (e) => {
    const a = e.target?.closest ? e.target.closest(".cs-pm__tab") : null;
    if (!a) return;
    e.preventDefault();
    const m = modal(); const body = m?.querySelector("[data-pm-body]");
    const sec = m?.querySelector(a.getAttribute("href"));
    if (!body || !sec) return;
    body.scrollTo({ top: sec.offsetTop - body.offsetTop - 8 });
    m.querySelectorAll(".cs-pm__tab").forEach((x) => x.classList.toggle("is-active", x === a));
    tabLock = Date.now() + 700;
  });
  document.addEventListener("scroll", (e) => {
    const body = e.target;
    if (!body?.matches || !body.matches("[data-pm-body]") || Date.now() < tabLock) return;
    const tabs = [...modal().querySelectorAll(".cs-pm__tab")];
    let cur = tabs[0];
    tabs.forEach((a) => { const sec = modal().querySelector(a.getAttribute("href")); if (sec && sec.offsetTop - body.offsetTop - 40 <= body.scrollTop) cur = a; });
    if (body.scrollTop + body.clientHeight >= body.scrollHeight - 4) cur = tabs[tabs.length - 1];
    tabs.forEach((a) => a.classList.toggle("is-active", a === cur));
  }, true);

  const render = () => {
    const d = drawer();
    if (!d) return;
    const n = cart.size;
    const badge = d.querySelector("[data-cart-n]"); if (badge) badge.textContent = n;
    const sv = d.querySelector("[data-cart-save]");
    if (sv && sv.textContent === sv.dataset.save) sv.disabled = n === 0;
    const n2 = document.querySelector("[data-cart-n2]");
    if (n2) {
      if (n2.textContent !== String(n)) { n2.classList.remove("is-bump"); void n2.offsetWidth; n2.classList.add("is-bump"); }
      n2.textContent = n; n2.classList.toggle("is-on", n > 0);
    }
    document.querySelectorAll(".cs-pcard [data-cs-cart]").forEach((b) => {
      const on = cart.has(b.closest(".cs-pcard").dataset.code);
      b.setAttribute("aria-pressed", String(on)); b.textContent = on ? b.dataset.on : b.dataset.off;
    });
    syncModalCart();
  };
  const post = async (fields) => {
    const body = new URLSearchParams(fields);
    const r = await fetch(`/c/${encodeURIComponent(convId())}/cart`, { method: "POST", body });
    if (!r.ok) return;
    const box = document.getElementById("cs-cart-body");
    if (box) box.innerHTML = await r.text();
    readCart();
    render();
  };
  const setCart = (code, on) => { if (code) post({ op: on ? "add" : "del", code }); };

  const showDrawer = (on) => {
    const d = drawer(); const t = document.querySelector("[data-cart-toggle]");
    if (!d) return;
    if (on && !d.open) d.showModal();
    if (!on && d.open) d.close();
    t?.setAttribute("aria-expanded", String(on));
  };

  document.addEventListener("click", (e) => {
    const t = e.target instanceof Element ? e.target : null;
    if (!t) return;
    const m = modal(); const d = drawer();
    if (m && e.target === m) { m.close(); return; }
    if (d && e.target === d) { showDrawer(false); return; }
    const explain = t.closest("[data-cs-explain]");
    if (explain) { openModal(explain.closest(".cs-pcard")); return; }
    if (t.closest("[data-pm-cart]")) { setCart(m.dataset.code, !cart.has(m.dataset.code)); return; }
    const cartBtn = t.closest("[data-cs-cart]");
    if (cartBtn) { const c = cartBtn.closest(".cs-pcard"); setCart(c.dataset.code, !cart.has(c.dataset.code)); return; }
    const del = t.closest("[data-cart-del]");
    if (del) { setCart(del.getAttribute("data-cart-del"), false); return; }
    if (t.closest("[data-cart-clear]")) { post({ op: "clear" }); return; }
    const addLine = t.closest("[data-opt-add]");
    if (addLine) {
      const sel = addLine.closest("[data-opt-pick]")?.querySelector("[data-opt-select]");
      const [pk, ok] = (sel?.value || "").split("|");
      if (pk && ok) post({ op: "line_add", code: addLine.dataset.cartCode, product_key: pk, option_key: ok });
      return;
    }
    const delLine = t.closest("[data-line-del]");
    if (delLine) { post({ op: "line_del", code: delLine.dataset.cartCode, idx: delLine.dataset.lineDel }); return; }
    if (t.closest("[data-cart-close]")) { showDrawer(false); return; }
    const cx = t.closest("[data-cart-explain]");
    if (cx) { const c = card(cx.getAttribute("data-cart-explain")); if (c) { showDrawer(false); openModal(c); } return; }
    const save = t.closest("[data-cart-save]");
    if (save && !save.disabled) {
      /* CT7 — 장부 cart_saved(서버) · 표시는 시안 그대로 */
      fetch(`/c/${encodeURIComponent(convId())}/cart/save`, { method: "POST" });
      save.textContent = save.dataset.saved; save.disabled = true;
      setTimeout(() => { save.textContent = save.dataset.save; save.disabled = cart.size === 0; showDrawer(false); }, 900);
      return;
    }
    if (t.closest("[data-cart-toggle]")) { showDrawer(!(d && d.open)); return; }
    if (t.closest("[data-topic-switch]")) { e.preventDefault(); return; } /* IL11 앞 주제 머리 — 펼치지 않고 POST /topic(htmx) */
    const open = t.closest("[data-opt-open]");
    if (open) {
      const box = open.closest("[data-cart-opts]")?.querySelector("[data-opt-pick]");
      if (box) { box.hidden = !box.hidden; open.setAttribute("aria-expanded", String(!box.hidden)); }
      return;
    }
    const c = t.closest(".cs-pcard");
    if (c) pick(c);
  });
  document.addEventListener("close", (e) => {
    if (e.target === drawer()) document.querySelector("[data-cart-toggle]")?.setAttribute("aria-expanded", "false");
  }, true);
  /* 테스트 고침 A-6 — 다음 턴(질문 보냄)에 점 선택을 푼다: 거른 목록이면 전체 목록을 다시 받는다(화면만 · 장부 줄 0) */
  document.addEventListener("htmx:beforeRequest", (e) => {
    if (!e.target?.matches?.("[data-cs-form]")) return;
    const l = document.getElementById("procedure-list");
    if (l && l.dataset.siteFilter && window.htmx) {
      window.htmx.ajax("GET", `/c/${encodeURIComponent(convId())}/items`, { target: "#procedure-list", swap: "outerHTML" });
    }
  });
  /* 목록이 바뀌면(items 이벤트 · 부위 거름) 카드 담김 표시를 다시 맞춘다 */
  document.addEventListener("htmx:afterSettle", render);
  document.addEventListener("DOMContentLoaded", () => { readCart(); render(); });
  document.addEventListener("htmx:sseMessage", render);
})();
