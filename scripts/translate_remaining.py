#!/usr/bin/env python3
"""Translate remaining Yomitan glossary text with DeepL, preserving structure."""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import sqlite3
import tempfile
import urllib.error
import urllib.request
import zipfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "out"
PUBLIC = ROOT / "public_out"
DB = ROOT / "work" / "translations.sqlite"
STATE = ROOT / "work" / "translation_last_run.json"
PENDING = "pt-pendente"
AUTO = "pt-automatica"
PARTIAL = "pt-parcial"
REUSED = "pt-reaproveitada"
JAPANESE = re.compile(r"[\u3040-\u30ff\u3400-\u9fff]")
ROMANIZED = re.compile(r"^[A-Z][A-Za-zÀ-ÿ'’.-]*(?:\s+[A-Z][A-Za-zÀ-ÿ'’.-]*)+$")
COMMON_LOANS = {"vagina", "sexy", "kosher", "déjà vu", "ISP"}
LABELS_PATH = ROOT / "data" / "overrides" / "glossario-etiquetas.csv"


def endpoint_and_key():
    key = os.environ.get("DEEPL_API_KEY")
    if not key:
        raise RuntimeError("DEEPL_API_KEY não encontrada; abra um terminal novo e tente novamente.")
    return ("https://api-free.deepl.com" if key.endswith(":fx") else "https://api.deepl.com"), key


def call_json(url, key, body=None):
    headers = {"Authorization": f"DeepL-Auth-Key {key}"}
    data = None
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code in (429, 456):
            raise RuntimeError(f"DeepL retornou {e.code}; progresso salvo no cache.") from None
        raise RuntimeError(f"DeepL retornou HTTP {e.code}.") from None


def db():
    con = sqlite3.connect(DB)
    con.execute("CREATE TABLE IF NOT EXISTS translations (source TEXT PRIMARY KEY, translation TEXT NOT NULL, billed_chars INTEGER NOT NULL DEFAULT 0, engine TEXT NOT NULL, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)")
    return con


def labels():
    with LABELS_PATH.open(encoding="utf-8", newline="") as f:
        return {r["en"]: r["pt"] for r in csv.DictReader(f) if r.get("en")}


def archives():
    for name in ("Jitendex-PT.zip", "JMnedict-PT.zip", "KANJIDIC-PT-completo.zip"):
        p = OUT / name
        if p.exists():
            yield p


def grammar_context(row):
    tags = row[2] if len(row) > 2 and isinstance(row[2], str) else ""
    return tags.replace("pt-pendente", "") or "not specified"


def glossary_strings(value, in_glossary=False, plain=False):
    """Yield visible definition strings, never attributes/data/ruby/tag text."""
    if isinstance(value, str):
        if in_glossary or plain:
            yield value
    elif isinstance(value, list):
        for x in value:
            yield from glossary_strings(x, in_glossary, plain)
    elif isinstance(value, dict):
        data = value.get("data")
        here = in_glossary or (isinstance(data, dict) and data.get("content") == "glossary")
        if "content" in value:
            yield from glossary_strings(value["content"], here, plain)


def row_strings(row):
    defs = row[5] if len(row) > 5 else []
    if isinstance(defs, list) and all(isinstance(x, str) for x in defs):
        return list(glossary_strings(defs, plain=True))
    return list(glossary_strings(defs))


def proper_name(text, archive):
    if JAPANESE.search(text):
        return True
    if archive == "JMnedict-PT.zip" and ROMANIZED.fullmatch(text.strip()):
        return True
    return False


def collect():
    records, refs = {}, []
    for archive in archives():
        with zipfile.ZipFile(archive) as z:
            for name in z.namelist():
                if not name.endswith(".json") or "term_bank" not in name:
                    continue
                payload = json.loads(z.read(name))
                for ri, row in enumerate(payload):
                    if not isinstance(row, list) or len(row) < 6 or PENDING not in str(row[2]):
                        continue
                    expr = row[0] if isinstance(row[0], str) else ""
                    context = f"Japanese dictionary entry; expression: {expr}; grammatical class/tags: {grammar_context(row)}. Translate English into Brazilian Portuguese only. Preserve names and romanization."
                    for text in row_strings(row):
                        if not text.strip():
                            continue
                        refs.append((archive, name, ri, text))
                        records.setdefault(text, {"context": context, "archive": archive.name, "expr": expr})
    return records, refs


def quality(source, target, record):
    target = target.strip()
    if len(target) > 3 * len(source) + 10:
        return None, "expansao"
    low = target.casefold()
    if any(x in low for x in ("sinto muito", "não posso ajudar", "estou aqui")):
        return None, "chatbot"
    if target.casefold() == source.casefold() and source not in COMMON_LOANS and re.fullmatch(r"[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ .'-]*", source):
        return None, "igual"
    return target, "deepl"


def translate(records, max_chars, dry_run=False):
    base, key = endpoint_and_key()
    con = db()
    cur = con.cursor()
    existing = {s for (s,) in cur.execute("SELECT source FROM translations")}
    exact_labels = labels()
    candidates = []
    skipped = Counter()
    for text, rec in records.items():
        if text in exact_labels or text in existing:
            continue
        if proper_name(text, rec["archive"]):
            skipped["protegida"] += 1
            continue
        candidates.append((text, rec))
    candidates.sort(key=lambda x: (-len(x[0]), x[0]))
    selected, chars = [], 0
    for item in candidates:
        if chars + len(item[0]) > max_chars:
            break
        selected.append(item); chars += len(item[0])
    if dry_run:
        return {"selected": len(selected), "chars_selected": chars, "skipped": dict(skipped), "api_chars": 0}
    billed = 0
    for i in range(0, len(selected), 50):
        batch = selected[i:i + 50]
        context = "\n".join(f"[{n+1}] {r['context']}" for n, (_, r) in enumerate(batch))
        body = {"text": [t for t, _ in batch], "source_lang": "EN", "target_lang": "PT-BR", "context": context, "preserve_formatting": True, "show_billed_characters": True}
        response = call_json(base + "/v2/translate", key, body)
        translations = response.get("translations", [])
        if len(translations) != len(batch):
            raise RuntimeError("DeepL retornou quantidade de traduções inesperada; cache preservado.")
        for (source, rec), result in zip(batch, translations):
            translated, outcome = quality(source, result.get("text", ""), rec)
            if translated:
                paid = int(result.get("billed_characters", len(source)))
                cur.execute("INSERT OR REPLACE INTO translations(source,translation,billed_chars,engine) VALUES(?,?,?,?)", (source, translated, paid, "deepl"))
                billed += paid
            else:
                skipped[outcome] += 1
        con.commit()
    return {"selected": len(selected), "chars_selected": chars, "skipped": dict(skipped), "api_chars": billed}


def apply_translations():
    con = db()
    cache = {s: t for s, t in con.execute("SELECT source,translation FROM translations")}
    exact_labels = labels()
    counts = Counter()
    for archive in archives():
        with zipfile.ZipFile(archive) as zin:
            entries = [(info, zin.read(info.filename)) for info in zin.infolist()]
        with tempfile.TemporaryDirectory(dir=ROOT) as td:
            tmp = Path(td) / archive.name
            with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
                for info, data in entries:
                    if info.filename.endswith(".json") and "term_bank" in info.filename:
                        payload = json.loads(data)
                        for row in payload:
                            if not isinstance(row, list) or len(row) < 6 or PENDING not in str(row[2]):
                                continue
                            changed = unresolved = False
                            plain = isinstance(row[5], list) and all(isinstance(x, str) for x in row[5])
                            def tx(value, ing=False):
                                nonlocal changed, unresolved
                                if isinstance(value, str):
                                    if not (ing or plain):
                                        return value
                                    if value in exact_labels:
                                        changed = True; return exact_labels[value]
                                    if value in cache:
                                        changed = True; return cache[value]
                                    unresolved = True; return value
                                if isinstance(value, list): return [tx(x, ing) for x in value]
                                if isinstance(value, dict):
                                    new = dict(value)
                                    data2 = value.get("data")
                                    here = ing or (isinstance(data2, dict) and data2.get("content") == "glossary")
                                    if "content" in value: new["content"] = tx(value["content"], here)
                                    return new
                                return value
                            row[5] = tx(row[5])
                            tags = [t for t in re.split(r"[ ,]", str(row[2])) if t and t not in {PENDING, AUTO, PARTIAL, REUSED}]
                            if changed and unresolved: tags.append(PARTIAL); counts[PARTIAL] += 1
                            elif changed: tags.append(AUTO); counts[AUTO] += 1
                            else: tags.append(PENDING); counts[PENDING] += 1
                            row[2] = " ".join(tags)
                        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                    zout.writestr(info, data)
            os.replace(tmp, archive)
        PUBLIC.mkdir(exist_ok=True)
        shutil.copy2(archive, PUBLIC / archive.name)
    return counts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-chars", type=int, default=450000)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--usage", action="store_true")
    ns = ap.parse_args()
    base, key = endpoint_and_key()
    if ns.usage:
        usage = call_json(base + "/v2/usage", key)
        print(json.dumps({k: usage.get(k) for k in ("character_count", "character_limit")}, ensure_ascii=False))
        return
    records, refs = collect()
    result = translate(records, ns.max_chars, ns.dry_run)
    result.update({"unique_pending": len(records), "references": len(refs)})
    if not ns.dry_run:
        result["applied"] = dict(apply_translations())
    STATE.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
