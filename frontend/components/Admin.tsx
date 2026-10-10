"use client";
import { useEffect, useState } from "react";
import { UserPlus, Shield, Camera, Layers, Settings } from "lucide-react";
import { api } from "../lib/api";

export function Admin({
  sites,
  onError,
  onRefresh,
}: {
  sites: any[];
  onError: (s: string) => void;
  onRefresh: () => void;
}) {
  const [people, setPeople] = useState<any[]>([]);
  const [target, setTarget] = useState("");
  const [busy, setBusy] = useState(false);
  const [provider, setProvider] = useState("ollama");
  const [audit, setAudit] = useState<any[]>([]);
  const [editingSite, setEditingSite] = useState("");
  const [person, setPerson] = useState({
    username: "",
    password: "",
    full_name: "",
    personnel_number: "",
    role: "employee",
  });
  const [assignment, setAssignment] = useState({
    principal_id: "",
    site_id: sites[0]?.id || "",
    kind: "employee",
    active: true,
  });
  const [site, setSite] = useState({
    name: "",
    timezone: "Europe/Moscow",
    west: 37.615,
    east: 37.62,
    south: 55.75,
    north: 55.753,
  });
  const refresh = () => {
    api<any[]>("/people")
      .then(setPeople)
      .catch((e) => onError(e.message));
    api<any[]>("/audit")
      .then(setAudit)
      .catch((e) => onError(e.message));
  };
  useEffect(() => {
    refresh();
    api("/settings/model")
      .then((v) => setProvider(v.provider))
      .catch((e) => onError(e.message));
  }, []);
  async function run(action: () => Promise<any>) {
    setBusy(true);
    try {
      await action();
      refresh();
      onRefresh();
    } catch (e: any) {
      onError(e.message);
    } finally {
      setBusy(false);
    }
  }
  async function uploadReferences(files: FileList | null) {
    if (!files || !target) return;
    if (files.length !== 3) {
      onError("Выберите ровно три снимка");
      return;
    }
    const form = new FormData();
    Array.from(files).forEach((file) => form.append("photos", file));
    await run(() =>
      api(`/employees/${target}/references`, { method: "POST", body: form }),
    );
  }
  return (
    <div className="stack">
      <div className="admin-grid">
        <section className="panel">
          <div className="section-heading">
            <h2>Новая учётная запись</h2>
            <UserPlus size={22} />
          </div>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              void run(async () => {
                await api("/people", {
                  method: "POST",
                  body: JSON.stringify(person),
                });
                setPerson({
                  ...person,
                  username: "",
                  password: "",
                  full_name: "",
                  personnel_number: "",
                });
              });
            }}
          >
            <div className="field-grid">
              <label>
                Логин
                <input
                  required
                  value={person.username}
                  onChange={(e) =>
                    setPerson({ ...person, username: e.target.value })
                  }
                />
              </label>
              <label>
                Пароль
                <input
                  required
                  type="password"
                  minLength={12}
                  value={person.password}
                  onChange={(e) =>
                    setPerson({ ...person, password: e.target.value })
                  }
                />
              </label>
              <label>
                ФИО
                <input
                  required
                  value={person.full_name}
                  onChange={(e) =>
                    setPerson({ ...person, full_name: e.target.value })
                  }
                />
              </label>
              <label>
                Табельный номер
                <input
                  required
                  value={person.personnel_number}
                  onChange={(e) =>
                    setPerson({ ...person, personnel_number: e.target.value })
                  }
                />
              </label>
            </div>
            <label>
              Роль
              <select
                value={person.role}
                onChange={(e) => setPerson({ ...person, role: e.target.value })}
              >
                <option value="employee">Сотрудник</option>
                <option value="controller">Вахтёр</option>
                <option value="manager">Руководитель</option>
                <option value="admin">Администратор</option>
              </select>
            </label>
            <button className="primary" disabled={busy}>
              Создать в Keycloak
            </button>
          </form>
        </section>
        <section className="panel">
          <div className="section-heading">
            <h2>Назначение на объект</h2>
            <Shield size={22} />
          </div>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              void run(() =>
                api("/assignments", {
                  method: "POST",
                  body: JSON.stringify(assignment),
                }),
              );
            }}
          >
            <label>
              Пользователь
              <select
                required
                value={assignment.principal_id}
                onChange={(e) => {
                  const selected = people.find(
                    (p) => p.principal_id === e.target.value,
                  );
                  setAssignment({
                    ...assignment,
                    principal_id: e.target.value,
                    kind:
                      selected?.roles.find((r: string) =>
                        ["controller", "manager", "employee"].includes(r),
                      ) || "employee",
                  });
                }}
              >
                <option value="">Выберите пользователя</option>
                {people
                  .filter((p) => !p.roles.includes("admin"))
                  .map((p) => (
                    <option key={p.principal_id} value={p.principal_id}>
                      {p.full_name || p.username} · {p.roles.join(", ")}
                    </option>
                  ))}
              </select>
            </label>
            <label>
              Объект
              <select
                value={assignment.site_id}
                onChange={(e) =>
                  setAssignment({ ...assignment, site_id: e.target.value })
                }
              >
                {sites.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Назначение
              <select
                value={assignment.kind}
                onChange={(e) =>
                  setAssignment({ ...assignment, kind: e.target.value })
                }
              >
                <option value="employee">Сотрудник объекта</option>
                <option value="controller">Вахтёр объекта</option>
                <option value="manager">Руководитель объекта</option>
              </select>
            </label>
            <label className="checkbox">
              <input
                type="checkbox"
                checked={assignment.active}
                onChange={(e) =>
                  setAssignment({ ...assignment, active: e.target.checked })
                }
              />
              Назначение активно
            </label>
            <button className="primary" disabled={busy}>
              Сохранить назначение
            </button>
            <p className="small muted">
              Вахтёр отвечает за один объект. Новое назначение отзывает прежние
              сессии КПП.
            </p>
          </form>
        </section>
        <section className="panel">
          <div className="section-heading">
            <h2>Три эталона лица</h2>
            <Camera size={22} />
          </div>
          <label>
            Сотрудник
            <select value={target} onChange={(e) => setTarget(e.target.value)}>
              <option value="">Выберите сотрудника</option>
              {people
                .filter((p) => p.employee_id)
                .map((p) => (
                  <option key={p.employee_id} value={p.employee_id}>
                    {p.full_name} · эталонов {p.reference_count}
                  </option>
                ))}
            </select>
          </label>
          <div className="reference-slots">
            <span>
              01
              <br />
              <small>Анфас</small>
            </span>
            <span>
              02
              <br />
              <small>Поворот влево</small>
            </span>
            <span>
              03
              <br />
              <small>Поворот вправо</small>
            </span>
          </div>
          <label className="file-input full">
            {busy ? "Проверка и публикация…" : "Выбрать три фотографии"}
            <input
              type="file"
              multiple
              accept="image/*"
              disabled={!target || busy}
              onChange={(e) => void uploadReferences(e.target.files)}
            />
          </label>
          <p className="small muted">
            Сервер проверит качество и совпадение личности. Новый набор
            публикуется целиком.
          </p>
          {target && (
            <button
              onClick={() =>
                void run(() =>
                  api(`/employees/${target}/reset-device`, { method: "POST" }),
                )
              }
            >
              Сбросить браузер сотрудника
            </button>
          )}
        </section>
        <section className="panel">
          <div className="section-heading">
            <h2>Границы объекта</h2>
            <Layers size={22} />
          </div>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              if (!(site.west < site.east && site.south < site.north)) {
                onError("Запад должен быть меньше востока, юг — меньше севера");
                return;
              }
              const boundary = {
                type: "Polygon",
                coordinates: [
                  [
                    [site.west, site.south],
                    [site.east, site.south],
                    [site.east, site.north],
                    [site.west, site.north],
                    [site.west, site.south],
                  ],
                ],
              };
              void run(() =>
                api(editingSite ? `/sites/${editingSite}` : "/sites", {
                  method: editingSite ? "PUT" : "POST",
                  body: JSON.stringify({
                    name: site.name,
                    timezone: site.timezone,
                    boundary,
                  }),
                }),
              );
            }}
          >
            <label>
              Объект
              <select
                value={editingSite}
                onChange={(e) => {
                  setEditingSite(e.target.value);
                  const selected = sites.find((s) => s.id === e.target.value);
                  if (!selected) {
                    setSite({ ...site, name: "" });
                    return;
                  }
                  const points = selected.boundary.coordinates[0];
                  setSite({
                    name: selected.name,
                    timezone: selected.timezone,
                    west: Math.min(...points.map((p: number[]) => p[0])),
                    east: Math.max(...points.map((p: number[]) => p[0])),
                    south: Math.min(...points.map((p: number[]) => p[1])),
                    north: Math.max(...points.map((p: number[]) => p[1])),
                  });
                }}
              >
                <option value="">Создать новый объект</option>
                {sites.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Название
              <input
                required
                value={site.name}
                onChange={(e) => setSite({ ...site, name: e.target.value })}
              />
            </label>
            <label>
              Часовой пояс
              <input
                value={site.timezone}
                onChange={(e) => setSite({ ...site, timezone: e.target.value })}
              />
            </label>
            <div className="field-grid">
              {(["west", "east", "south", "north"] as const).map((key) => (
                <label key={key}>
                  {
                    {
                      west: "Запад",
                      east: "Восток",
                      south: "Юг",
                      north: "Север",
                    }[key]
                  }
                  <input
                    type="number"
                    step="0.000001"
                    value={site[key]}
                    onChange={(e) =>
                      setSite({ ...site, [key]: Number(e.target.value) })
                    }
                  />
                </label>
              ))}
            </div>
            <button className="primary" disabled={busy}>
              {editingSite ? "Сохранить границы" : "Создать объект и хранилище"}
            </button>
            <p className="small muted">
              Граница задаётся прямоугольником WGS84. Изменение отзовёт суточные
              QR объекта. Зоны рисуются отдельно в редакторе схемы.
            </p>
          </form>
        </section>
      </div>
      <section className="panel">
        <div className="section-heading">
          <h2>Пользователи</h2>
          <span className="badge">{people.length}</span>
        </div>
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Сотрудник / логин</th>
                <th>Роль</th>
                <th>Эталоны</th>
                <th>Статус</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {people.map((p) => (
                <tr key={p.principal_id}>
                  <td>
                    <strong>{p.full_name || p.username}</strong>
                    <small>{p.username}</small>
                  </td>
                  <td>
                    {p.roles
                      .map(
                        (r: string) =>
                          (
                            ({
                              admin: "Администратор",
                              manager: "Руководитель",
                              controller: "Вахтёр",
                              employee: "Сотрудник",
                            }) as any
                          )[r],
                      )
                      .join(", ")}
                  </td>
                  <td>{p.employee_id ? `${p.reference_count}/3` : "—"}</td>
                  <td>
                    <span className={`badge ${p.active ? "green" : ""}`}>
                      {p.active ? "Активен" : "Заблокирован"}
                    </span>
                  </td>
                  <td>
                    <button
                      disabled={busy}
                      onClick={() =>
                        void run(() =>
                          api(`/people/${p.principal_id}/active`, {
                            method: "PUT",
                            body: JSON.stringify({ active: !p.active }),
                          }),
                        )
                      }
                    >
                      {p.active ? "Заблокировать" : "Активировать"}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
      <section className="panel">
        <div className="section-heading">
          <h2>Провайдер помощника</h2>
          <Settings size={22} />
        </div>
        <div className="form-row">
          <select
            value={provider}
            onChange={(e) => setProvider(e.target.value)}
          >
            <option value="ollama">Локально · Ollama / Qwen3 0.6B</option>
            <option value="gigachat">Внешний сервис · GigaChat</option>
          </select>
          <button
            onClick={() =>
              void run(() =>
                api("/settings/model", {
                  method: "PUT",
                  body: JSON.stringify({ provider }),
                }),
              )
            }
          >
            Применить
          </button>
        </div>
        <p className="small muted">
          Для GigaChat необходимо настроить ключ и доверенные сертификаты
          сервиса. Ошибка провайдера отображается явно.
        </p>
      </section>
      <section className="panel">
        <h2>Аудит системы</h2>
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Время</th>
                <th>Действие</th>
                <th>Детали</th>
              </tr>
            </thead>
            <tbody>
              {audit.slice(0, 30).map((a) => (
                <tr key={a.id}>
                  <td>{new Date(a.created_at).toLocaleString("ru")}</td>
                  <td>{a.action}</td>
                  <td className="small">{JSON.stringify(a.detail)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
