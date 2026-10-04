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
index.html                       briefing por município (arquivo único)
prefeitos.html                   listagem nacional: filtros, resumo e exportação
etl/build_prefeitos.py           TSE → JSON por UF, chave = código IBGE
etl/fotos_prefeitos.py           fotos oficiais do TSE → WebP 240×300
etl/enriquecer_municipios.py     IBGE + Wikidata para todos os municípios → data/municipios/municipios.json
etl/capag.py                     CAPAG do Tesouro → data/capag/<uf>.json (só para o briefing; licença ODbL)
etl/consolidar_prefeitos.py      consolidado nacional → todos.json e todos.csv
etl/municipios_brasileiros_tse.csv  de-para IBGE↔TSE (5.570 linhas)
data/prefeitos/<uf>.json         gerado pelo ETL
data/prefeitos/_indice.json      cobertura por UF e data de geração
data/prefeitos/_auditoria.json   o que não casou: ler após cada execução
data/fotos/<ibge>.webp           foto do prefeito (~8 KB)
data/fotos/_manifest.json        ibge → sq e lista de quem ficou sem foto
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

**Testes:** `npm i && npm test` (ETL, fotos, consolidado, briefing e listagem). Para as fotos localmente: `pip install pillow && python3 etl/fotos_prefeitos.py`. Para checar só a sintaxe: `npm run check`.

## Interface

**Briefing.** O topo reúne retrato, ficha do prefeito e o **selo de completude**: um anel dividido por fonte (TSE, IBGE, Wikidata, Tesouro) com o total de dados encontrados ao centro. Fonte ainda respondendo aparece como pendente, não como falta, e "O que falta" nomeia cada campo ausente. Abaixo do topo, a faixa de indicadores-chave (população, PIB per capita, CAPAG e pessoal sobre a RCL, com a cor da faixa da LRF) e os painéis por tema.

**Ficha na listagem.** Clicar numa linha abre a ficha do município em modal, com os mesmos blocos em versão compacta e a CAPAG carregada sob demanda. As setas ← → percorrem a ordem do filtro atual (a tabela acompanha a página), `Esc` fecha e devolve o foco, e o endereço ganha `ficha=<código>`, então a ficha aberta pode ser compartilhada por link. Ctrl/Cmd-clique continua abrindo o briefing completo numa nova aba.

## Uso em outros sistemas

O ETL publica um consolidado nacional em endereço fixo, com CORS liberado:

```
https://SEU-SITE.vercel.app/data/prefeitos/todos.json   (≈ 5.570 registros, esquema plano)
https://SEU-SITE.vercel.app/data/prefeitos/todos.csv    (separador ;, UTF-8 com BOM, decimal com vírgula)
https://SEU-SITE.vercel.app/data/prefeitos/<uf>.json    (detalhe completo, com trajetória)
```

São **5.571 linhas, uma por município** (Brasília e municípios sem eleito definido incluídos, com as colunas de prefeito vazias e `tem_prefeito_tse = nao`). Além dos dados do prefeito, cada linha traz o que o briefing mostra do município:

| Grupo | Colunas |
|---|---|
| Território (IBGE) | `regiao`, `regiao_intermediaria`, `regiao_imediata`, `gentilico` |
| Indicadores (IBGE, cada um com `_ano`) | `populacao`, `area_km2`, `densidade`, `pib_per_capita`, `idhm`, `salario_medio_sm` (em salários mínimos), `escolarizacao_6_14` (%), `mortalidade_infantil` (por mil nascidos vivos) |
| Reconciliação | `prefeito_ibge` (+`_ano`), `prefeito_wikidata` (+`_desde`), `situacao_mandato`: consistente, divergente, parcial, sem_dados ou nao_se_aplica |
| Links | `site_oficial`, `wikidata`, `wikipedia` (só o endereço; o texto é CC BY-SA e não é redistribuído) |

Na reconciliação, um prefeito do IBGE com referência anterior ao mandato vigente (por exemplo, 2021 para o mandato iniciado em 2025) fica na coluna, mas não entra na comparação: é o prefeito anterior, e compará-lo daria falso alarme de divergência. A mesma regra vale no briefing ao vivo.

**No agente analítico offline:** arraste o `todos.json`. O envelope `registros` é reconhecido, os números chegam como números e a coluna `codigo_ibge` (7 dígitos) cruza com a CAPAG e com os municípios embutidos. Prefira o JSON ao CSV para o agente, porque o JSON preserva os tipos.

O esquema é versionado no campo `schema` (hoje 1). Colunas novas entram ao final; renomear ou remover coluna sobe a versão, então quem consome pode travar na versão que conhece. As colunas estão listadas no próprio arquivo, em `colunas`. A idade no arquivo é calculada na data de geração (`idade_referencia`); para a idade de hoje, use `nascimento`.

No Excel ou Power BI: *Dados → Obter dados → Da Web*, com a URL do CSV. Em JavaScript:

```js
const { registros } = await (await fetch(".../data/prefeitos/todos.json")).json();
const mulheresNoCeara = registros.filter(p => p.uf === "CE" && p.genero === "Feminino");
```

A página `prefeitos.html` exporta o recorte filtrado no mesmo esquema, em CSV ou JSON, e o filtro fica no endereço da página para compartilhar. O CSV neutraliza injeção de fórmula (valores iniciados por `=`, `+`, `-` ou `@` ganham um apóstrofo).

## Situação fiscal (Fase 2)

**CAPAG.** Não existe API da CAPAG: a fonte é a planilha de cada posição no portal de dados do Tesouro. O ETL descobre as posições pela API do portal (sem URL fixa), baixa só as novas ou republicadas e reconhece as colunas pelo **conteúdo**, porque o layout muda entre anos e os metadados oficiais estão vazios. O código IBGE é a coluna de 7 dígitos que bate com o cadastro (aceita o código antigo de 6). A nota final é a coluna de notas com "CAPAG" no cabeçalho; sem isso, vence a que tem A+/B+ ou D, que só ela tem. Os indicadores são identificados por palavra inteira ("Indicador II" não casa com "Indicador I"). A escala de cada indicador é decidida pela mediana da coluna e tudo é gravado como fração. A nota não é recalculada: a metodologia mudou ao longo do período. As posições de 2018 a 2020 estão com 0 bytes no portal e são ignoradas.

O log do passo "CAPAG" mostra, para cada posição, qual cabeçalho foi lido como qual campo. Se o Tesouro mudar o layout, é ali que aparece.

**Licença.** A CAPAG é publicada sob ODbL. Por decisão do produto, ela aparece só no briefing e **não** entra no `todos.json` nem nas exportações. Os arquivos `data/capag/<uf>.json` levam o aviso de licença e a atribuição ao Tesouro, e o Vercel não libera CORS para eles.

**SICONFI (ao vivo).** O briefing consulta o extrato de entregas do município, localiza o último RGF do **Executivo** (o RGF da Câmara tem outro limite e é ignorado) e lê o Anexo 1 (despesa com pessoal ÷ RCL, limites de 48,6%, 51,3% e 54%) e o Anexo 2 (dívida consolidada líquida ÷ RCL, limite de 120%). Se a linha publicada em percentual não for encontrada, o valor é calculado a partir das linhas em reais, e a origem do número aparece na tela. Se nada for reconhecido, aparece "linha não identificada", nunca um número inventado. O botão **"Ver dados brutos"** mostra as linhas recebidas, para diagnóstico.

Os rótulos de linha do RGF não puderam ser conferidos contra a API real durante o desenvolvimento. Se algum município mostrar "linha não identificada", um print dos dados brutos basta para ajustar a leitura. O RREO (saúde, educação, resultado primário) fica para a próxima rodada, depois dessa conferência.

## Decisões de projeto

**Quem é o prefeito hoje.** O TSE é a fonte de referência e a eleição mais recente vence, então uma suplementar substitui a ordinária. IBGE Cidades e Wikidata servem para conferência. Se alguma das duas aponta outra pessoa, o briefing mostra a divergência e rebaixa o card para "Eleito em 2024, segundo o TSE". Enquanto alguma fonte não responde, o veredito fica como "Conferindo".

**Comparação de nomes.** A comparação descarta acentos e partículas (de, da, dos…) e aceita nome de urna contra nome completo. Basta coincidir em dois sobrenomes ou em todos os tokens do nome mais curto. "José da Silva" e "Maria da Silva" não casam.

**Junção IBGE↔TSE.** Primeiro pelo de-para, com comparação numérica, porque o TSE usa zero à esquerda. Se não achar, cai para nome normalizado + UF contra a lista oficial do IBGE. Esse fallback cobre Boa Esperança do Norte (MT), que não está no de-para.

**Trajetória.** O vínculo entre eleições é feito por nome completo + data de nascimento, porque o TSE publica o CPF como `-4` desde 2024.

**Campo "Prefeito" do IBGE.** O ID não fica fixo no código: a aplicação o descobre pelo nome do indicador na pesquisa 33, com cache de 30 dias.

**Fotos.** A fonte é o conjunto "Fotos de candidatos" do TSE, com um ZIP por UF e licença CC BY. Os ZIPs trazem todos os candidatos e somam vários GB, então são lidos por HTTP Range: trafegam só o índice e as fotos dos prefeitos. Se o servidor não aceitar Range, o script baixa o ZIP completo, uma UF por vez. Fotos já processadas não são baixadas de novo; quando o prefeito muda, a foto é trocada e o parâmetro `?v=` invalida o cache. Na interface, a ordem é TSE, depois Wikidata (só se o nome conferir) e por fim as iniciais. O passo de fotos no workflow é tolerante a falha.

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
