# Correções manuais

Coloque aqui arquivos de correção documentados. Eles têm prioridade sobre a tradução automática e não devem copiar conteúdo de fontes pessoais ou restritas.

`scripts/finalizar_publicacao.py` lê estas tabelas toda vez que gera `public_out/`:

| Arquivo | Colunas | Uso |
|---|---|---|
| `etiquetas-jitendex.csv` | `contexto,en,pt` | Etiquetas e rótulos fixos do Jitendex (classe gramatical, registro, área, dialeto, "Ver também", "Nota"). O `contexto` é o valor de `data.content` do nó. |
| `tooltips-jitendex.csv` | `en,pt` | Texto do atributo `title` (tooltip) das etiquetas. Os padrões repetitivos ("Godan verb with 'ru' ending", "龍 is the old character form of 竜") são traduzidos pelo script. |
| `idiomas.csv` | `en,pt` | Nomes de idioma do campo "idioma de origem". A palavra estrangeira entre aspas é sempre copiada da fonte. |
| `tags.csv` | `dicionario,tag,nome,nota` | Nome e descrição das tags do `tag_bank` de cada dicionário. |
| `correcoes-glosas.csv` | `en,pt` | Correção de glosas do Jitendex. A chave é a glosa em inglês sem o número circulado; se não houver linha com a grafia exata, vale a comparação sem diferenciar maiúsculas. `pt` vazio mantém a glosa em inglês (use para palavras ambíguas demais, como "right" ou "run", que não têm uma tradução que sirva a todas as entradas). |
| `correcoes-kanji.csv` | `kanji,errado,certo` | Correção de significados do KANJIDIC em português. `kanji` igual a `*` vale para todos os kanji. |

`glossario-etiquetas.csv` e `glossario-gramatical.csv` são usados por `scripts/build_dictionary.py` na primeira etapa do build; na publicação valem as tabelas acima.

## Como corrigir uma tradução

1. Ache a glosa em inglês na fonte (o Jitendex mostra a mesma entrada em inglês).
2. Acrescente uma linha em `correcoes-glosas.csv` (ou `correcoes-kanji.csv`).
3. Rode `python scripts/finalizar_publicacao.py` e confira a entrada no ZIP gerado.
