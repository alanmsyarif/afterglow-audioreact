"use strict";
(() => {
  const input = document.querySelector("#guide-search");
  const clear = document.querySelector("#search-clear");
  const status = document.querySelector("#search-status");
  const sections = [...document.querySelectorAll(".section")];
  const links = [...document.querySelectorAll(".toc a")];
  const normalize = text => text.toLocaleLowerCase().normalize("NFKD").replace(/[\u0300-\u036f]/g, "");
  const searchText = new Map(sections.map(section => [section.id, normalize(section.textContent)]));
  function filter() {
    const words = normalize(input.value.trim()).split(/\s+/).filter(Boolean);
    let count = 0;
    sections.forEach(section => {
      const matches = words.every(word => searchText.get(section.id).includes(word));
      section.hidden = !matches;
      if (matches) count++;
      section.querySelectorAll("details").forEach(detail => {
        if (words.length && matches && words.some(word => normalize(detail.textContent).includes(word))) {
          if (!detail.open) detail.dataset.searchOpened = "true";
          detail.open = true;
        } else if (detail.dataset.searchOpened) {
          detail.open = false;
          delete detail.dataset.searchOpened;
        }
      });
    });
    links.forEach(link => link.hidden = document.querySelector(link.getAttribute("href")).hidden);
    document.querySelectorAll(".nav-group").forEach(group => {
      let next = group.nextElementSibling;
      let visible = false;
      while (next && !next.classList.contains("nav-group")) {
        if (next.tagName === "A" && !next.hidden) visible = true;
        next = next.nextElementSibling;
      }
      group.hidden = !visible;
    });
    clear.hidden = !words.length;
    status.textContent = words.length ? `${count} of ${sections.length} topics match` : "Type a feature or a button name";
    document.querySelector("#no-results").hidden = count !== 0;
  }
  input.addEventListener("input", filter);
  clear.addEventListener("click", () => { input.value = ""; filter(); input.focus(); });
  document.querySelectorAll('a[href^="#"]').forEach(link => link.addEventListener("click", () => {
    input.value = "";
    filter();
    const mobile = document.querySelector("#mobile-contents");
    if (mobile) mobile.open = false;
  }));
  document.addEventListener("keydown", event => {
    if (event.key === "/" && !/^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName) && !event.ctrlKey && !event.metaKey) {
      event.preventDefault(); input.focus();
    }
    if (event.key === "Escape" && document.activeElement === input && input.value) {
      input.value = ""; filter();
    }
  });
  const dialog = document.querySelector("#image-dialog");
  const zoomImage = dialog.querySelector("img");
  document.querySelectorAll(".visual-open").forEach(button => button.addEventListener("click", () => {
    const figure = button.closest("figure");
    const source = button.querySelector("img");
    zoomImage.src = source.src;
    zoomImage.alt = source.alt;
    dialog.querySelector(".zoom-caption").textContent = figure.querySelector(".caption-copy").textContent;
    if (dialog.showModal) dialog.showModal();
    else dialog.setAttribute("open", "");
  }));
  dialog.querySelector(".zoom-close").addEventListener("click", () => dialog.close());
  dialog.addEventListener("click", event => { if (event.target === dialog) dialog.close(); });
  dialog.addEventListener("close", () => zoomImage.removeAttribute("src"));
  document.querySelectorAll(".media-toggle").forEach(button => {
    const image = button.closest("figure").querySelector("img");
    image.manualPoster = image.src;
    button.addEventListener("click", () => {
      const play = button.getAttribute("aria-pressed") !== "true";
      image.src = play ? button.dataset.animation : image.manualPoster;
      button.setAttribute("aria-pressed", String(play));
      button.textContent = play ? "Pause animation" : "Play animation";
    });
  });
  const observer = "IntersectionObserver" in window ? new IntersectionObserver(entries => {
    const visible = entries.filter(entry => entry.isIntersecting).sort((a,b) => a.boundingClientRect.top - b.boundingClientRect.top);
    if (!visible.length || input.value.trim()) return;
    links.forEach(link => {
      if (link.getAttribute("href") === `#${visible[0].target.id}`) link.setAttribute("aria-current", "location");
      else link.removeAttribute("aria-current");
    });
  }, {rootMargin:"-5% 0px -65% 0px"}) : null;
  if (observer) sections.forEach(section => observer.observe(section));
  let printState = [];
  let printQuery = null;
  let printMedia = [];
  window.addEventListener("beforeprint", () => {
    if (printQuery !== null) return;
    printQuery = input.value;
    input.value = "";
    filter();
    printState = [...document.querySelectorAll("details")].map(detail => [detail, detail.open]);
    printState.forEach(([detail]) => detail.open = true);
    printMedia = [...document.querySelectorAll(".media-toggle")].map(button => {
      const image = button.closest("figure").querySelector("img");
      const source = image.src;
      image.src = image.manualPoster;
      return [image, source];
    });
  });
  window.addEventListener("afterprint", () => {
    if (printQuery === null) return;
    printState.forEach(([detail, open]) => detail.open = open);
    printMedia.forEach(([image, source]) => image.src = source);
    input.value = printQuery;
    printQuery = null;
    filter();
  });
  document.querySelectorAll(".print-guide").forEach(button => button.addEventListener("click", () => window.print()));
  filter();
})();
