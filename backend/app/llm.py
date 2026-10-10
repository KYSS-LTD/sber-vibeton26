"""The sole model gateway. Models select tools; only trusted code executes SQL."""

import json
import os
from pathlib import Path
import re
import ssl
import time
from uuid import uuid4
import httpx
from .db import transaction
from .analytics import tool_query

PROMPTS = Path(__file__).parent.parent / "prompts"
TOOLS = json.loads((PROMPTS / "tools.json").read_text(encoding="utf-8"))


def parse_object(text):
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text).strip()
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError("Model must return an object")
    return value


class ModelGateway:
    def __init__(self):
        self.token = None
        self.expires = 0

    def complete(self, provider, prompt, data, schema):
        messages = [
            {"role": "system", "content": prompt},
            {
                "role": "user",
                "content": json.dumps(data, ensure_ascii=False, default=str),
            },
        ]
        if provider == "ollama":
            response = httpx.post(
                os.getenv("OLLAMA_URL", "http://ollama:11434") + "/api/chat",
                json={
                    "model": "qwen3:0.6b",
                    "messages": messages,
                    "stream": False,
                    "think": False,
                    "format": schema,
                    "options": {"temperature": 0, "num_predict": 512},
                },
                timeout=120,
            )
            response.raise_for_status()
            return parse_object(response.json()["message"]["content"])
        if provider != "gigachat":
            raise ValueError("Unknown provider")
        key = os.getenv("GIGACHAT_AUTH_KEY")
        if not key:
            raise ValueError("GIGACHAT_AUTH_KEY is not configured")
        ca = os.getenv("GIGACHAT_CA_FILE")
        verify = ssl.create_default_context(cafile=ca) if ca else True
        with httpx.Client(verify=verify, timeout=60) as client:
            if not self.token or time.time() > self.expires - 30:
                response = client.post(
                    "https://ngw.devices.sberbank.ru:9443/api/v2/oauth",
                    headers={"Authorization": "Basic " + key, "RqUID": str(uuid4())},
                    data={"scope": os.getenv("GIGACHAT_SCOPE", "GIGACHAT_API_PERS")},
                )
                response.raise_for_status()
                payload = response.json()
                self.token = payload["access_token"]
                self.expires = payload["expires_at"] / 1000
            response = client.post(
                "https://gigachat.devices.sberbank.ru/api/v1/chat/completions",
                headers={"Authorization": "Bearer " + self.token},
                json={
                    "model": "GigaChat",
                    "messages": messages,
                    "temperature": 0.01,
                    "max_tokens": 512,
                },
            )
            response.raise_for_status()
            return parse_object(response.json()["choices"][0]["message"]["content"])


gateway = ModelGateway()


def facts_from_results(results):
    facts = []

    def fact(text):
        facts.append({"id": f"f{len(facts) + 1}", "text": text})

    for name, data in results.items():
        if name == "onsite_now":
            fact(
                f"Сейчас на объекте: {len(data)} сотрудников; у {sum(bool(r['location_unknown']) for r in data)} нет свежей геопозиции."
            )
        elif name == "shift_statistics":
            if not data:
                fact("За указанный период смен не найдено.")
            for row in data[:20]:
                fact(
                    f"{row['full_name']}: смен — {row['shifts']}, отработано — {row['hours']:.2f} ч."
                )
        elif name == "zone_statistics":
            if not data["zones"]:
                fact("Зоны площадки ещё не размечены.")
            for row in data["zones"][:20]:
                fact(
                    f"Зона «{row['name']}»: оценочно {row['person_minutes']:.1f} человеко-минут, точек — {row['samples']}."
                )
            fact(
                f"Геоточек за период: {data['coverage']['samples']}; подозрительных: {data['coverage']['suspicious_samples']}."
            )
            fact(
                f"Без достоверных координат: {data['coverage']['unknown_person_minutes']:.1f} человеко-минут смен."
            )
        elif name == "passage_denials":
            if not data:
                fact("Отказов за указанный период не зарегистрировано.")
            for row in data:
                fact(f"Причина «{row['reason']}»: отказов — {row['count']}.")
        elif name == "period_summary":
            fact(
                f"За период: сотрудников — {data['employees']}, смен — {data['shifts']}, отработано — {data['hours']:.2f} ч."
            )
    return facts


def assistant(actor, site_id, question, start, end, employee_ids, provider):
    selection = gateway.complete(
        provider,
        (PROMPTS / "select.txt").read_text(encoding="utf-8"),
        {"question": question},
        TOOLS["selection_schema"],
    )
    names = selection.get("tools")
    if (
        set(selection) != {"tools"}
        or not isinstance(names, list)
        or not 1 <= len(names) <= 5
        or any(n not in TOOLS["tools"] for n in names)
    ):
        raise ValueError("Model selected an unsupported tool")
    results = {}
    with transaction(actor, analytics=True) as conn:
        for name in dict.fromkeys(names):
            results[name] = tool_query(conn, name, site_id, start, end, employee_ids)
    facts = facts_from_results(results)
    answer = gateway.complete(
        provider,
        (PROMPTS / "summarize.txt").read_text(encoding="utf-8"),
        {"question": question, "facts": facts},
        TOOLS["answer_schema"],
    )
    if (
        set(answer) != {"blocks"}
        or not isinstance(answer["blocks"], list)
        or not 1 <= len(answer["blocks"]) <= 6
    ):
        raise ValueError("Invalid summary format")
    lookup = {f["id"]: f["text"] for f in facts}
    ids = []
    for block in answer["blocks"]:
        if (
            not isinstance(block, dict)
            or set(block) != {"fact_id"}
            or block["fact_id"] not in lookup
        ):
            raise ValueError("Unsupported model claim")
        if block["fact_id"] not in ids:
            ids.append(block["fact_id"])
    return {
        "text": "\n".join(lookup[i] for i in ids),
        "fact_ids": ids,
        "facts": facts,
        "tools": list(results),
        "provider": provider,
        "start": start,
        "end": end,
        "location_note": "Геопозиция передаётся раз в 10 минут, пропуски не восстанавливаются.",
    }
