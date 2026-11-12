"""Pós-processamento final antes de publicar.

Reconstrói cada pacote a partir da fonte original e só aproveita do pacote
gerado (``out/``) as traduções que passam por validação:

* a entrada publicada sai sempre com a forma, os textos em japonês, os
  espaços e a ordem da fonte. Só mudam os textos traduzíveis (glosas, notas,
  exemplos), as etiquetas e os tooltips;
* as traduções são reunidas em uma memória ``texto da fonte → português``,
  aprendida das entradas confiáveis: as que têm (termo, leitura) único e
  cujo conteúdo não traduzível é idêntico ao da fonte. Entradas homógrafas
  não ensinam nada, porque versões antigas do pipeline trocavam o conteúdo
  entre elas;
* uma tradução é descartada quando continua em inglês, quando é igual a
  outro texto da fonte ou quando é uma troca palavra a palavra do léxico
  antigo ("maiden nome", "ruim Godesberg");
* etiquetas, tooltips, idiomas de origem, tags e correções manuais vêm das
  tabelas de ``data/overrides/``, que têm prioridade sobre a tradução
  automática;
* as tags de status (``pt-reaproveitada``, ``pt-automatica``, ``pt-parcial``,
  ``pt-pendente``) são recalculadas e separadas por espaço, como o Yomitan
  exige;
* grava o resultado em ``public_out/`` e o índice solto ao lado do ZIP
  (``<Nome>.json``), que é o arquivo apontado por ``indexUrl``.

Uso: python scripts/finalizar_publicacao.py [--revision AAAA-MM-DD]
         [--sources DIR] [--out DIR] [--public DIR]

``--out`` também aceita ``dictionaries/``: nesse caso o script corrige os
pacotes já publicados (os ZIPs são procurados nas subpastas).
"""
from __future__ import annotations

import argparse
import csv
import datetime
import json
import re
import shutil
import sqlite3
import unicodedata
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OVERRIDES = ROOT / "data" / "overrides"
REPO = "CosmionLabs/yomitan-ja-pt"
RELEASE_URL = f"https://github.com/{REPO}/releases/latest/download/"

STATUS = ["pt-reaproveitada", "pt-automatica", "pt-parcial", "pt-pendente"]
STATUS_DECLS = [
    ["pt-reaproveitada", "translation", 0, "Português reaproveitado de fonte aberta (JMdict-PT)", 0],
    ["pt-automatica", "translation", 0, "Tradução automática (DeepL/Gemini), ainda não revisada", 0],
    ["pt-parcial", "translation", 0, "Parte das glosas ainda está em inglês", 0],
    ["pt-pendente", "translation", 0, "Glosas ainda em inglês, aguardando tradução", 0],
]

# (arquivos de fonte aceitos, descrição, tipo)
PACKAGES = {
    "Jitendex-PT": (["jitendex-yomitan.zip"], "Termos japonês → português brasileiro (base Jitendex/JMdict).", "jitendex"),
    "JMnedict-PT": (["JMnedict.zip"], "Nomes próprios japonês → português brasileiro (base JMnedict).", "nomes"),
    "KANJIDIC-PT-completo": (
        ["KANJIDIC_portuguese (1).zip", "KANJIDIC_portuguese.zip"],
        "Kanji japonês → português brasileiro (base KANJIDIC2).",
        "kanji",
    ),
}
PRERELEASE = " Pré-lançamento: parte das definições ainda está em inglês (tag pt-pendente/pt-parcial)."

# Contextos (data.content) do Jitendex cujo texto é traduzível.
TEXT_CTX = {
    "glossary", "xref-glossary", "antonym-glossary", "sense-note-content",
    "info-gloss-content", "lang-source-content", "example-sentence-b", "graphic-attribution",
}
# Contextos cujo texto vem de uma tabela fechada (etiquetas-jitendex.csv).
LABEL_CTX = {
    "part-of-speech-info", "misc-info", "field-info", "dialect-info", "lang-source-label",
    "lang-source-wasei", "info-gloss-label", "reference-label", "forms-label", "sense-note-label",
}

JAPANESE = re.compile(r"[぀-ヿ㐀-鿿]")
LATIN = re.compile(r"[A-Za-z]{3,}")
LOWER_WORD = re.compile(r"(?<![^\W\d_])[a-z]{3,}(?![^\W\d_])")
PT_CHARS = re.compile(r"[áéíóúâêôãõçàÁÉÍÓÚÂÊÔÃÕÇÀ]")
PT_WORDS = re.compile(
    r"\b(?:de|da|do|das|dos|uma|um|não|para|com|pessoa|alguém|algo|ser|estar|que|em|ou|no|na|nos|nas|"
    r"pelo|pela|sem|seu|sua|isso|este|esta|muito|como|mais|também|quando|onde|ele|ela|eu|você|é|"
    r"os|ao|aos|por|são|se)\b",
    re.I,
)
EN_WORDS = re.compile(
    r"\b(?:the|of|to|and|with|someone|something|one's|being|having|which|who|is|are|was|it|this|"
    r"that|you|your|from|by|at|on|an|as|not|be)\b",
    re.I,
)
WORD = re.compile(r"[^\W\d_]{2,}")
CIRCLED = re.compile(r"^([①-⑳])\s*")
CURLY = str.maketrans({"“": '"', "”": '"', "‘": "'", "’": "'"})

# Léxico que versões antigas de build_dictionary.py aplicavam palavra a palavra
# dentro de frases inglesas; serve para reconhecer e desfazer essas trocas.
OLD_LEX = {
    "animal": "animal", "person": "pessoa", "people": "pessoas", "place": "lugar", "name": "nome",
    "Japanese": "japonês", "English": "inglês", "to eat": "comer", "to go": "ir", "to see": "ver",
    "to make": "fazer", "to use": "usar", "to have": "ter", "to be": "ser/estar", "school": "escola",
    "student": "estudante", "work": "trabalho", "water": "água", "fire": "fogo", "one": "um",
    "two": "dois", "three": "três", "good": "bom", "bad": "ruim", "large": "grande",
    "small": "pequeno", "food": "comida",
}
OLD_LEX_RE = [(re.compile(r"\b" + re.escape(a) + r"\b", re.I), b) for a, b in sorted(OLD_LEX.items(), key=lambda z: -len(z[0]))]
OLD_LEX_KEYS = {k.lower() for k in OLD_LEX}
OLD_LEX_VALUES = {v for k, v in OLD_LEX.items() if v != k and "/" not in v}


# --------------------------------------------------------------------------
# tabelas de data/overrides
# --------------------------------------------------------------------------

def read_csv(name: str) -> list[dict]:
    path = OVERRIDES / name
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


class Tables:
    def __init__(self) -> None:
        self.labels = {(r["contexto"], r["en"]): r["pt"] for r in read_csv("etiquetas-jitendex.csv")}
        self.tooltips = {r["en"]: r["pt"] for r in read_csv("tooltips-jitendex.csv")}
        self.languages = {r["en"]: r["pt"] for r in read_csv("idiomas.csv")}
        self.tags = {(r["dicionario"], r["tag"]): (r["nome"], r["nota"]) for r in read_csv("tags.csv")}
        self.glosses: dict[str, str] = {}
        self.glosses_ci: dict[str, str] = {}
        for r in read_csv("correcoes-glosas.csv"):
            self.glosses[r["en"]] = r["pt"]
            self.glosses_ci.setdefault(r["en"].lower(), r["pt"])
        self.kanji = defaultdict(dict)
        for r in read_csv("correcoes-kanji.csv"):
            self.kanji[r["kanji"]][r["errado"]] = r["certo"]

    def gloss_override(self, body: str) -> str | None:
        """Correção manual para o texto (sem o número circulado). '' = manter em inglês."""
        if body in self.glosses:
            return self.glosses[body]
        return self.glosses_ci.get(body.lower())


OLD_FORM = re.compile(r"^(.) is the old character form of (.)$")
VERB_CLASS = re.compile(
    r"^(Godan|Yodan|Nidan) verb(?: \((lower|upper) class\))? with '([^']+)' ending"
    r"(?: and '([^']+)' conjugation)?(?: \((archaic|irregular verb|special class)\))?$"
)
VERB_EXTRA = {"archaic": "arcaico", "irregular verb": "verbo irregular", "special class": "classe especial"}


def tooltip(title: str, tables: Tables) -> str:
    if title in tables.tooltips:
        return tables.tooltips[title]
    m = OLD_FORM.match(title)
    if m:
        return f"{m.group(1)} é a forma antiga do caractere {m.group(2)}"
    m = VERB_CLASS.match(title)
    if m:
        kind, level, ending, conj, extra = m.groups()
        out = f"verbo {kind.lower()}"
        if level:
            out += " (classe inferior)" if level == "lower" else " (classe superior)"
        out += f" terminado em '{ending}'"
        if conj:
            out += f" e conjugação '{conj}'"
        if extra:
            out += f" ({VERB_EXTRA[extra]})"
        return out
    return title


LANG_ITEM = re.compile(r'^(?P<lang>[^:"]+?)(?P<word>: ".*")?$')


def language_of_origin(src: str, tables: Tables) -> str | None:
    """'French: "ragoût"' → 'francês: "ragoût"'; a palavra estrangeira nunca muda."""
    parts = []
    for part in src.split(" & "):
        m = LANG_ITEM.match(part)
        if not m or m.group("lang") not in tables.languages:
            return None
        parts.append(tables.languages[m.group("lang")] + (m.group("word") or ""))
    return " e ".join(parts)


# --------------------------------------------------------------------------
# validação e normalização de traduções
# --------------------------------------------------------------------------

def old_lex(s: str) -> str:
    for rx, b in OLD_LEX_RE:
        s = rx.sub(b, s)
    return s


def still_english(s: str) -> bool:
    return bool(EN_WORDS.search(s)) and not PT_WORDS.search(s) and not PT_CHARS.search(s)


def translatable(s: str) -> bool:
    # Nomes romanizados soltos ("Tanaka", "Yōichi", "NASDAQ") não precisam de tradução.
    if JAPANESE.search(s) or not LATIN.search(s):
        return False
    return bool(EN_WORDS.search(s) or LOWER_WORD.search(s))


class Validator:
    """Decide se ``cand`` é uma tradução aceitável de ``src``."""

    def __init__(self, source_strings: set[str], targets: set[str], names: bool) -> None:
        self.source_strings = source_strings
        self.names = names
        # frequência de cada palavra nos textos da fonte (inglês) e nas traduções
        self.en = Counter(w.lower() for s in source_strings if not JAPANESE.search(s) for w in WORD.findall(s))
        # só contam as traduções claramente portuguesas (acento ou palavra gramatical)
        self.pt = Counter(
            w.lower() for t in targets if PT_CHARS.search(t) or PT_WORDS.search(t) for w in WORD.findall(t)
        )

    def portuguese(self, word: str, own: int = 0) -> bool:
        """A palavra é bem mais comum nas traduções do que na fonte (que é ~10x maior).

        ``own`` desconta as ocorrências na própria candidata que está sendo julgada.
        """
        return (self.pt[word] - own) * 10 > self.en[word]

    def ok(self, src: str, cand: str) -> bool:
        if not cand or cand == src or cand.strip() == src.strip():
            return False
        body_s, body_c = CIRCLED.sub("", src).strip(), CIRCLED.sub("", cand).strip()
        if not body_c or body_c == body_s:
            return False
        # texto de outra entrada ou de uma versão antiga da fonte. Uma palavra só pode
        # ser cognata ("informal", "impasse"); essas passam por revisão manual
        if " " in body_c and (cand in self.source_strings or body_c in self.source_strings):
            return False
        # "being unruly" → "unruly", "halter (esp. ...)" → "halter": glosa antiga, mais curta
        if " " not in body_c and " " in body_s and re.search(r"(?<![^\W\d_])" + re.escape(body_c.lower()) + r"(?![^\W\d_])", body_s.lower()):
            return False
        if still_english(cand):
            return False
        # o japonês citado na glosa tem de ser o mesmo ("usu. ～にする" ≠ "usu. 缶詰にする")
        if sorted(JAPANESE.findall(src)) != sorted(JAPANESE.findall(cand)):
            return False
        if body_s.lower() not in OLD_LEX_KEYS and cand == old_lex(src):
            return False
        words = [w.lower() for w in WORD.findall(body_c)]
        if self.names:
            return self.ok_name(body_s, body_c, words)
        if " " in body_c and not PT_CHARS.search(body_c) and not PT_WORDS.search(body_c):
            count = Counter(words)
            if not any(self.portuguese(w, n) for w, n in count.items()):
                # "soft fair skin", "(UN) Security Council", "scoop (icecream, mashed potato,
                # etc.)": inglês de outra versão da fonte. Uma palavra só não é julgada aqui
                # (cognatos como "impasse" e "creche"); essas passam por revisão manual.
                # o nome científico entre parênteses é igual nos dois lados e não conta
                same = [p for p in re.findall(r"\([^()]*\)", body_s) if p in body_c]
                plain_s, plain_c = body_s, body_c
                for p in same:
                    plain_s, plain_c = plain_s.replace(p, " "), plain_c.replace(p, " ")
                original = [w.lower() for w in WORD.findall(plain_s)]
                mine = [w.lower() for w in WORD.findall(plain_c)]
                shared = [w for w in mine if w in original]
                english = all(w in self.en or w in original for w in mine) or len(shared) * 2 > len(mine)
                if mine and english and not reordered(original, mine):
                    return False
        return True

    def ok_name(self, src: str, cand: str, words: list[str]) -> bool:
        # "japonês Corporação Nacional...": o texto enviado ao tradutor já tinha a troca antiga
        if src[:1].isupper() and cand[:1].islower():
            return False
        # "Holst, Gustav (1874-1934; inglês composer)": troca antiga + resto em inglês
        kept = {w.lower() for w in LOWER_WORD.findall(src) if len(w) >= 4} & set(words)
        if kept and any(v in words for v in OLD_LEX_VALUES):
            return False
        # homônimos: os números (datas, ordinais) têm de ser os da fonte
        return numbers(SRC_DATE.sub(fmt_date, src)) == numbers(localize_dates(src, cand))


def reordered(src_words: list[str], words: list[str]) -> bool:
    """"vocal solo" → "solo vocal": a ordem portuguesa (núcleo antes do adjunto) indica tradução."""
    if not src_words or not words:
        return False
    first, last = src_words[0], src_words[-1]
    return (first in words and words[0] != first) or (last in words and words[-1] != last)


def numbers(text: str) -> list[int]:
    return sorted(int(n) for n in re.findall(r"\d+", text))


def dedupe_list(text: str) -> str:
    """"sem graça; sem graça; cafona" → "sem graça; cafona"."""
    if "; " not in text:
        return text
    seen, out = set(), []
    for item in text.split("; "):
        key = item.strip().lower()
        if key and key in seen:
            continue
        seen.add(key)
        out.append(item)
    return "; ".join(out)


def normalize(src: str, cand: str, ctx: str) -> str:
    """Ajustes mecânicos: numeração, espaços, aspas, repetições, maiúscula inicial."""
    lead = src[: len(src) - len(src.lstrip())]
    trail = src[len(src.rstrip()):]
    body = cand.strip()
    m = CIRCLED.match(src.strip())
    body = CIRCLED.sub("", body)
    if not re.search(r"[“”‘’]", src):
        body = body.translate(CURLY)
    if ctx != "example-sentence-b":
        body = dedupe_list(body)
        src_body = CIRCLED.sub("", src.strip())
        first = body.split(" ", 1)[0]
        if (
            body[:1].isupper() and not any(ch.isupper() for ch in src_body)
            and not (len(first) > 1 and first.isupper())
        ):
            body = body[0].lower() + body[1:]
    if ctx == "sense-note-content":
        body = re.sub(r"\b(usad|dit|gritad)a(s?)\b", r"\1o\2", body)
    if m:
        body = f"{m.group(1)} {body}"
    return lead + body + trail


# datas do JMnedict: 1964.5.5 → 05/05/1964, 1952.2 → 02/1952
SRC_DATE = re.compile(r"(?<![\d.])(\d{3,4})\.(\d{1,2})(?:\.(\d{1,2}))?(?![\d.])")
MONTHS = "janeiro|fevereiro|março|abril|maio|junho|julho|agosto|setembro|outubro|novembro|dezembro"
PT_DATE = re.compile(
    r"(?:\d{1,2}[º°]? de )?(?:" + MONTHS + r") de \d{3,4}|(?<![\d/])(?:\d{1,2}/)?\d{1,2}/\d{3,4}(?![\d/])|"
    r"(?<![\d.])\d{3,4}\.\d{1,2}(?:\.\d{1,2})?(?![\d.])",
    re.I,
)


def fmt_date(m: re.Match) -> str:
    day = f"{int(m.group(3)):02d}/" if m.group(3) else ""
    return f"{day}{int(m.group(2)):02d}/{m.group(1)}"


def fold(text: str) -> str:
    text = "".join(ch for ch in unicodedata.normalize("NFD", text.lower()) if not unicodedata.combining(ch))
    return re.sub(r"(?<=[ou])u|(?<=o)o", "", text)


def localize_dates(src: str, text: str) -> str:
    """Datas de ``text`` no formato dd/mm/aaaa, a partir das de ``src``; mantém a grafia do nome."""
    if not re.search(r"[“”‘’]", src):
        text = text.translate(CURLY)
    head_s, sep, _ = src.partition(" (")
    head_t, _, rest = text.partition(" (")
    if sep and head_t != head_s and fold(head_t) == fold(head_s):
        text = head_s + " (" + rest  # "Kōzou" não vira "Kōzō"
    dates = [fmt_date(m) for m in SRC_DATE.finditer(src)]
    if not dates:
        return text
    spans = list(PT_DATE.finditer(text))
    if len(spans) != len(dates):
        return text
    out, pos = [], 0
    for span, date in zip(spans, dates):
        out += [text[pos:span.start()], date]
        pos = span.end()
    out.append(text[pos:])
    text = "".join(out)
    # "05/10/1892 – 05/01/1985" → "05/10/1892-05/01/1985", como na fonte
    return re.sub(r"(?<=\d{4})\s*[–—-]\s*(?=\d{2}/|[);])", "-", text)


def only_dates_differ(src: str, cur: str) -> bool:
    return SRC_DATE.sub(fmt_date, src) == cur


# --------------------------------------------------------------------------
# leitura em paralelo de fonte e pacote gerado
# --------------------------------------------------------------------------

def child_ctx(node: dict, ctx: str) -> str:
    data = node.get("data")
    if node.get("tag") == "rt":
        return "rt"
    if isinstance(data, dict) and data.get("content"):
        return data["content"]
    return ctx


def pairs(src, cur, ctx: str, out: list) -> bool:
    """Coleta (contexto, fonte, atual). Devolve False se as formas divergem."""
    if isinstance(src, str):
        if not isinstance(cur, str):
            return False
        out.append((ctx, src, cur))
        return True
    if isinstance(src, list):
        if not isinstance(cur, list) or len(src) != len(cur):
            return False
        return all([pairs(a, b, ctx, out) for a, b in zip(src, cur)])
    if isinstance(src, dict):
        if not isinstance(cur, dict):
            return False
        ctx = child_ctx(src, ctx)
        ok = True
        for key in ("content", "text"):
            if key in src:
                ok = pairs(src[key], cur.get(key), ctx, out) and ok
        return ok
    return True


def same_ignoring_space(a: str, b: str) -> bool:
    return a == b or "".join(a.split()) == "".join(b.split())


def term_bank_names(z: zipfile.ZipFile) -> list[str]:
    names = [n for n in z.namelist() if re.fullmatch(r"term_bank_\d+\.json", n)]
    return sorted(names, key=lambda n: int(re.search(r"\d+", n).group()))


def iter_rows(sz: zipfile.ZipFile, cz: zipfile.ZipFile, name: str):
    """Pares (fonte, atual) alinhados por posição; atual=None se não houver correspondente."""
    cur_names = set(cz.namelist())
    for fn in term_bank_names(sz):
        src_rows = json.loads(sz.read(fn))
        cur_rows = json.loads(cz.read(fn)) if fn in cur_names else []
        aligned = len(cur_rows) == len(src_rows) and all(
            s[0] == c[0] and s[1] == c[1] for s, c in zip(src_rows, cur_rows)
        )
        if not aligned and cur_rows:
            print(f"aviso: {name}/{fn} não está alinhado com a fonte; traduções desse banco só entram pela memória")
        yield fn, src_rows, (cur_rows if aligned else [None] * len(src_rows))


class Memory:
    """Memória de tradução ``texto da fonte → português`` de um dicionário."""

    def __init__(self, kind: str, tables: Tables, cache: dict[str, str]) -> None:
        self.kind = kind
        self.tables = tables
        self.cache = cache
        self.votes: dict[str, Counter] = defaultdict(Counter)
        self.source_strings: set[str] = set()
        self.reused: set[str] = set()
        self.best: dict[tuple, str | None] = {}
        self.kept: set[str] = set()  # correção manual igual à fonte ("halal", "ska")
        self.validator: Validator | None = None
        self.stats = Counter()

    def is_text(self, ctx: str) -> bool:
        return ctx in TEXT_CTX if self.kind == "jitendex" else True

    def learn(self, sz: zipfile.ZipFile, cz: zipfile.ZipFile, name: str) -> None:
        groups = Counter()
        for _, src_rows, _ in iter_rows(sz, cz, name):
            groups.update((r[0], r[1]) for r in src_rows)
        for _, src_rows, cur_rows in iter_rows(sz, cz, name):
            for s, c in zip(src_rows, cur_rows):
                items: list = []
                pairs(s[5], s[5], "top", items)
                self.source_strings.update(a for ctx, a, _ in items if self.is_text(ctx))
                if c is None:
                    continue
                items = []
                same_shape = pairs(s[5], c[5], "top", items)
                changed = [(ctx, a, b) for ctx, a, b in items if a != b]
                if not changed:
                    continue
                unique = groups[(s[0], s[1])] == 1
                intact = same_shape and all(
                    same_ignoring_space(a, b) for ctx, a, b in changed if not self.is_text(ctx) and ctx not in LABEL_CTX
                )
                if not intact:
                    self.stats["entradas_com_conteudo_trocado"] += 1
                reused = "pt-reaproveitada" in re.split(r"[ ,]", c[2] or "")
                for ctx, a, b in changed:
                    # idiomas de origem saem de idiomas.csv e não podem votar em glosas ("Korean")
                    if not self.is_text(ctx) or ctx == "lang-source-content":
                        continue
                    if unique and intact:
                        self.votes[a][b] += 1
                        if reused:
                            self.reused.add(a)
                    elif self.kind == "nomes" and self.same_name(a, b):
                        # homógrafos do JMnedict: só vale se o nome romanizado é o mesmo
                        self.votes[a][b] += 1
                        if reused:
                            self.reused.add(a)
                    else:
                        self.stats["traducoes_de_homografos_descartadas"] += 1
        targets = {value for counter in self.votes.values() for value in counter}
        self.validator = Validator(self.source_strings, targets, names=self.kind == "nomes")

    @staticmethod
    def same_name(a: str, b: str) -> bool:
        head_a, head_b = a.split(" (")[0].strip(), b.split(" (")[0].strip()
        return bool(LATIN.search(head_a)) and head_a == head_b and " (" in a

    def lookup(self, src: str, ctx: str) -> str:
        """Texto a publicar para ``src`` (o próprio ``src`` se não houver tradução)."""
        key = (src, ctx if ctx in ("lang-source-content", "graphic-attribution") else "")
        if key not in self.best:
            self.best[key] = self._resolve(src, ctx)
        return self.best[key] or src

    def _resolve(self, src: str, ctx: str) -> str | None:
        t = self.tables
        if not src.strip() or JAPANESE.search(src) and not LATIN.search(src):
            return None
        if ctx == "graphic-attribution" and ("graphic-attribution", src) in t.labels:
            return t.labels[("graphic-attribution", src)]
        if ctx == "lang-source-content":
            return language_of_origin(src, t)
        stripped = src.strip()
        body = CIRCLED.sub("", stripped)
        if self.kind == "jitendex":
            manual = t.gloss_override(body)
            if manual is not None:
                self.stats["correcoes_manuais"] += 1
                if manual == body:
                    self.kept.add(src)
                return normalize(src, manual, "example-sentence-b") if manual else None
        cand = None
        for value, _ in self.votes.get(src, Counter()).most_common():
            if ctx != "example-sentence-b":
                value = dedupe_list(value)
            if self.validator.ok(src, value):
                cand = value
                break
            self.stats["candidatas_rejeitadas"] += 1
        if cand is None:
            cached = self.cache.get(src) or self.cache.get(stripped)
            if cached and self.validator.ok(src, cached):
                cand = cached
                self.stats["do_cache"] += 1
        if self.kind == "nomes":
            cand = localize_dates(src, cand if cand is not None else src)
            return cand if cand != src else None
        if cand is None:
            return None
        if ctx == "graphic-attribution" and not src.strip()[:1].isupper():
            return None  # nomes de autores e conectivos soltos
        return normalize(src, cand, ctx)


# --------------------------------------------------------------------------
# emissão
# --------------------------------------------------------------------------

def emit(node, ctx: str, memory: Memory, found: list):
    """Copia ``node`` da fonte trocando só o que é traduzível."""
    tables = memory.tables
    if isinstance(node, str):
        if ctx in LABEL_CTX:
            return tables.labels.get((ctx, node), node)
        if not memory.is_text(ctx):
            return node
        out = memory.lookup(node, ctx)
        done = out != node or node in memory.kept
        if (translatable(node) or done) and not (ctx == "graphic-attribution" and not done):
            # trocar só o formato da data não conta como tradução
            cosmetic = memory.kind == "nomes" and out != node and only_dates_differ(node, out)
            if not cosmetic:
                found.append((ctx in ("glossary", "top"), done, node in memory.reused))
        return out
    if isinstance(node, list):
        return [emit(x, ctx, memory, found) for x in node]
    if isinstance(node, dict):
        ctx = child_ctx(node, ctx)
        out = {}
        for key, value in node.items():
            if key in ("content", "text"):
                out[key] = emit(value, ctx, memory, found)
            elif key == "title" and isinstance(value, str):
                out[key] = tooltip(value, tables)
            else:
                out[key] = value
        # o texto deste nó passou a ser português
        if out.get("lang") == "en" and isinstance(node.get("content"), str) and out["content"] != node["content"]:
            out["lang"] = "pt"
        return out
    return node


def status_for(found: list) -> str | None:
    gloss = [f for f in found if f[0]] or found
    if not gloss:
        return None
    done = sum(1 for f in gloss if f[1])
    if done == len(gloss):
        return "pt-reaproveitada" if all(f[2] for f in gloss) else "pt-automatica"
    return "pt-parcial" if done else "pt-pendente"


def entry_tags(tags: str, status: str | None, name: str, tables: Tables) -> str:
    # Yomitan separa as tags por espaço (os nomes do Jitendex usam espaço rígido).
    items = [t for t in re.split(r"[ ,]", tags or "") if t and t not in STATUS]
    items = [tables.tags.get((name, t.replace("\xa0", " ")), (t,))[0].replace(" ", "\xa0") for t in items]
    if status:
        items.append(status)
    return " ".join(items)


def fix_tag_bank(raw: bytes, name: str, tables: Tables) -> bytes:
    tags = json.loads(raw)
    for t in tags:
        # os nomes do Jitendex usam espaço rígido, porque o Yomitan separa as tags por espaço
        key = (name, t[0].replace("\xa0", " "))
        if key in tables.tags:
            new_name, t[3] = tables.tags[key]
            t[0] = new_name.replace(" ", "\xa0")
    if name != "KANJIDIC-PT-completo":
        have = {t[0] for t in tags}
        tags += [d for d in STATUS_DECLS if d[0] not in have]
    return json.dumps(tags, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def fix_index(raw: bytes, name: str, desc: str, revision: str, prerelease: bool) -> dict:
    idx = json.loads(raw)
    idx.update(
        title=name,
        author="CosmionLabs",
        revision=revision,
        sourceLanguage="ja",
        targetLanguage="pt",
        isUpdatable=True,
        indexUrl=RELEASE_URL + name + ".json",
        downloadUrl=RELEASE_URL + name + ".zip",
        description=desc + (PRERELEASE if prerelease else ""),
    )
    for k in ("description", "attribution"):
        if isinstance(idx.get(k), str):
            idx[k] = idx[k].replace("現代日葡辞典", "").strip()
    return idx


def fix_kanji_bank(raw: bytes, tables: Tables, stats: Counter) -> bytes:
    rows = json.loads(raw)
    general = tables.kanji.get("*", {})
    for row in rows:
        fixes = {**general, **tables.kanji.get(row[0], {})}
        new, seen = [], set()
        for meaning in row[4]:
            fixed = fixes.get(meaning, meaning)
            stats["kanji_significados_corrigidos"] += fixed != meaning
            if fixed.lower() not in seen:
                seen.add(fixed.lower())
                new.append(fixed)
        row[4] = new
        stats["entradas"] += 1
    return json.dumps(rows, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def process(name: str, source: Path, current: Path, target: Path, desc: str, kind: str,
            revision: str, tables: Tables, cache: dict[str, str]) -> Counter:
    stats = Counter()
    memory = Memory(kind, tables, cache)
    with zipfile.ZipFile(source) as sz, zipfile.ZipFile(current) as cz:
        if kind != "kanji":
            memory.learn(sz, cz, name)
        cur_names = set(cz.namelist())

        def write(oz: zipfile.ZipFile, fn: str, raw: bytes) -> None:
            # ZipInfo novo: data fixa e sem atributos da fonte, para o ZIP ser reprodutível
            info = zipfile.ZipInfo(fn, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_STORED if fn.endswith("/") else zipfile.ZIP_DEFLATED
            info.external_attr = (0o755 if fn.endswith("/") else 0o644) << 16
            oz.writestr(info, raw)

        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as oz:
            banks = set(term_bank_names(sz)) if kind != "kanji" else set()
            for info in sz.infolist():
                fn = info.filename
                if fn in banks:
                    continue
                raw = sz.read(fn)
                if fn == "index.json":
                    idx = fix_index(cz.read(fn) if fn in cur_names else raw, name, desc, revision, kind != "kanji")
                    raw = json.dumps(idx, ensure_ascii=False, indent=2).encode("utf-8")
                    target.with_suffix(".json").write_bytes(raw)
                elif re.fullmatch(r"tag_bank_\d+\.json", fn):
                    raw = fix_tag_bank(raw, name, tables)
                    stats["tag_bank"] += 1
                elif re.fullmatch(r"kanji_bank_\d+\.json", fn):
                    raw = fix_kanji_bank(raw, tables, stats)
                write(oz, fn, raw)
            if not any(re.fullmatch(r"tag_bank_\d+\.json", n) for n in sz.namelist()):
                write(oz, "tag_bank_1.json", json.dumps(STATUS_DECLS, ensure_ascii=False).encode("utf-8"))
                stats["tag_bank"] += 1
            if kind != "kanji":
                for fn, src_rows, _ in iter_rows(sz, cz, name):
                    out_rows = []
                    for s in src_rows:
                        found: list = []
                        row = list(s)
                        row[5] = emit(s[5], "top", memory, found)
                        status = status_for(found)
                        row[2] = entry_tags(s[2], status, name, tables)
                        stats[status or "sem-texto"] += 1
                        stats["entradas"] += 1
                        out_rows.append(row)
                    write(oz, fn, json.dumps(out_rows, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    stats.update(memory.stats)
    return stats


def validate(path: Path) -> None:
    with zipfile.ZipFile(path) as z:
        assert "index.json" in z.namelist(), path
        for n in z.namelist():
            if n.endswith(".json"):
                raw = z.read(n)
                json.loads(raw)
                for bad in (b"translation-source", "[EN→PT pendente]".encode()):
                    if bad in raw:
                        raise SystemExit(f"{path.name}/{n}: contém {bad!r}")
        # O JMnedict tem uma entrada para o título 現代日葡辞典 (nome de obra);
        # o que não pode aparecer é a obra como fonte nos metadados.
        if "現代日葡辞典".encode() in z.read("index.json"):
            raise SystemExit(f"{path.name}: index.json cita 現代日葡辞典")


def find_zip(folder: Path, names: list[str]) -> Path:
    for n in names:
        if (folder / n).exists():
            return folder / n
    for n in names:
        hits = sorted(folder.rglob(n))
        if hits:
            return hits[0]
    raise SystemExit(f"não encontrei {' / '.join(names)} em {folder}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--revision", default=datetime.date.today().isoformat())
    ap.add_argument("--sources", type=Path, default=ROOT, help="pasta com os ZIPs originais")
    ap.add_argument("--out", type=Path, default=ROOT / "out", help="pasta com os pacotes gerados (ou dictionaries/)")
    ap.add_argument("--public", type=Path, default=ROOT / "public_out")
    args = ap.parse_args()
    cache: dict[str, str] = {}
    db = ROOT / "work" / "translations.sqlite"
    if db.exists():
        with sqlite3.connect(db) as con:
            cache.update(con.execute("SELECT source, translation FROM translations"))
    tables = Tables()
    args.public.mkdir(exist_ok=True)
    lines = [
        "| Dicionário | Entradas | reaproveitada | automática | parcial | pendente | sem texto a traduzir |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for name, (sources, desc, kind) in PACKAGES.items():
        target = args.public / f"{name}.zip"
        st = process(name, find_zip(args.sources, sources), find_zip(args.out, [f"{name}.zip"]),
                     target, desc, kind, args.revision, tables, cache)
        validate(target)
        n = st["entradas"] or 1
        pct = lambda k: f"{st[k]:,} ({st[k] / n * 100:.1f}%)".replace(",", ".")
        if kind != "kanji":
            lines.append(
                f"| {name} | {st['entradas']:,} | {pct('pt-reaproveitada')} | {pct('pt-automatica')} | "
                f"{pct('pt-parcial')} | {pct('pt-pendente')} | {pct('sem-texto')} |".replace(",", ".")
            )
        print(name, dict(st))
    bonus = args.public / "bonus"
    bonus.mkdir(exist_ok=True)
    for p in sorted(args.out.rglob("bonus/*.zip")):
        shutil.copy2(p, bonus / p.name)
    (args.public / "status.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
