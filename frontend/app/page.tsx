"use client";
import { useEffect, useState } from "react";
import Keycloak from "keycloak-js";
import {
  LayoutDashboard,
  Map,
  ScanLine,
  Users,
  Settings,
  LogOut,
  ChevronDown,
  ArrowUpRight,
  ShieldCheck,
  Sparkles,
  Layers,
  Menu,
} from "lucide-react";
import { api, setAuth } from "../lib/api";
import { Worker } from "../components/Worker";
import { Gate } from "../components/Gate";
import { Admin } from "../components/Admin";
import { Manager } from "../components/Manager";

let loginPromise: Promise<boolean> | null = null;
let keycloak: Keycloak | null = null;
const labels: Record<string, string> = {
  dashboard: "Обзор объекта",
  heatmap: "Heatmap посещений",
  zones: "Схема и зоны",
  assistant: "Помощник",
  gate: "Проходная",
  worker: "Мой пропуск",
  admin: "Управление",
};
const roleLabels: Record<string, string> = {
  admin: "Администратор",
  manager: "Руководитель",
  controller: "Вахтёр",
  employee: "Сотрудник",
};

export default function Page() {
  const [loaded, setLoaded] = useState(false);
  const [authenticated, setAuthenticated] = useState(false);
  const [me, setMe] = useState<any>(null);
  const [siteId, setSiteId] = useState("");
  const [tab, setTab] = useState("");
  const [toast, setToast] = useState("");
  const [mobile, setMobile] = useState(false);
  const error = (text: string) => setToast(text);
  async function refresh() {
    try {
      const value = await api("/me");
      setMe(value);
      setSiteId(
        (current) =>
          current || value.controller_site_id || value.sites[0]?.id || "",
      );
    } catch (e: any) {
      error(e.message);
    }
  }
  useEffect(() => {
    if (!keycloak) {
      keycloak = new Keycloak({
        url: location.origin + "/auth",
        realm: "prohod",
        clientId: "prohod-web",
      });
      setAuth(keycloak);
    }
    if (!loginPromise)
      loginPromise = keycloak.init({
        pkceMethod: "S256",
        checkLoginIframe: false,
      });
    loginPromise
      .then(async (success) => {
        setAuthenticated(success);
        setLoaded(true);
        if (success) {
          await refresh();
        }
      })
      .catch(() => {
        error("Keycloak недоступен. Проверьте запуск контейнеров.");
        setLoaded(true);
      });
  }, []);
  useEffect(() => {
    if (me) {
      if (me.roles.includes("admin")) setTab("dashboard");
      else if (me.roles.includes("manager")) setTab("dashboard");
      else if (me.roles.includes("controller")) setTab("gate");
      else setTab("worker");
    }
  }, [me?.id]);
  useEffect(() => {
    if (!toast) return;
    const timer = setTimeout(() => setToast(""), 8000);
    return () => clearTimeout(timer);
  }, [toast]);
  if (!authenticated)
    return (
      <main className="landing">
        <header className="landing-header">
          <a className="brand">
            <span className="brand-mark">
              <Layers size={24} />
            </span>
            проход<span className="brand-sub">контроль объекта</span>
          </a>
          <span className="badge">Единая система присутствия</span>
        </header>
        <div className="landing-grid">
          <div>
            <span className="eyebrow">СТРОИТЕЛЬНАЯ ПЛОЩАДКА ПОД КОНТРОЛЕМ</span>
            <h1>
              Каждый проход.
              <br />
              Каждая смена.
              <br />
              <em>На своём месте.</em>
            </h1>
            <p>
              Личный пропуск, проверка лица и журнал присутствия. Всё, что нужно
              сотруднику, проходной и руководителю — в одном окне.
            </p>
            <button
              className="primary login-button"
              disabled={!loaded}
              onClick={() => keycloak?.login()}
            >
              {loaded ? "Войти в систему" : "Подключение…"}
              <ArrowUpRight size={20} />
            </button>
            <small>Защищённый вход через Keycloak</small>
          </div>
          <div className="landing-visual">
            <div className="schematic">
              <div className="building b1" />
              <div className="building b2" />
              <div className="building b3" />
              <div className="road" />
              <div className="site-dot d1" />
              <div className="site-dot d2" />
              <div className="site-dot d3" />
              <div className="checkpoint">
                <ShieldCheck size={24} />
                КПП
              </div>
            </div>
            <div className="visual-caption">
              <span className="status-dot ok" />
              <div>
                <strong>Присутствие, подтверждённое системой</strong>
                <small>От проходной до отчёта руководителю</small>
              </div>
            </div>
          </div>
        </div>
        <footer>
          Проход · рабочее пространство объекта{" "}
          <span>Веб-интерфейс для телефона, планшета и компьютера</span>
        </footer>
        {toast && (
          <div className="toast" role="alert">
            {toast}
            <button onClick={() => setToast("")}>×</button>
          </div>
        )}
      </main>
    );
  if (!me || !tab)
    return (
      <main className="loading">
        <span className="brand-mark">
          <Layers />
        </span>
        <p>Загружаем ваше рабочее пространство…</p>
        {toast && <p role="alert">{toast}</p>}
      </main>
    );
  const manage = me.roles.includes("admin") || me.roles.includes("manager");
  const nav = [
    ...(manage
      ? [
          ["dashboard", LayoutDashboard],
          ["heatmap", Map],
          ["zones", Layers],
          ["assistant", Sparkles],
        ]
      : []),
    ...(me.roles.includes("controller") ? [["gate", ScanLine]] : []),
    ...(me.employee ? [["worker", ShieldCheck]] : []),
    ...(me.roles.includes("admin") ? [["admin", Settings]] : []),
  ] as const;
  const site = me.sites.find((s: any) => s.id === siteId);
  const siteOptions =
    tab === "gate"
      ? me.sites.filter((s: any) => s.id === me.controller_site_id)
      : me.sites;
  return (
    <div className="app-shell">
      <aside className={mobile ? "sidebar open" : "sidebar"}>
        <a className="brand">
          <span className="brand-mark">
            <Layers size={23} />
          </span>
          проход
        </a>
        <span className="sidebar-caption">РАБОЧЕЕ ПРОСТРАНСТВО</span>
        <nav>
          {nav.map(([id, Icon]: any) => (
            <button
              key={id}
              className={tab === id ? "active" : ""}
              onClick={() => {
                setTab(id);
                if (id === "gate") setSiteId(me.controller_site_id || "");
                setMobile(false);
              }}
            >
              <Icon size={19} />
              {labels[id]}
            </button>
          ))}
        </nav>
        <div className="sidebar-footer">
          <div className="user-avatar">
            {me.username.slice(0, 2).toUpperCase()}
          </div>
          <div>
            <strong>{me.username}</strong>
            <small>
              {me.roles.map((r: string) => roleLabels[r]).join(", ")}
            </small>
          </div>
          <button
            aria-label="Выйти"
            onClick={() => keycloak?.logout({ redirectUri: location.origin })}
          >
            <LogOut size={18} />
          </button>
        </div>
      </aside>
      <main className="workspace">
        <header className="topbar">
          <div className="topbar-left">
            <button
              className="mobile-menu"
              onClick={() => setMobile(!mobile)}
              aria-label="Меню"
            >
              <Menu />
            </button>
            <span className="breadcrumb">
              Рабочее пространство <span>/</span> {labels[tab]}
            </span>
          </div>
          <div className="site-select">
            <Map size={16} />
            <select value={siteId} onChange={(e) => setSiteId(e.target.value)}>
              {siteOptions.map((s: any) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                  {!s.active ? " · настройка" : ""}
                </option>
              ))}
            </select>
            <ChevronDown size={14} />
          </div>
        </header>
        <div className="page-content">
          <div className="page-heading">
            <div>
              <span className="eyebrow">
                {site?.name || "СИСТЕМА ПРОХОДНОЙ"}
              </span>
              <h1>{labels[tab]}</h1>
              <p>
                {tab === "dashboard"
                  ? "Присутствие и смены на вашей площадке"
                  : tab === "heatmap"
                    ? "Где работают люди и как используется пространство"
                    : tab === "gate"
                      ? "Подтверждение личности на входе и выходе"
                      : tab === "admin"
                        ? "Учётные записи, объекты и права доступа"
                        : "Единые данные. Понятные действия."}
              </p>
            </div>
            <span className="date-label">
              {new Date().toLocaleDateString("ru", {
                day: "numeric",
                month: "long",
                year: "numeric",
              })}
            </span>
          </div>
          {tab === "admin" ? (
            <Admin sites={me.sites} onError={error} onRefresh={refresh} />
          ) : !site ? (
            <section className="panel empty">
              Объект не назначен. Обратитесь к администратору.
            </section>
          ) : tab === "worker" && me.employee ? (
            <Worker key={siteId} me={me} siteId={siteId} onError={error} />
          ) : tab === "gate" ? (
            <Gate key={siteId} siteId={siteId} onError={error} />
          ) : (
            <Manager key={siteId} site={site} tab={tab} onError={error} />
          )}
        </div>
        <footer className="workspace-footer">
          Проход · система присутствия
          <span>Доступ ограничен вашими назначениями</span>
        </footer>
      </main>
      {toast && (
        <div className="toast" role="alert">
          {toast}
          <button onClick={() => setToast("")}>×</button>
        </div>
      )}
    </div>
  );
}
