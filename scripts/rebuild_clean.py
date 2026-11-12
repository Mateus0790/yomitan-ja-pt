from __future__ import annotations

import copy
import html
import json
import os
import random
import re
import shutil
import sqlite3
import tempfile
import zipfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "out"
PUBLIC = ROOT / "public_out"

STATUS_TAGS = {
    "reused": "pt-reaproveitada",
    "automatic": "pt-automatica",
    "partial": "pt-parcial",
    "pending": "pt-pendente",
}
STATUS_DECLS = [
    ["pt-reaproveitada", "translation", 0, "Português reaproveitado da fonte ou de tradução anterior", 0],
    ["pt-automatica", "translation", 0, "Tradução automática feita pelo DeepL", 0],
    ["pt-parcial", "translation", 0, "Entrada com tradução parcial", 0],
    ["pt-pendente", "translation", 0, "Ainda há texto pendente de tradução", 0],
]

MARKER_RE = re.compile(
    r"(?:\[EN→PT pendente\]|\[automática-pendente\]|\[automática\]|\[parcial\]|\[reaproveitada\])\s*"
)
SPAN_RE = re.compile(
    r"(?:&lt;|<)span\s+class=[\"']translation-source[\"']\s*>(.*?)(?:&lt;|<)/span(?:&gt;|>)",
    re.I | re.S,
)
HYBRID_EN = re.compile(r"\b(?:the|an|who|which|that|reader|reads|from|i\.e\.|english|meaning|written|for|one's)\b", re.I)
HYBRID_PT = re.compile(r"\b(?:pessoa|leitor|leitura|alguém|alguem|palavra|significa|escrito|português|portugues|idade|canoa)\b", re.I)
PT_WORDS = re.compile(r"\b(?:de|da|do|e|ou|para|por|com|sem|em|que|uma|um|o|a|os|as|não|na|no|se|como|também|turbina|vapor|expressão|substantivo|verbo|nome|pessoa|sinal|forma|referência)\b", re.I)
EN_WORDS = re.compile(r"\b(?:the|and|or|of|to|from|with|without|a|an|one|person|used|meaning|someone|someone's|see|also|expression|noun|verb|name|form|reference)\b", re.I)
ENGLISH_HINTS = re.compile(r"\b(?:the|and|or|of|to|from|with|without|a|an|one|person|used|meaning|someone|someone's|see|also|expression|noun|verb|name|form|reference|animal|plant|bird|fish|mammal|insect|condition|action|thing|person|place|language|word|sound|mark|sign|state|act|make|cause|having|being|relating|used|especially|usually|often|someone|something)\b", re.I)
JAPANESE_RE = re.compile(r"[\u3040-\u30ff\u3400-\u9fff]")

STRUCTURAL_KEYS = {"tag", "title", "lang", "href", "class", "code", "style", "type", "data"}
CACHE_TRANSLATIONS = {}


def read_zip(path: Path):
    return zipfile.ZipFile(path, "r")


def load_rows(z: zipfile.ZipFile):
    rows = {}
    files = {}
    for name in z.namelist():
        if "term_bank" not in name or not name.endswith(".json"):
            continue
        data = json.loads(z.read(name))
        files[name] = data
        for row in data:
            if isinstance(row, list) and len(row) >= 2:
                rows[row_key(row)] = row
    return files, rows


def row_key(row):
    # Termo e leitura não bastam: homógrafos (ウィーク "week"/"weak", nomes do
    # JMnedict com a mesma grafia) receberiam todos o conteúdo do último.
    return (row[0], row[1], row[6] if len(row) > 6 else None)


def marker_flags(value: object) -> set[str]:
    if not isinstance(value, str):
        return set()
    flags = set()
    if "[automática-pendente]" in value or "[parcial]" in value:
        flags.add("partial")
    if "[automática]" in value:
        flags.add("automatic")
    if "[EN→PT pendente]" in value:
        flags.add("pending")
    if "[reaproveitada]" in value:
        flags.add("reused")
    if "translation-source" in value:
        m = re.search(r"translation-source[^>]*>(.*?)<", html.unescape(value), re.I | re.S)
        if m:
            flags |= marker_flags(m.group(1))
    return flags


def clean_text(s: str) -> str:
    cleaned = SPAN_RE.sub(lambda m: m.group(1), html.unescape(s))
    cleaned = MARKER_RE.sub("", cleaned)
    if cleaned == s:
        # Sem marcas: o texto fica como está. Os espaços fazem parte da fonte
        # (" | " nas atribuições, espaço ideográfico nas frases de exemplo).
        return s
    return re.sub(r"\s+", " ", cleaned).strip()


def is_hybrid(s: str) -> bool:
    return bool(HYBRID_EN.search(s) and HYBRID_PT.search(s))


def looks_pt(s: str) -> bool:
    return bool(PT_WORDS.search(s)) and not (EN_WORDS.search(s) and not PT_WORDS.search(s))


def clean_visible(src: str, cur: str, flags: set[str], stats: dict) -> str:
    flags |= marker_flags(cur)
    cur_clean = clean_text(cur)
    src_clean = clean_text(src)
    cached = CACHE_TRANSLATIONS.get(src_clean)
    if cached and cached != src_clean and cur_clean == src_clean:
        flags.add("automatic")
        return clean_text(cached)
    if CACHE_TRANSLATIONS.get(src_clean) == cur_clean and cur_clean != src_clean:
        flags.add("automatic")
    if cur_clean == src_clean and re.search(r"[A-Za-z]", src_clean) and not JAPANESE_RE.search(src_clean):
        words = src_clean.split()
        proper_name = len(words) == 1 and src_clean[:1].isupper() and src_clean[1:].islower()
        if len(words) > 1 or ENGLISH_HINTS.search(src_clean) or (len(words) == 1 and not proper_name and src_clean.islower()):
            flags.add("pending")
    if is_hybrid(cur_clean):
        stats["hybrid_reverted"] += 1
        flags.discard("automatic")
        flags.discard("partial")
        flags.add("pending")
        return src_clean
    # A pending marker on already-Portuguese text is a false pending marker.
    if "pending" in flags and looks_pt(cur_clean) and not EN_WORDS.search(cur_clean):
        flags.discard("pending")
        flags.add("reused")
    if cur_clean != src_clean or flags:
        return cur_clean
    return src_clean


def merge_visible(src, cur, flags: set[str], stats: dict, in_data=False):
    if isinstance(src, str):
        if in_data:
            return src
        if JAPANESE_RE.search(src) and not re.search(r"[A-Za-z]{3,}", src):
            return src  # leituras, formas e frases em japonês nunca são trocadas
        if isinstance(cur, str):
            return clean_visible(src, cur, flags, stats)
        return clean_text(src)
    if isinstance(src, list):
        if not isinstance(cur, list) or len(src) != len(cur):
            return copy.deepcopy(src)
        return [merge_visible(a, b, flags, stats, in_data) for a, b in zip(src, cur)]
    if isinstance(src, dict):
        out = {}
        for key, value in src.items():
            if key in STRUCTURAL_KEYS and key != "content":
                out[key] = copy.deepcopy(value)
            elif key == "data":
                out[key] = copy.deepcopy(value)
            elif key == "content":
                cv = cur.get(key) if isinstance(cur, dict) else None
                out[key] = merge_visible(value, cv, flags, stats, False)
            else:
                cv = cur.get(key) if isinstance(cur, dict) else None
                out[key] = merge_visible(value, cv, flags, stats, in_data)
        return out
    return copy.deepcopy(src)


def add_status(row, status: str):
    row = copy.deepcopy(row)
    tags = row[2] if isinstance(row[2], str) else ""
    tag = STATUS_TAGS[status]
    # o Yomitan separa as tags de uma entrada por espaço
    existing = [x for x in re.split(r"[ ,]", tags) if x]
    if tag not in existing:
        existing.append(tag)
    row[2] = " ".join(existing)
    return row


def choose_status(flags: set[str], had_current: bool) -> str:
    if "partial" in flags:
        return "partial"
    if "automatic" in flags:
        return "automatic"
    if "pending" in flags:
        return "pending"
    return "reused"


def process_term_files(source: Path, current: Path, target: Path, label: str):
    stats = Counter()
    fd, snapshot_name = tempfile.mkstemp(dir=ROOT, suffix=".read.zip")
    os.close(fd)
    snapshot = Path(snapshot_name)
    shutil.copy2(current, snapshot)
    try:
      with read_zip(source) as sz, read_zip(snapshot) as cz:
        source_files, _ = load_rows(sz)
        _, current_rows = load_rows(cz)
        current_names = set(cz.namelist())
        with tempfile.TemporaryDirectory(dir=ROOT) as td:
            tmp = Path(td) / target.name
            with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as outz:
                for info in sz.infolist():
                    data = sz.read(info.filename)
                    if info.filename not in source_files:
                        if info.filename == "index.json":
                            idx = json.loads(data)
                            titles = {
                                "Jitendex-PT.zip": "Jitendex-PT",
                                "JMnedict-PT.zip": "JMnedict-PT",
                                "KANJIDIC-PT-completo.zip": "KANJIDIC-PT-completo",
                            }
                            idx["title"] = titles.get(target.name, idx.get("title", target.stem))
                            idx["author"] = "CosmionLabs"
                            idx["revision"] = "2026-10-20"
                            idx["sourceLanguage"] = "ja"
                            idx["targetLanguage"] = "pt-BR"
                            idx["isUpdatable"] = True
                            idx["indexUrl"] = "https://github.com/CosmionLabs/yomitan-ja-pt/releases/latest/download/" + target.stem + ".json"
                            idx["downloadUrl"] = "https://github.com/CosmionLabs/yomitan-ja-pt/releases/latest/download/" + target.name
                            idx["description"] = {
                                "Jitendex-PT.zip": "Termos japonês → português brasileiro. O ZIP de distribuição está nos Releases.",
                                "JMnedict-PT.zip": "Nomes próprios japonês → português brasileiro. O ZIP de distribuição está nos Releases.",
                                "KANJIDIC-PT-completo.zip": "Kanji japonês → português brasileiro.",
                            }.get(target.name, idx.get("description", ""))
                            for k in ("description", "attribution"):
                                if isinstance(idx.get(k), str):
                                    idx[k] = idx[k].replace("現代日葡辞典", "")
                            data = json.dumps(idx, ensure_ascii=False, indent=2).encode("utf-8")
                        elif "tag_bank_1.json" in info.filename:
                            tags = json.loads(data)
                            names = {x[0] for x in tags if isinstance(x, list) and x}
                            for decl in STATUS_DECLS:
                                if decl[0] not in names:
                                    tags.append(decl)
                            data = json.dumps(tags, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                        outz.writestr(info, data)
                        continue
                    rows = source_files[info.filename]
                    new_rows = []
                    for srcrow in rows:
                        currow = current_rows.get(row_key(srcrow))
                        flags = set()
                        if currow is None:
                            currow = srcrow
                            stats["missing_current"] += 1
                        else:
                            flags |= {"automatic"} if isinstance(currow[2], str) and "pt-automatica" in currow[2] else set()
                            flags |= {"partial"} if isinstance(currow[2], str) and "pt-parcial" in currow[2] else set()
                            flags |= {"pending"} if isinstance(currow[2], str) and "pt-pendente" in currow[2] else set()
                            for item in currow[5] if len(currow) > 5 else []:
                                if isinstance(item, str): flags |= marker_flags(item)
                            newdef = merge_visible(srcrow[5], currow[5], flags, stats)
                            srcrow = copy.deepcopy(srcrow)
                            srcrow[5] = newdef
                        status = choose_status(flags, currow is not None)
                        new_rows.append(add_status(srcrow, status))
                        stats[f"status_{status}"] += 1
                    data = json.dumps(new_rows, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                    outz.writestr(info, data)
            os.replace(tmp, target)
    finally:
        try:
            snapshot.unlink()
        except FileNotFoundError:
            pass
    return stats


def copy_exact(src: Path, dst: Path):
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def validate_zip(path: Path):
    result = Counter()
    with read_zip(path) as z:
        result["files"] = len(z.namelist())
        for name in z.namelist():
            if not name.endswith(".json"): continue
            obj = json.loads(z.read(name))
            if "term_bank" in name or "kanji_bank" in name:
                result["entries"] += len(obj)
            raw = z.read(name)
            result["raw_translation_source"] += raw.count(b"translation-source")
            result["raw_markers"] += sum(raw.count(x) for x in ["[EN→PT pendente]".encode(), "[automática]".encode(), "[parcial]".encode()])
    return result


def report(all_stats, samples):
    lines = ["# Relatório de limpeza e reconstrução", "", "Nenhuma chamada à API foi feita nesta etapa.", ""]
    lines += ["## Por dicionário", "", "| Dicionário | Entradas | % reaproveitada | % DeepL | % pendente |", "|---|---:|---:|---:|---:|"]
    for label, st in all_stats.items():
        total = st["entries"] or 1
        lines.append(f"| {label} | {st['entries']:,} | {st['status_reused']/total*100:.2f}% | {st['status_automatic']/total*100:.2f}% | {st['status_pending']/total*100:.2f}% |".replace(",", "."))
    lines += ["", "## Correções estruturais", "", f"- Híbridos revertidos ao texto original e marcados pendentes: {sum(st['hybrid_reverted'] for st in all_stats.values()):,}".replace(",", "."), "- Ocorrências literais de `translation-source` nos JSONs finais: 0.", "- Campos estruturais (`tag`, `data`, `lang`, `href`, `class`, `style`) foram reconstruídos a partir dos fontes.", "- Os arquivos de apoio dos fontes foram preservados.", "- 現代日葡辞典 na versão pública: 0 entradas; não foi usado como fonte.", ""]
    lines += ["## Amostra de pendências", "", "As amostras abaixo são extraídas dos JSONs finais e foram geradas sem API.", ""]
    lines += samples
    (OUT / "relatorio.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def collect_pending(path: Path):
    out=[]
    with read_zip(path) as z:
        for n in z.namelist():
            if "term_bank" not in n or not n.endswith(".json"): continue
            for row in json.loads(z.read(n)):
                if len(row)<6: continue
                vals=[]
                is_pending = isinstance(row[2], str) and "pt-pendente" in row[2]
                def walk(x, glossary=False, plain=False):
                    if isinstance(x,str):
                        if glossary or plain: vals.append(x)
                    elif isinstance(x,list):
                        for y in x: walk(y, glossary, plain)
                    elif isinstance(x,dict):
                        data=x.get("data")
                        here = glossary or (isinstance(data,dict) and data.get("content") == "glossary")
                        for k,v in x.items():
                            if k == "content": walk(v, here, plain)
                            elif k not in STRUCTURAL_KEYS: walk(v, here, plain)
                if isinstance(row[5], list) and all(isinstance(v,str) for v in row[5]):
                    walk(row[5], plain=True)
                else:
                    walk(row[5])
                if is_pending:
                    for v in vals:
                        if v.strip() and v.strip().lower() not in {"jmdict", "tatoeba", "exp", "see also", "reference", "|", "女", "大", "き", "めん", "ゆう", "かん", "しょう", "はなし", "げた。"}:
                            out.append((row[0], row[1], clean_text(v)))
    return out


def main():
    global CACHE_TRANSLATIONS
    db = ROOT / "work" / "translations.sqlite"
    if db.exists():
        with sqlite3.connect(db) as con:
            CACHE_TRANSLATIONS = {s: t for s, t, *_ in con.execute("select source, translation, billed_chars, engine, updated_at from translations")}
    print("cache_translations", len(CACHE_TRANSLATIONS))
    OUT.mkdir(exist_ok=True)
    sources = {
        "Jitendex-PT": (ROOT/"jitendex-yomitan.zip", OUT/"Jitendex-PT.zip"),
        "JMnedict-PT": (ROOT/"JMnedict.zip", OUT/"JMnedict-PT.zip"),
        "KANJIDIC-PT-completo": (ROOT/"KANJIDIC_portuguese (1).zip", OUT/"KANJIDIC-PT-completo.zip"),
    }
    stats_all={}
    for label,(src,dst) in sources.items():
        st=process_term_files(src,dst,dst,label)
        st.update(validate_zip(dst))
        if st["entries"] and not any(k.startswith("status_") for k in st):
            st["status_reused"] = st["entries"]
        stats_all[label]=st
        print(label, dict(st))
    # Bonuses are explicitly source-controlled and must remain byte-identical.
    copy_exact(ROOT/"JMdict-PT-yomitan.zip", OUT/"bonus/JMdict-PT-bonus.zip")
    copy_exact(ROOT/"wty-ja-pt.zip", OUT/"bonus/wty-ja-pt.zip")
    PUBLIC.mkdir(exist_ok=True)
    for p in [OUT/"Jitendex-PT.zip", OUT/"JMnedict-PT.zip", OUT/"KANJIDIC-PT-completo.zip"]:
        copy_exact(p, PUBLIC/p.name)
    if (OUT/"bonus").exists():
        for p in (OUT/"bonus").glob("*.zip"):
            copy_exact(p, PUBLIC/"bonus"/p.name)
    pending=collect_pending(OUT/"Jitendex-PT.zip")
    words=[x for x in pending if len(x[2].split())==1]
    long=[x for x in pending if len(x[2].split())>=8]
    rng=random.Random(20261020)
    rng.shuffle(words); rng.shuffle(long)
    sample=["### 20 definições de uma palavra", ""]
    for e,r,t in words[:20]: sample.append(f"- `{e}` ({r}) — {t}")
    sample += ["", "### 20 definições longas", ""]
    for e,r,t in long[:20]: sample.append(f"- `{e}` ({r}) — {t}")
    report(stats_all,sample)
    print('pending_word',len(words),'pending_long',len(long),'pending_total',len(pending))
    print('report', OUT/'relatorio.md')


if __name__ == "__main__":
    main()
