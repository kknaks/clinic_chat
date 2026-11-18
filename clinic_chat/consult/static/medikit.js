/* medikit × HTMX 공통 동작 — 프레임워크 없음, eval 없음(CSP 가능). <script defer src="medikit.js"> 로 쓴다.
 * 속성 규칙:
 *   #ms-progress                          HTMX 요청 진행 표시줄
 *   <dialog data-open-on-load>            서버가 #modal-root 에 넣으면 열기 · 닫히면 제거 · 바깥 클릭 닫기
 *   [data-close-dialog]                   가장 가까운 dialog 닫기
 *   input[data-filter-list="tbody-id"]    #tbody-id [data-filter-text] 행을 화면 안에서 거른다
 *   input[data-check-all="name"]          같은 form 의 name 체크박스 전체 선택
 *   [data-add-row="tbody-id"] / [data-remove-row]   <template id="tbody-id-template"> 행 추가 · 삭제
 *   [data-row-link]                       Enter = 클릭
 */
(() => {
  "use strict";

  const progress = () => document.getElementById("ms-progress");
  document.addEventListener("htmx:beforeRequest", () => progress()?.classList.add("is-active"));
  document.addEventListener("htmx:afterRequest", () => progress()?.classList.remove("is-active"));

  const openDialogs = (root) => {
    root.querySelectorAll("dialog[data-open-on-load]").forEach((dialog) => {
      if (!dialog.open) dialog.showModal();
      dialog.removeAttribute("data-open-on-load");
      dialog.addEventListener("close", () => dialog.remove(), { once: true });
      dialog.addEventListener("click", (event) => {
        if (event.target === dialog) dialog.close();
      });
    });
  };
  document.addEventListener("htmx:afterSwap", (event) => openDialogs(event.detail?.target || document));
  document.addEventListener("DOMContentLoaded", () => openDialogs(document));

  document.addEventListener("click", (event) => {
    const target = event.target instanceof Element ? event.target : null;
    target?.closest("[data-close-dialog]")?.closest("dialog")?.close();

    const add = target?.closest("[data-add-row]");
    if (add) {
      const id = add.getAttribute("data-add-row");
      const template = document.getElementById(`${id}-template`);
      const body = document.getElementById(id);
      if (template instanceof HTMLTemplateElement && body) {
        body.append(template.content.cloneNode(true));
        body.lastElementChild?.querySelector("input, select")?.focus();
      }
      return;
    }
    target?.closest("[data-remove-row]")?.closest("tr")?.remove();
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && event.target instanceof HTMLElement && event.target.matches("[data-row-link]")) {
      event.target.click();
    }
  });

  document.addEventListener("input", (event) => {
    const input = event.target;
    if (!(input instanceof HTMLInputElement) || !input.dataset.filterList) return;
    const query = input.value.trim().toLowerCase();
    document.querySelectorAll(`#${input.dataset.filterList} [data-filter-text]`).forEach((item) => {
      item.hidden = Boolean(query) && !item.getAttribute("data-filter-text").toLowerCase().includes(query);
    });
  });

  document.addEventListener("change", (event) => {
    const box = event.target;
    if (!(box instanceof HTMLInputElement) || !box.dataset.checkAll || !box.form) return;
    box.form.querySelectorAll(`input[name="${box.dataset.checkAll}"]:not(:disabled)`).forEach((item) => {
      item.checked = box.checked;
    });
  });
})();
