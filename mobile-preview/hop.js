function hopGo(name) {
  const map = {
    login: "s-login",
    home: "s-home",
    parties: "s-parties",
    sale: "s-sale",
    projects: "s-projects",
    hub: "s-hub",
    menu: "s-menu",
  };
  const id = map[name] || "s-home";
  document.querySelectorAll(".screen").forEach((s) => {
    s.classList.toggle("active", s.id === id);
  });
  const tabMap = { home: "home", parties: "parties", sale: "sale", projects: "projects", menu: "menu", hub: "projects" };
  const tab = tabMap[name];
  document.querySelectorAll(".tabbar .t").forEach((btn) => {
    btn.classList.toggle("on", btn.dataset.go === tab);
  });
}

document.querySelectorAll(".tabbar .t").forEach((btn) => {
  btn.addEventListener("click", () => hopGo(btn.dataset.go));
});

const start = new URLSearchParams(location.search).get("screen") || "login";
hopGo(start);
