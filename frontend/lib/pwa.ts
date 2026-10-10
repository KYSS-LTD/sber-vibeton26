import { useEffect, useState } from "react";

type InstallEvent = Event & {
  prompt(): Promise<void>;
  userChoice: Promise<{ outcome: "accepted" | "dismissed" }>;
};

let deferred: InstallEvent | null = null;
const listeners = new Set<() => void>();
const notify = () => listeners.forEach((listener) => listener());

// Браузер присылает beforeinstallprompt один раз и рано, поэтому слушатель ставится при загрузке модуля.
if (typeof window !== "undefined") {
  window.addEventListener("beforeinstallprompt", (event) => {
    event.preventDefault();
    deferred = event as InstallEvent;
    notify();
  });
  window.addEventListener("appinstalled", () => {
    deferred = null;
    notify();
  });
}

export function registerWorker() {
  // В разработке файлы меняются без смены имени, поэтому worker не кэширует (см. public/sw.js).
  const url = process.env.NODE_ENV === "production" ? "/sw.js" : "/sw.js?dev";
  return navigator.serviceWorker.register(url, { scope: "/" });
}

function isStandalone() {
  return (
    matchMedia("(display-mode: standalone)").matches ||
    (navigator as any).standalone === true
  );
}

// iOS Safari не поддерживает beforeinstallprompt: установка только через меню «Поделиться».
function isIos() {
  return (
    /iphone|ipad|ipod/i.test(navigator.userAgent) ||
    (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1)
  );
}

export function useInstall() {
  const [, rerender] = useState(0);
  const [state, setState] = useState({ standalone: false, ios: false });
  useEffect(() => {
    setState({ standalone: isStandalone(), ios: isIos() });
    const update = () => rerender((n) => n + 1);
    listeners.add(update);
    return () => {
      listeners.delete(update);
    };
  }, []);
  async function install() {
    const event = deferred;
    if (!event) return;
    deferred = null;
    notify();
    await event.prompt();
    await event.userChoice;
  }
  return {
    installed: state.standalone,
    canInstall: !!deferred,
    showIosHint: state.ios && !deferred,
    install,
  };
}
