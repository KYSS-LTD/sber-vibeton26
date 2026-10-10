const TEXT: Record<string, string> = {
  shift_opened: "Вы вошли на объект — смена открыта",
  shift_closed: "Вы вышли с объекта — смена закрыта",
  schedule: "Скоро начнётся запланированная смена",
  alert: "Новое предупреждение по объекту",
};

export function notificationText(n: {
  kind: string;
  payload?: { detail?: string };
}) {
  return n.payload?.detail || TEXT[n.kind] || "Новое уведомление";
}
