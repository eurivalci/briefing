# Briefing Municipal

Seleciona UF e município e monta um briefing com o perfil do município e do prefeito, cruzando IBGE, TSE, Wikidata e Wikipedia. Cada dado carrega sua fonte, e a interface avisa quando as fontes discordam sobre quem está no cargo.

EDP Sistemas · HTML único + ETL em Python · deploy GitHub → Vercel

## Arquitetura

```
GitHub Actions (semanal)                       Navegador (index.html)
┌────────────────────────────┐                 ┌───────────────────────────────────┐
│ cdn.tse.jus.br (ZIPs)      │                 │ IBGE Localidades / Pesquisas  ◄── tempo real
│   consulta_cand_AAAA       │  build_         │ Wikidata SPARQL               ◄── tempo real
│   votacao_..._munzona      ├─ prefeitos.py ─►│ Wikipedia REST                ◄── tempo real
│   bem_candidato            │                 │ /data/prefeitos/<uf>.json     ◄── estático (ETL)
│ + de-para IBGE↔TSE         │                 │                                   │
└────────────────────────────┘                 │ reconciliação → briefing → PDF/MD │
                                               └───────────────────────────────────┘
```

O TSE não serve dados utilizáveis direto do navegador: são ZIPs de centenas de MB, sem CORS. Por isso a parte eleitoral é pré-processada. Todo o resto é consultado em tempo real, porque essas APIs liberam CORS.

## Estrutura

```
index.html                       aplicação (arquivo único, sem dependências)
etl/build_prefeitos.py           TSE → JSON por UF, chave = código IBGE
etl/municipios_brasileiros_tse.csv  de-para IBGE↔TSE (5.570 linhas)
data/prefeitos/<uf>.json         gerado pelo ETL
data/prefeitos/_indice.json      cobertura por UF e data de geração
data/prefeitos/_auditoria.json   o que não casou: ler após cada execução
.github/workflows/etl-prefeitos.yml
tests/test_etl.py                ETL ponta a ponta com fixtures no layout do TSE
tests/test_app.js                interface em jsdom com APIs simuladas
vercel.json                      cache de /data, cabeçalhos de segurança
```

## Operação

**Primeira carga (local):**

```bash
mkdir tse_raw && cd tse_raw
B=https://cdn.tse.jus.br/estatistica/sead/odsele
curl -fLO $B/consulta_cand/consulta_cand_2024.zip
curl -fLO $B/votacao_candidato_munzona/votacao_candidato_munzona_2024.zip
curl -fLO $B/bem_candidato/bem_candidato_2024.zip
curl -fLO $B/consulta_cand/consulta_cand_2020.zip      # opcional: trajetória
cd ..
python3 etl/build_prefeitos.py \
  --cand tse_raw/consulta_cand_2024.zip \
  --votos tse_raw/votacao_candidato_munzona_2024.zip \
  --bens tse_raw/bem_candidato_2024.zip \
  --historico tse_raw/consulta_cand_2020.zip
```

Depois, faça commit de `data/prefeitos/` e push. O Vercel publica sozinho.

**Rotina:** o workflow roda às segundas (03h de Brasília) e também pode ser disparado à mão em *Actions → ETL prefeitos → Run workflow*. Ele só publica se a cobertura passar de 5.400 municípios.

**Sem servidor:** abrindo o `index.html` direto do disco, use "Importar JSON da UF" no painel lateral. IBGE, Wikidata e Wikipedia continuam funcionando.

**Testes:** `npm i && npm test`. Para checar só a sintaxe: `npm run check`.

## Decisões de projeto

**Quem é o prefeito hoje.** O TSE é a fonte de referência e a eleição mais recente vence, então uma suplementar substitui a ordinária. IBGE Cidades e Wikidata servem para conferência. Se alguma das duas aponta outra pessoa, o briefing mostra a divergência e rebaixa o card para "Eleito em 2024, segundo o TSE". Enquanto alguma fonte não responde, o veredito fica como "Conferindo".

**Comparação de nomes.** A comparação descarta acentos e partículas (de, da, dos…) e aceita nome de urna contra nome completo. Basta coincidir em dois sobrenomes ou em todos os tokens do nome mais curto. "José da Silva" e "Maria da Silva" não casam.

**Junção IBGE↔TSE.** Primeiro pelo de-para, com comparação numérica, porque o TSE usa zero à esquerda. Se não achar, cai para nome normalizado + UF contra a lista oficial do IBGE. Esse fallback cobre Boa Esperança do Norte (MT), que não está no de-para.

**Trajetória.** O vínculo entre eleições é feito por nome completo + data de nascimento, porque o TSE publica o CPF como `-4` desde 2024.

**Campo "Prefeito" do IBGE.** O ID não fica fixo no código: a aplicação o descobre pelo nome do indicador na pesquisa 33, com cache de 30 dias.

**HubPolítico.** Entra apenas como link para a página do município. Não há raspagem: é empresa privada, e os dados equivalentes vêm da fonte primária (TSE).

**Segurança.** Há uma CSP restrita às origens usadas. Todo texto externo é escapado e só são aceitas URLs http(s). Respostas atrasadas de um município anterior são descartadas, e requisições idênticas em voo viram uma só.

**LGPD.** O briefing mostra só dados públicos ligados ao exercício e à disputa do mandato (art. 7º, III e §4º). Cor/raça, CPF, título eleitoral e e-mail são descartados ainda no ETL.

## Validar na primeira execução com dados reais

O desenvolvimento usou fixtures no layout documentado do TSE. Quatro pontos só se confirmam com os arquivos de verdade:

| Ponto | Como conferir | Se falhar |
|---|---|---|
| Linhas de vice com `DS_SIT_TOT_TURNO` preenchido | campo `vice` dos JSON gerados | o ETL já busca o vice pelo número da chapa; ajustar só o critério de desempate |
| Link do DivulgaCand para 2024 | abrir o link em um briefing | trocar o padrão em `rGov()` (busque `divulgacandcontas`) |
| Campo "Prefeito" na pesquisa 33 do IBGE | tabela de fontes do briefing: "IBGE Cidades — painel" | aparece como falhou e o briefing segue com TSE + Wikidata |
| Cobertura da P1585 na Wikidata | linha "Wikidata" na tabela de fontes | municípios sem item ficam sem foto, site e verbete |

Leia também o `_auditoria.json`. Ali ficam os códigos TSE não resolvidos, os municípios resolvidos por nome e os municípios sem eleito nos arquivos (eleição anulada ou sub judice).

## Limitações conhecidas

- IDHM é do Censo 2010 (o IBGE ainda não publicou série nova por município).
- A Wikidata atualiza devagar em municípios pequenos; por isso nunca é usada como referência, só como conferência.
- Os links para IBGE Cidades e HubPolítico são montados a partir do nome; nomes com apóstrofo podem gerar um caminho diferente do usado pelo site.
- Brasília não tem prefeito; a interface trata o DF à parte.

## Próximas fases

2. Seção fiscal: CAPAG e SICONFI (RREO/RGF), reaproveitando os módulos existentes.
3. Extração dos sites das prefeituras via proxy serverless + LLM, com citação por campo.
