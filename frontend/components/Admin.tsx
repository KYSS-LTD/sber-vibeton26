"use client";
import { useEffect, useState } from "react";
import { UserPlus, Shield, Camera, Settings } from "lucide-react";
import { api } from "../lib/api";
import { BoundaryEditor } from "./BoundaryEditor";

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
              <small>Анфас, другой кадр</small>
            </span>
            <span>
              03
              <br />
              <small>Анфас, другой кадр</small>
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
            На КПП сотрудник смотрит только прямо, поэтому нужны три разных
            снимка анфас (разная мимика или освещение); лёгкий поворот головы
            допустим. Сервер проверит качество и совпадение личности. Новый
            набор публикуется целиком.
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
      </div>
      <BoundaryEditor sites={sites} busy={busy} run={run} onError={onError} />
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
