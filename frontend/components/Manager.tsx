"use client";
import { useEffect, useState } from "react";
import {
  Users,
  Clock,
  MapPin,
  ArrowUpRight,
  Download,
  Sparkles,
  RefreshCw,
} from "lucide-react";
import { api, mediaBlob, snapshots } from "../lib/api";
import { enablePush } from "../lib/push";
import { notificationText } from "../lib/notifications";
import { GeoMap } from "./GeoMap";
import { MapEditor } from "./MapEditor";
import { Photo } from "./Photo";

function localInput(date: Date) {
  return new Date(date.getTime() - date.getTimezoneOffset() * 60000)
    .toISOString()
    .slice(0, 16);
}
export function Manager({
  site,
  tab,
  onError,
}: {
  site: any;
  tab: string;
  onError: (s: string) => void;
}) {
  const [people, setPeople] = useState<any[]>([]);
  const [report, setReport] = useState<any>(null);
  const [map, setMap] = useState<any>({ zones: [] });
  const [background, setBackground] = useState("");
  const [routes, setRoutes] = useState<any[]>([]);
  const [start, setStart] = useState(
    localInput(new Date(new Date().setHours(0, 0, 0, 0))),
  );
  const [end, setEnd] = useState(localInput(new Date()));
  const [filter, setFilter] = useState<string[]>([]);
  const [roster, setRoster] = useState<any[]>([]);
  const [question, setQuestion] = useState(
    "Сколько людей сейчас на объекте и сколько часов отработано сегодня?",
  );
  const [answer, setAnswer] = useState<any>(null);
  const [thinking, setThinking] = useState(false);
  const [alerts, setAlerts] = useState<any[]>([]);
  const [notifications, setNotifications] = useState<any[]>([]);
  const [schedule, setSchedule] = useState({
    employee_id: "",
    starts_at: localInput(new Date()),
    ends_at: localInput(new Date(Date.now() + 8 * 3600000)),
  });
  const query = () => {
    const p = new URLSearchParams({
      start: new Date(start).toISOString(),
      end: new Date(end).toISOString(),
    });
    filter.forEach((id) => p.append("employee_ids", id));
    return p.toString();
  };
  async function refresh() {
    try {
      const [onsite, analytics, mapData, routeData, warnings] =
        await Promise.all([
          api(`/sites/${site.id}/onsite`),
          api(`/sites/${site.id}/analytics?${query()}`),
          api(`/sites/${site.id}/map`),
          api(`/sites/${site.id}/routes?${query()}`),
          api(`/sites/${site.id}/alerts`),
        ]);
      setPeople(onsite);
      setReport(analytics);
      setMap(mapData);
      setRoutes(routeData);
      setAlerts(warnings);
    } catch (e: any) {
      onError(e.message);
    }
  }
  useEffect(() => {
    void refresh();
    api<any[]>(`/people?site_id=${site.id}`)
      .then((rows) => setRoster(rows.filter((p) => p.employee_id)))
      .catch((e) => onError(e.message));
    const timer = setInterval(() => {
      api<any[]>(`/sites/${site.id}/onsite`)
        .then(setPeople)
        .catch(() => {});
      api<any[]>(`/sites/${site.id}/alerts`)
        .then(setAlerts)
        .catch(() => {});
    }, 10000);
    return () => clearInterval(timer);
  }, [site.id]);
  useEffect(() => {
    const controller = new AbortController();
    snapshots(
      (value) => setNotifications(value.notifications),
      controller.signal,
    );
    return () => controller.abort();
  }, []);
  useEffect(() => {
    let url = "";
    if (map.version?.media_id)
      mediaBlob(map.version.media_id)
        .then((blob) => {
          url = URL.createObjectURL(blob);
          setBackground(url);
        })
        .catch(() => {});
    else setBackground("");
    return () => {
      if (url) URL.revokeObjectURL(url);
    };
  }, [map.version?.media_id]);
  async function ask() {
    setThinking(true);
    try {
      setAnswer(
        await api("/assistant", {
          method: "POST",
          body: JSON.stringify({
            site_id: site.id,
            question,
            start: new Date(start).toISOString(),
            end: new Date(end).toISOString(),
            employee_ids: filter.length ? filter : null,
          }),
        }),
      );
    } catch (e: any) {
      onError(e.message);
    } finally {
      setThinking(false);
    }
  }
  async function exportCSV() {
    try {
      const value = await api(`/sites/${site.id}/report?${query()}`);
      const blob = await mediaBlob(value.media_id);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "report.csv";
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (e: any) {
      onError(e.message);
    }
  }
  if (tab === "zones")
    return (
      <MapEditor
        key={site.id}
        site={site}
        onError={onError}
        onSaved={refresh}
      />
    );
  const hours =
    report?.employees.reduce((a: number, r: any) => a + r.hours, 0) || 0;
  return (
    <div className="stack">
      <div className="metrics">
        <div className="metric">
          <div>
            <span>Сейчас на объекте</span>
            <Users size={20} />
          </div>
          <strong>
            {people.length}
            <small>чел.</small>
          </strong>
          <p>По открытым сменам</p>
        </div>
        <div className="metric">
          <div>
            <span>Отработано за период</span>
            <Clock size={20} />
          </div>
          <strong>
            {hours.toFixed(1)}
            <small>ч.</small>
          </strong>
          <p>{report?.employees.length || 0} сотрудников</p>
        </div>
        <div className="metric">
          <div>
            <span>Без свежей позиции</span>
            <MapPin size={20} />
          </div>
          <strong>
            {people.filter((p) => p.location_unknown || p.suspicious).length}
            <small>чел.</small>
          </strong>
          <p>Позиция устарела или требует проверки</p>
        </div>
      </div>
      <section className="panel filters">
        <label>
          Начало периода
          <input
            type="datetime-local"
            value={start}
            onChange={(e) => setStart(e.target.value)}
          />
        </label>
        <label>
          Конец периода
          <input
            type="datetime-local"
            value={end}
            onChange={(e) => setEnd(e.target.value)}
          />
        </label>
        <label>
          Сотрудники
          <select
            multiple
            value={filter}
            onChange={(e) =>
              setFilter(
                Array.from(e.target.selectedOptions).map((o) => o.value),
              )
            }
          >
            {roster.map((p) => (
              <option key={p.employee_id} value={p.employee_id}>
                {p.full_name}
              </option>
            ))}
          </select>
        </label>
        <button onClick={refresh}>
          <RefreshCw size={16} />
          Показать
        </button>
        <button onClick={exportCSV}>
          <Download size={16} />
          Отчёт CSV
        </button>
      </section>
      {tab === "assistant" ? (
        <section className="panel assistant">
          <div className="section-heading">
            <div>
              <span className="eyebrow">НА ОСНОВЕ ДАННЫХ ОБЪЕКТА</span>
              <h2>Помощник руководителя</h2>
            </div>
            <Sparkles size={24} />
          </div>
          <p className="muted">
            Задайте вопрос о присутствии, сменах, зонах или отказах за выбранный
            период.
          </p>
          <textarea
            rows={3}
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
          />
          <button className="primary" onClick={ask} disabled={thinking}>
            {thinking
              ? "Получаем данные и готовим сводку…"
              : "Спросить помощника"}
            <ArrowUpRight size={17} />
          </button>
          {answer && (
            <div className="assistant-answer">
              <span className="badge">
                {answer.provider} · {answer.tools.join(", ")}
              </span>
              <p>{answer.text}</p>
              <small>
                Период ответа: {new Date(answer.start).toLocaleString("ru")} —{" "}
                {new Date(answer.end).toLocaleString("ru")}
              </small>
              <small>{answer.location_note}</small>
              <details>
                <summary>Факты из базы данных</summary>
                {answer.facts.map((f: any) => (
                  <p key={f.id} id={`fact-${f.id}`}>
                    <code>{f.id}</code> {f.text}
                  </p>
                ))}
              </details>
            </div>
          )}
        </section>
      ) : (
        <section className="panel map-panel">
          <div className="section-heading">
            <div>
              <span className="eyebrow">
                {tab === "heatmap"
                  ? "ОЦЕНОЧНЫЕ ЧЕЛОВЕКО-МИНУТЫ"
                  : "ПРИСУТСТВИЕ НА ПЛОЩАДКЕ"}
              </span>
              <h2>
                {tab === "heatmap" ? "Heatmap посещений" : "Карта объекта"}
              </h2>
            </div>
            <span className="badge">GPS раз в 10 минут</span>
          </div>
          <GeoMap
            boundary={site.boundary}
            zones={tab === "heatmap" ? report?.heatmap.zones : map.zones}
            people={people}
            routes={routes}
            heat={tab === "heatmap"}
            fitKey={site.id}
            bounds={map.version?.transform}
            background={background}
          />
          {tab === "heatmap" && (
            <p className="small muted">
              Последняя точка учитывается максимум 10 минут. Серый — нет данных
              о посещении. Подозрительные координаты исключены. Обновление
              разметки не переписывает историю.
              {report && (
                <>
                  {" "}
                  Из {report.heatmap.coverage.shift_person_minutes.toFixed(
                    1,
                  )}{" "}
                  чел·мин смен наблюдения покрывают{" "}
                  {report.heatmap.coverage.observed_person_minutes.toFixed(1)};
                  без достоверных координат —{" "}
                  {report.heatmap.coverage.unknown_person_minutes.toFixed(1)}{" "}
                  чел·мин.
                </>
              )}
            </p>
          )}
        </section>
      )}
      {tab === "heatmap" && (
        <section className="panel">
          <h2>Посещение зон</h2>
          <div className="zone-stat-list">
            {report?.heatmap.zones.map((z: any) => (
              <div className="zone-stat" key={z.id}>
                <strong>{z.name}</strong>
                <div className="bar">
                  <span
                    style={{
                      width: `${Math.min(100, (z.person_minutes / Math.max(1, ...report.heatmap.zones.map((r: any) => r.person_minutes))) * 100)}%`,
                    }}
                  />
                </div>
                <span>
                  {z.samples === 0
                    ? "Нет посещений в данных"
                    : `${z.person_minutes.toFixed(1)} чел·мин`}
                </span>
              </div>
            ))}
          </div>
          {!report?.heatmap.zones.length && (
            <p className="empty">Создайте зоны в редакторе площадки</p>
          )}
        </section>
      )}
      <section className="panel">
        <div className="section-heading">
          <h2>Сотрудники на объекте</h2>
          <span className="badge">{people.length}</span>
        </div>
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Сотрудник</th>
                <th>Начало смены</th>
                <th>На смене</th>
                <th>Местоположение</th>
              </tr>
            </thead>
            <tbody>
              {people.map((p) => (
                <tr key={p.employee_id}>
                  <td>
                    <div className="person-cell">
                      <Photo id={p.photo_id} />
                      <strong>{p.full_name}</strong>
                    </div>
                  </td>
                  <td>{new Date(p.opened_at).toLocaleTimeString("ru")}</td>
                  <td>{Number(p.hours).toFixed(1)} ч</td>
                  <td>
                    <span
                      className={`badge ${p.location_unknown || p.suspicious ? "" : "green"}`}
                    >
                      {p.suspicious
                        ? "Сомнительные координаты"
                        : p.location_unknown
                          ? "Неизвестно"
                          : p.inside
                            ? "На объекте"
                            : "За границей"}
                    </span>
                    {p.location_at && (
                      <small>
                        {new Date(p.location_at).toLocaleTimeString("ru")}
                      </small>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {!people.length && <p className="empty">Открытых смен пока нет</p>}
        </div>
      </section>
      <section className="panel">
        <div className="section-heading">
          <h2>Предупреждения объекта</h2>
          <button onClick={() => enablePush().catch((e) => onError(e.message))}>
            Разрешить уведомления
          </button>
        </div>
        {alerts.slice(0, 20).map((a) => (
          <div className="list-row" key={a.id}>
            <span>
              <strong>{a.full_name}</strong> · {a.detail}
            </span>
            <small>{new Date(a.created_at).toLocaleString("ru")}</small>
          </div>
        ))}
        {!alerts.length && <p className="empty">Предупреждений пока нет</p>}
        {!!notifications.length && (
          <details>
            <summary>Мои последние уведомления</summary>
            {notifications.slice(0, 5).map((n) => (
              <p key={n.id}>
                {notificationText(n)} ·{" "}
                {new Date(n.created_at).toLocaleString("ru")}
              </p>
            ))}
          </details>
        )}
      </section>
      <section className="panel">
        <h2>Отработанные смены за период</h2>
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Сотрудник</th>
                <th>Смен</th>
                <th>Часы</th>
              </tr>
            </thead>
            <tbody>
              {report?.employees.map((p: any) => (
                <tr key={p.employee_id}>
                  <td>{p.full_name}</td>
                  <td>{p.shifts}</td>
                  <td>{p.hours.toFixed(2)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
      <section className="panel">
        <h2>Запланировать смену</h2>
        <form
          className="filters"
          onSubmit={(e) => {
            e.preventDefault();
            api("/schedules", {
              method: "POST",
              body: JSON.stringify({
                employee_id: schedule.employee_id,
                site_id: site.id,
                starts_at: new Date(schedule.starts_at).toISOString(),
                ends_at: new Date(schedule.ends_at).toISOString(),
              }),
            })
              .then(() => onError("Смена добавлена в расписание"))
              .catch((e) => onError(e.message));
          }}
        >
          <label>
            Сотрудник
            <select
              required
              value={schedule.employee_id}
              onChange={(e) =>
                setSchedule({ ...schedule, employee_id: e.target.value })
              }
            >
              <option value="">Выберите</option>
              {roster.map((p) => (
                <option key={p.employee_id} value={p.employee_id}>
                  {p.full_name}
                </option>
              ))}
            </select>
          </label>
          <label>
            Начало
            <input
              type="datetime-local"
              value={schedule.starts_at}
              onChange={(e) =>
                setSchedule({ ...schedule, starts_at: e.target.value })
              }
            />
          </label>
          <label>
            Конец
            <input
              type="datetime-local"
              value={schedule.ends_at}
              onChange={(e) =>
                setSchedule({ ...schedule, ends_at: e.target.value })
              }
            />
          </label>
          <button className="primary">Добавить</button>
        </form>
      </section>
    </div>
  );
}
