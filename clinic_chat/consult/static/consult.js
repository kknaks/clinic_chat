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
