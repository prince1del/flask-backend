function go(name) {
  const screens = document.querySelectorAll(".screen");
  screens.forEach((s) => s.classList.toggle("is-active", s.dataset.screen === name));

  const tabs = document.querySelectorAll(".tab");
  const tabNames = ["home", "orders", "match", "more"];
  tabs.forEach((t) => {
    const target = t.dataset.go;
    t.classList.toggle("is-active", target === name || (name === "login" && false));
    if (tabNames.includes(name)) {
      t.classList.toggle("is-active", target === name);
    }
  });
}

document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => go(tab.dataset.go));
});

document.querySelectorAll(".theme-swatch").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".theme-swatch").forEach((b) => b.classList.remove("is-on"));
    btn.classList.add("is-on");
    document.documentElement.setAttribute("data-preview-theme", btn.dataset.theme);
  });
});

// Start on login
go("login");
