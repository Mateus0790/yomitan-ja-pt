# Yomitan JA → PT-BR

Dicionários japonês → português brasileiro para [Yomitan](https://github.com/yomidevs/yomitan): termos, nomes próprios e kanji.

> **Pré-lançamento (v0.x).** A estrutura dos dicionários está completa e validada, mas a maior parte das glosas do Jitendex-PT ainda está em inglês. Cada entrada traz uma tag dizendo o estado da tradução (veja abaixo).

## Instalação

Baixe os ZIPs na página de [Releases](https://github.com/CosmionLabs/yomitan-ja-pt/releases) e importe-os em **Yomitan → Dictionaries → Configure → Import**. Os dicionários principais apontam para o release mais recente, então o Yomitan consegue verificar atualizações sozinho.

| Arquivo | Conteúdo |
|---|---|
| `Jitendex-PT.zip` | Termos (base Jitendex/JMdict, com exemplos Tatoeba) |
| `JMnedict-PT.zip` | Nomes próprios (base JMnedict) |
| `KANJIDIC-PT-completo.zip` | Kanji (base KANJIDIC2, significados em português) |
| `JMdict-PT-bonus.zip`, `wty-ja-pt.zip` | Bônus, idênticos às fontes abertas |

## Estado da tradução

| Dicionário | Entradas | PT completo | Parcial | Ainda em inglês | Sem texto a traduzir* |
|---|---:|---:|---:|---:|---:|
| Jitendex-PT | 436.555 | 29.992 | 32.848 | 231.052 | 142.663 |
| JMnedict-PT | 668.008 | 4.950 | 93 | 9.788 | 653.177 |
| KANJIDIC-PT-completo | 1.944 kanji | todos | — | — | — |

\* Redirecionamentos (`⟶ outra grafia`) e nomes romanizados, que não precisam de tradução.

Tags exibidas no Yomitan:

- `pt-automatica`: tradução automática (DeepL/Gemini), ainda não revisada;
- `pt-reaproveitada`: português vindo de fonte aberta;
- `pt-parcial`: parte das glosas da entrada ainda em inglês;
- `pt-pendente`: glosas ainda em inglês.

Detalhes em [docs/RELATORIO.md](docs/RELATORIO.md).

## Conteúdo e licenças

Os dados derivam de JMdict/JMnedict/KANJIDIC2 (EDRDG), Jitendex (© Stephen Kraus, CC BY-SA 4.0), Tatoeba (CC BY 2.0 FR) e fontes portuguesas públicas. Os dados são distribuídos sob CC BY-SA 4.0 ([LICENSE-DATA](LICENSE-DATA)) e o código sob MIT ([LICENSE](LICENSE)). Consulte [ATRIBUICOES.md](ATRIBUICOES.md) e [FONTES.md](FONTES.md). O dicionário comercial 現代日葡辞典 não é usado como fonte e nenhum conteúdo dele é redistribuído.

## Build e contribuição

O pipeline completo está em `scripts/` e é descrito em [docs/BUILD.md](docs/BUILD.md). Correções manuais de tradução vão em `data/overrides/` (veja o [README da pasta](data/overrides/README.md)). Veja [CONTRIBUTING.md](CONTRIBUTING.md).

English: Japanese → Brazilian Portuguese Yomitan dictionaries (pre-release; most Jitendex glosses are still in English and are tagged `pt-pendente`). Contributions and corrections are welcome.
