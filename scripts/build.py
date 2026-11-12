"""Valida os dicionários publicados em dictionaries/ e monta dist/ para o Release.

O pipeline completo (build_dictionary.py → translate_*.py → rebuild_clean.py →
finalizar_publicacao.py) roda localmente, porque precisa das fontes originais e
do cache de tradução. Este script só confere o resultado versionado no Git:

* todo ZIP tem index.json válido, format 3, sem citar 現代日葡辞典;
* nenhum JSON contém marcas internas ("[EN→PT pendente]", spans translation-source);
* o índice solto <Nome>.json é idêntico ao index.json de dentro do ZIP;
* indexUrl/downloadUrl apontam para os assets do Release;
* nos dicionários do projeto, as tags de cada entrada são separadas por espaço
  (como o Yomitan exige) e todas estão declaradas no tag_bank.

Uso: python scripts/build.py --public [--dist dist]
"""
from pathlib import Path
import argparse
import json
import shutil
import sys
import zipfile

REPO = "CosmionLabs/yomitan-ja-pt"
RELEASE_URL = f"https://github.com/{REPO}/releases/latest/download/"
FORBIDDEN = (b"translation-source", "[EN→PT pendente]".encode())


def check(zip_path: Path, errors: list) -> None:
    with zipfile.ZipFile(zip_path) as z:
        if "index.json" not in z.namelist():
            errors.append(f"{zip_path}: sem index.json")
            return
        idx = json.loads(z.read("index.json"))
        if idx.get("format", idx.get("version")) != 3:
            errors.append(f"{zip_path}: format != 3")
        if "現代日葡辞典" in json.dumps(idx, ensure_ascii=False):
            errors.append(f"{zip_path}: index.json cita 現代日葡辞典")
        ours = REPO in str(idx.get("indexUrl", ""))
        declared: set = set()
        used: set = set()
        for name in z.namelist():
            if name.endswith(".json"):
                raw = z.read(name)
                data = json.loads(raw)
                for bad in FORBIDDEN:
                    if bad in raw:
                        errors.append(f"{zip_path}/{name}: contém {bad.decode()}")
                if ours and name.startswith("tag_bank"):
                    declared.update(t[0] for t in data)
                elif ours and name.startswith("term_bank"):
                    for row in data:
                        used.update((row[2] or "").split(" "))
        # "person,pt-automatica" seria lido pelo Yomitan como uma única tag desconhecida
        for tag in sorted(used - declared - {""}):
            errors.append(f"{zip_path}: tag de entrada não declarada no tag_bank: {tag!r}")
        loose = zip_path.with_suffix(".json")
        # bônus copiados byte a byte (wty) mantêm os URLs de atualização da fonte
        if idx.get("isUpdatable") and REPO in str(idx.get("indexUrl", "")):
            if not loose.exists():
                errors.append(f"{zip_path}: falta {loose.name}")
            elif json.loads(loose.read_text(encoding="utf-8")) != idx:
                errors.append(f"{loose}: difere do index.json do ZIP")
            for key, ext in (("indexUrl", ".json"), ("downloadUrl", ".zip")):
                if idx.get(key) != RELEASE_URL + zip_path.stem + ext:
                    errors.append(f"{zip_path}: {key} inesperado ({idx.get(key)})")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--public", action="store_true", required=True)
    p.add_argument("--dist", type=Path)
    args = p.parse_args()
    zips = sorted(Path("dictionaries").rglob("*.zip"))
    if not zips:
        print("nenhum ZIP em dictionaries/", file=sys.stderr)
        return 1
    errors: list = []
    for z in zips:
        check(z, errors)
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    if args.dist:
        args.dist.mkdir(exist_ok=True)
        for z in zips:
            shutil.copy2(z, args.dist / z.name)
            if z.with_suffix(".json").exists():
                shutil.copy2(z.with_suffix(".json"), args.dist / z.with_suffix(".json").name)
    print(f"{len(zips)} dicionários validados.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
