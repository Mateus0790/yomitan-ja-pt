#!/usr/bin/env python3
"""Traduz as glosas que ainda estão em inglês usando a API do Gemini.

Lê os pacotes finalizados em public_out/, junta as glosas das entradas
pt-pendente/pt-parcial que continuam em inglês e grava as traduções no mesmo
cache do DeepL (work/translations.sqlite, engine "gemini:<modelo>"). Nada é
alterado nos ZIPs: depois rode scripts/finalizar_publicacao.py, que aplica o
cache. Pode ser interrompido e retomado: o que já está no cache não é pago de novo.

Uso:
  set GEMINI_API_KEY=...            (PowerShell: $env:GEMINI_API_KEY="...")
  python scripts/translate_gemini.py --dry-run         # conta strings e estima custo
  python scripts/translate_gemini.py --max-requests 5  # teste pequeno
  python scripts/translate_gemini.py                   # tudo (retomável)
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.request
import zipfile
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from finalizar_publicacao import PT_CHARS, PT_WORDS, still_english, translatable  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "public_out"
DB = ROOT / "work" / "translations.sqlite"
LABELS = ROOT / "data" / "overrides" / "glossario-etiquetas.csv"
API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
# preço por 1M tokens (entrada, saída) em USD, tabela de 2026-10; só para a estimativa
PRICES = {
    "gemini-2.5-flash-lite": (0.10, 0.40),
    "gemini-3.1-flash-lite": (0.25, 1.50),
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini-3.5-flash-lite": (0.30, 2.50),
}
CHATBOT = ("sinto muito", "não posso", "como modelo", "aqui está", "claro!", "desculpe")

SYSTEM = """Você traduz glosas de um dicionário japonês→inglês (JMdict/Jitendex) para português do Brasil.
Cada item traz a palavra japonesa, a leitura e as classes gramaticais só como contexto; traduza apenas o campo "en".
Regras:
- Devolva somente a tradução da glosa, curta e no mesmo estilo de dicionário. Não explique nem comente.
- Verbos ingleses "to X" viram infinitivo ("to eat" → "comer"). Mantenha "; " entre sinônimos.
- Preserve parênteses, números, símbolos (①, ⟶, etc.), siglas, nomes próprios, romanizações e termos científicos em latim.
- Abreviações de etiqueta: "esp." → "esp.", "e.g." → "ex.", "i.e." → "i.e.", "usu." → "geralmente", "abbr." → "abrev.".
- Se a glosa for um nome próprio ou já não tiver o que traduzir, devolva-a igual.
Glossário obrigatório (inglês → português):
{labels}"""


def labels() -> str:
    if not LABELS.exists():
        return "(vazio)"
    with LABELS.open(encoding="utf-8", newline="") as f:
        return "\n".join(f"- {r['en']} → {r['pt']}" for r in csv.DictReader(f) if r.get("en") and r.get("pt"))


def db() -> sqlite3.Connection:
    con = sqlite3.connect(DB)
    con.execute("CREATE TABLE IF NOT EXISTS translations (source TEXT PRIMARY KEY, translation TEXT NOT NULL, billed_chars INTEGER NOT NULL DEFAULT 0, engine TEXT NOT NULL, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)")
    return con


def glosses(value, out: list, glossary=False):
    if isinstance(value, str):
        if glossary:
            out.append(value)
    elif isinstance(value, list):
        for v in value:
            glosses(v, out, glossary)
    elif isinstance(value, dict):
        data = value.get("data")
        here = glossary or (isinstance(data, dict) and data.get("content") == "glossary")
        if "content" in value:
            glosses(value["content"], out, here)


def collect(cached: set[str]) -> list[dict]:
    """Strings únicas ainda em inglês, as mais usadas primeiro (Jitendex antes do JMnedict)."""
    found: dict[str, dict] = {}
    refs = Counter()
    for prio, name in enumerate(("Jitendex-PT.zip", "JMnedict-PT.zip")):
        path = PUBLIC / name
        if not path.exists():
            raise SystemExit(f"{path} não existe; rode scripts/finalizar_publicacao.py antes")
        with zipfile.ZipFile(path) as z:
            for fn in z.namelist():
                if not (fn.startswith("term_bank") and fn.endswith(".json")):
                    continue
                for row in json.loads(z.read(fn)):
                    tags = re.split(r"[ ,]", row[2] or "")
                    if "pt-pendente" not in tags and "pt-parcial" not in tags:
                        continue
                    items: list = []
                    glosses(row[5], items)
                    if not items:  # JMnedict: glosas são strings soltas
                        items = [x for x in row[5] if isinstance(x, str)]
                    for s in items:
                        if s in cached or not translatable(s) or PT_CHARS.search(s) or PT_WORDS.search(s):
                            continue
                        refs[s] += 1
                        found.setdefault(s, {"en": s, "ja": row[0], "kana": row[1], "pos": row[3] or "", "prio": prio})
    items = list(found.values())
    items.sort(key=lambda r: (r["prio"], -refs[r["en"]], len(r["en"])))
    return items


def call(model: str, key: str, batch: list[dict], system: str) -> list[dict]:
    payload = [{"id": i, "ja": r["ja"], "kana": r["kana"], "pos": r["pos"], "en": r["en"]} for i, r in enumerate(batch)]
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": json.dumps(payload, ensure_ascii=False)}]}],
        "generationConfig": {
            "temperature": 0.2,
            "responseMimeType": "application/json",
            "responseSchema": {
                "type": "ARRAY",
                "items": {"type": "OBJECT", "properties": {"id": {"type": "INTEGER"}, "pt": {"type": "STRING"}}, "required": ["id", "pt"]},
            },
        },
    }
    req = urllib.request.Request(
        API.format(model=model),
        json.dumps(body, ensure_ascii=False).encode("utf-8"),
        {"Content-Type": "application/json", "x-goog-api-key": key},
    )
    with urllib.request.urlopen(req, timeout=300) as r:
        res = json.loads(r.read().decode("utf-8"))
    usage = res.get("usageMetadata", {})
    text = "".join(p.get("text", "") for p in res["candidates"][0]["content"]["parts"])
    return json.loads(text), usage


def accept(src: str, pt: str) -> str | None:
    pt = (pt or "").strip()
    if not pt or len(pt) > 3 * len(src) + 20:
        return None
    if any(x in pt.casefold() for x in CHATBOT):
        return None
    if pt == src or still_english(pt):
        return None
    return pt


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="gemini-2.5-flash-lite")
    ap.add_argument("--batch", type=int, default=60, help="glosas por requisição")
    ap.add_argument("--rpm", type=float, default=10, help="requisições por minuto (free tier: veja o AI Studio)")
    ap.add_argument("--max-requests", type=int, default=0, help="0 = sem limite")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    con = db()
    cached = {s for (s,) in con.execute("SELECT source FROM translations")}
    items = collect(cached)
    chars = sum(len(r["en"]) for r in items)
    requests = -(-len(items) // args.batch)
    # ~4 caracteres por token; cada item leva ~25 tokens de contexto/JSON, mais o prompt fixo
    tin = chars / 4 + len(items) * 25 + requests * 600
    tout = chars / 3.5 + len(items) * 10
    pin, pout = PRICES.get(args.model, (0, 0))
    br = lambda n: f"{n:,}".replace(",", ".")
    print(f"{br(len(items))} glosas únicas, {br(chars)} caracteres, {br(requests)} requisições")
    if pin:
        print(f"estimativa {args.model}: ~{tin/1e6:.1f}M tokens entrada + ~{tout/1e6:.1f}M saída ≈ US$ {tin/1e6*pin + tout/1e6*pout:.2f} (pago; free tier = US$ 0, mas mais lento)")
    if args.dry_run:
        return 0
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        print("GEMINI_API_KEY não está definida neste terminal.", file=sys.stderr)
        return 2

    system = SYSTEM.format(labels=labels())
    engine = f"gemini:{args.model}"
    gap = 60 / args.rpm
    stats = Counter()
    for n in range(requests):
        if args.max_requests and n >= args.max_requests:
            break
        batch = items[n * args.batch:(n + 1) * args.batch]
        for attempt in range(6):
            started = time.monotonic()
            try:
                result, usage = call(args.model, key, batch, system)
                break
            except urllib.error.HTTPError as e:
                detail = e.read().decode("utf-8", "replace")[:300]
                if e.code == 429 and "PerDay" in detail:
                    print("cota diária do Gemini esgotada; progresso salvo. Rode de novo amanhã.", file=sys.stderr)
                    return 0
                if e.code in (429, 500, 503) and attempt < 5:
                    time.sleep(min(300, 15 * 2 ** attempt))
                    continue
                print(f"Gemini HTTP {e.code}: {detail}", file=sys.stderr)
                return 1
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, KeyError, IndexError) as e:
                if attempt < 5:
                    time.sleep(15 * 2 ** attempt)
                    continue
                print(f"falha na requisição {n}: {e}", file=sys.stderr)
                return 1
        by_id = {r.get("id"): r.get("pt") for r in result if isinstance(r, dict)}
        rows = []
        for i, item in enumerate(batch):
            pt = accept(item["en"], by_id.get(i))
            if pt:
                rows.append((item["en"], pt, len(item["en"]), engine))
                stats["aceitas"] += 1
            else:
                stats["rejeitadas"] += 1
        con.executemany("INSERT OR REPLACE INTO translations(source,translation,billed_chars,engine) VALUES(?,?,?,?)", rows)
        con.commit()
        stats["tokens_in"] += usage.get("promptTokenCount", 0)
        stats["tokens_out"] += usage.get("candidatesTokenCount", 0) + usage.get("thoughtsTokenCount", 0)
        print(f"[{n + 1}/{requests}] aceitas={stats['aceitas']} rejeitadas={stats['rejeitadas']} tokens={stats['tokens_in']}/{stats['tokens_out']}", flush=True)
        time.sleep(max(0, gap - (time.monotonic() - started)))
    print("pronto. Agora rode: python scripts/finalizar_publicacao.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
