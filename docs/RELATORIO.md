# Relatório de publicação (revisão 2026-11-12)

Gerado por `scripts/finalizar_publicacao.py`, que reconstrói cada pacote a partir da fonte e compara cada string publicada com a string correspondente da fonte.

## Por dicionário

| Dicionário | Entradas | reaproveitada | automática | parcial | pendente | sem texto a traduzir |
|---|---:|---:|---:|---:|---:|---:|
| Jitendex-PT | 436.555 | 0 | 29.992 (6,9%) | 32.848 (7,5%) | 231.052 (52,9%) | 142.663 (32,7%) |
| JMnedict-PT | 668.008 | 0 | 4.950 (0,7%) | 93 | 9.788 (1,5%) | 653.177 (97,8%) |
| KANJIDIC-PT-completo | 1.944 kanji | a fonte já é em português | | | | |

"Sem texto a traduzir" reúne redirecionamentos entre grafias (Jitendex), nomes romanizados sem glosa e nomes cuja única mudança é o formato da data (JMnedict).

Considerando só as entradas do Jitendex-PT que têm glosa, 10,2% estão inteiramente em português, 11,2% estão parciais e 78,6% ainda estão em inglês.

Os números de "automática" caíram em relação à revisão 2026-10-20 (33.550 no Jitendex-PT) porque traduções erradas foram retiradas, e não porque algo deixou de ser traduzido.

## Correções desta revisão

A revisão anterior foi comparada, string a string, com as fontes (Jitendex `2026.10.03.0`, JMnedict `2026-10-20`, KANJIDIC em português). Foram encontrados e corrigidos:

- **Conteúdo trocado entre homógrafos.** `rebuild_clean.py` casava as entradas só por termo e leitura, então entradas diferentes com a mesma grafia recebiam todas o conteúdo de uma delas. No Jitendex-PT, 1.127 entradas tinham leituras, formas, remissões ou glosas de outra entrada (ウィーク "week" aparecia como "fraco", ブランチ "branch" como "brunch"). No JMnedict-PT, cerca de 23,7 mil leituras e nomes estavam trocados (明良 com a leitura de outro 明良; pessoas homônimas com datas e profissão de outra pessoa). Tudo isso voltou a ser igual à fonte, e a chave agora inclui o número de sequência.
- **Tags separadas por vírgula.** O Yomitan separa as tags de uma entrada por espaço. `person,pt-automatica` era lido como uma única tag desconhecida em 40.856 entradas do Jitendex-PT e 18.253 do JMnedict-PT, então nem a tag original nem a de status apareciam direito. `scripts/build.py` agora recusa tags não declaradas.
- **Espaços apagados.** 44.963 strings tinham perdido espaços que fazem parte da fonte: o separador ` | ` das atribuições virava `|`, e o espaço ideográfico das frases de exemplo virava espaço comum ou sumia.
- **Etiquetas traduzidas errado ou não traduzidas.** A área `golf` aparecia como "onda", a classe `nari` como "dança", `wasei` como "palavra emprestada", `Hindi` como "não se aplica". Cerca de 34 mil ocorrências de etiquetas continuavam em inglês (`exp`, `food`, `art`, `engineering`, "See also" em parte das entradas). As etiquetas agora saem de uma tabela fechada, `data/overrides/etiquetas-jitendex.csv`.
- **Tooltips.** Os 833 mil tooltips das etiquetas (atributo `title`) estavam todos em inglês e foram traduzidos.
- **Idioma de origem.** A palavra estrangeira era às vezes traduzida ou alterada (`German: "Lebensphilosophie"` virava `Alemão: “filosofia de vida”`). Agora o nome do idioma vem de `idiomas.csv` e a palavra é copiada da fonte.
- **Glosas de uma palavra.** Sem contexto, o tradutor automático tratou várias palavras como se fossem de outro idioma ou escolheu o sentido errado: `die` → "a", `elk` → "cada", `drum` → "estrada", `fart` → "velocidade", `sweet` → "fofo", `blade` → "folhas". As cerca de 6.400 glosas de uma palavra foram revisadas à mão; `correcoes-glosas.csv` tem 775 correções, das quais 176 devolvem ao inglês palavras ambíguas demais para uma tradução única ("right", "run", "light").
- **Inglês passando por tradução.** Glosas que tinham mudado na fonte ficaram com o texto inglês antigo e a tag de traduzidas. Foram revertidas.
- **Trocas do léxico antigo em nomes.** "Bad Godesberg" → "ruim Godesberg", "Place de la Concorde" → "lugar de la Concorde", "japonês Corporação Nacional…". Revertidas.
- **Ajustes mecânicos.** Itens repetidos na mesma glosa ("sem graça; sem graça"), maiúscula inicial indevida, aspas tipográficas, número circulado perdido, e o gênero das notas de uso ("usada" → "usado").
- **Datas do JMnedict.** O mesmo campo aparecia como `18/10/1964`, `7/10/1975` e `5 de maio de 1964`. Todas as datas saem como `dd/mm/aaaa` (18,5 mil entradas), e o nome romanizado mantém a grafia da fonte.
- **KANJIDIC.** 165 significados corrigidos: restos em inglês (`-ous`, `enraged`, `chafe`, `raid`), palavras corrompidas na fonte (`umsertar`, `jáfusão`, `hátisfeito`, `exist6encia`), erros de grafia e a ortografia anterior ao acordo (`idéia`, `jóia`). As descrições das tags também foram traduzidas.
- **Atributo `lang`.** Nós com texto traduzido passaram de `lang="en"` para `lang="pt"`.
- **Tags do `tag_bank`.** Nomes e descrições das tags do Jitendex-PT e descrições do JMnedict-PT e do KANJIDIC estão em português.

Rodar o script duas vezes seguidas produz ZIPs idênticos byte a byte.

## Pendências

- Cerca de 229 mil strings únicas ainda estão em inglês.
- As traduções automáticas de glosas com duas ou mais palavras, das frases de exemplo e do JMnedict não foram revisadas uma a uma; passaram só pelas verificações automáticas.
- Cerca de 100 traduções corretas de entradas homógrafas foram descartadas junto com as erradas, porque não há como distinguir as duas automaticamente. Voltam quando forem traduzidas de novo.
- Os significados do KANJIDIC vêm da fonte em português, que tem outros erros de tradução além dos corrigidos aqui (por exemplo, 丸 "mês"). Uma comparação completa com o KANJIDIC em inglês ainda não foi feita.
- `docs/AMOSTRA*.md` são amostras antigas e não refletem esta revisão.
