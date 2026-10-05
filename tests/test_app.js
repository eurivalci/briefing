// Teste funcional do index.html em jsdom, com todas as APIs simuladas.
// Uso: npm i jsdom@24 && node tests/test_app.js
"use strict";
const fs = require("fs");
const path = require("path");
const assert = require("assert");
const { JSDOM } = require("jsdom");

const html = fs.readFileSync(path.join(__dirname, "..", "index.html"), "utf8");
const sleep = (ms) => new Promise(r => setTimeout(r, ms));

const TSE_CE = {
  uf: "CE", gerado_em: "2026-09-30T12:00:00+00:00", fontes: {},
  municipios: {
    "2304400": {
      tse: {sg_ue: "13897", nm_ue: "FORTALEZA"},
      eleicao: {ano: 2024, cd_eleicao: "2045202024", descricao: "Eleições Municipais 2024", data: "2024-10-27", turno: 2, suplementar: false},
      prefeito: {sq: "60001", nome: "EVANDRO TESTE LEAL", nome_urna: "EVANDRO", numero: "13", partido: "PT", partido_nome: "PARTIDO DOS TRABALHADORES",
        federacao: null, coligacao: null, nascimento: "1970-03-15", genero: "MASCULINO", instrucao: "SUPERIOR COMPLETO", ocupacao: "EMPRESÁRIO",
        votos: 650000, votos_pct: 65.0, bens_total: 351500.5, bens_qtd: 2, foto: "fotos/2304400.webp?v=60001"},
      vice: {nome: "GABRIELLA VICE", nome_urna: "GABRIELLA", partido: "PT"},
      eleicoes_anteriores_no_mandato: [],
      trajetoria: [{ano: 2020, cargo: "Vereador", local: "FORTALEZA", uf: "CE", partido: "PT", resultado: "ELEITO POR QP"}],
    },
    "2303709": {
      tse: {sg_ue: "13730", nm_ue: "CAUCAIA"},
      eleicao: {ano: 2024, cd_eleicao: "2045202024", data: "2024-10-06", turno: 1, suplementar: false},
      prefeito: {sq: "60100", nome: "FULANO ORIGINAL SILVA", nome_urna: "FULANO", partido: "PSB", nascimento: "1980-01-01",
        votos: null, votos_pct: null, bens_total: null, bens_qtd: null},
      vice: null, eleicoes_anteriores_no_mandato: [], trajetoria: [],
    },
  },
};

const CHAMADAS_RGF = [];
const CAPAG_CE = {
  aviso: "O resultado apurado para a CAPAG não vincula a posição do Tesouro Nacional.", licenca: "ODbL 1.0",
  posicoes: [{posicao: "2026-06-01"}, {posicao: "2026-09-01"}],
  municipios: {
    "2304400": [
      {posicao: "2026-06-01", ano_base: 2025, capag: "C"},
      {posicao: "2026-09-01", ano_base: 2025, capag: "B", endividamento: 0.352, nota_endividamento: "A",
       poupanca_corrente: 0.9123, nota_poupanca_corrente: "B", liquidez: 0.41, nota_liquidez: "A", qualidade_informacao: "A",
       origem_nota: "Indicadores", observacao: "Sem ressalvas"},
    ],
    "2303709": [{posicao: "2026-09-01", ano_base: 2025, capag: null, capag_publicada: "n.d."}],
  },
};

function wdRows(atual, antigo, extras = {}) {
  const b = (v) => ({value: v});
  const base = {mun: b("http://www.wikidata.org/entity/Q43463"), munLabel: b("Fortaleza"),
    artigo: b("https://pt.wikipedia.org/wiki/Fortaleza"), ...extras};
  return {results: {bindings: [
    {...base, st: b("s1"), mayor: b("http://www.wikidata.org/entity/Q1"), mayorLabel: b(antigo), inicio: b("2021-01-01T00:00:00Z"), fim: b("2024-12-31T00:00:00Z")},
    {...base, st: b("s2"), mayor: b("http://www.wikidata.org/entity/Q2"), mayorLabel: b(atual), inicio: b("2025-01-01T00:00:00Z"),
      img: b("http://commons.wikimedia.org/wiki/Special:FilePath/Foto.jpg"), partidoLabel: b("Partido dos Trabalhadores"),
      artigoPref: b("https://pt.wikipedia.org/wiki/Evandro_Leal")},
  ]}};
}

const atrasos = {};  // url-substring -> ms
const respostasExtras = {};
function respostas(url) {
  const u = decodeURIComponent(url);
  for (const [k, v] of Object.entries(respostasExtras)) if (u.includes(k)) return v;
  if (u.includes("/localidades/estados/CE/municipios")) return [{id: 2303709, nome: "Caucaia"}, {id: 2304400, nome: "Fortaleza"}];
  if (u.includes("/localidades/estados/DF/municipios")) return [{id: 5300108, nome: "Brasília"}];
  if (u.includes("/localidades/municipios/2304400")) return {id: 2304400, nome: "Fortaleza", microrregiao: null,
    "regiao-imediata": {nome: "Fortaleza", "regiao-intermediaria": {nome: "Fortaleza", UF: {sigla: "CE", regiao: {nome: "Nordeste"}}}}};
  if (u.includes("/localidades/municipios/2303709")) return {id: 2303709, nome: "Caucaia", microrregiao: null,
    "regiao-imediata": {nome: "Fortaleza", "regiao-intermediaria": {nome: "Fortaleza", UF: {sigla: "CE", regiao: {nome: "Nordeste"}}}}};
  if (u.includes("/localidades/municipios/5300108")) return {id: 5300108, nome: "Brasília", microrregiao: null,
    "regiao-imediata": {nome: "Distrito Federal", "regiao-intermediaria": {nome: "Distrito Federal", UF: {sigla: "DF", regiao: {nome: "Centro-Oeste"}}}}};
  if (u.endsWith("/pesquisas/33/indicadores")) return [{id: 1, indicador: "Grupo", children: [
    {id: 29169, indicador: "Gentílico", children: []}, {id: 29170, indicador: "Prefeito", children: []}]}];
  if (u.includes("/indicadores/29170/resultados/2304400")) return [{id: 29170, res: [{localidade: "2304400", res: {"2025": "EVANDRO TESTE LEAL"}}]}];
  if (u.includes("/indicadores/29170/resultados/2303709")) return [{id: 29170, res: [{localidade: "2303709", res: {"2025": "BELTRANO SUBSTITUTO"}}]}];
  if (u.includes("/indicadores/29169/resultados/")) return [{id: 29169, res: [{localidade: "x", res: {"2025": "fortalezense"}}]}];
  if (u.includes("/indicadores/30279/")) return {__status: 500};
  const mInd = u.match(/indicadores\/(\d+)\/resultados\/\d+/);
  if (mInd) return [{id: +mInd[1], res: [{localidade: "x", res: {"2010": "100", "2022": "2428708", "2025": "-"}}]}];
  if (u.includes("query.wikidata.org")) {
    if (u.includes('"2303709"')) return wdRows("Beltrano Substituto", "Fulano Original Silva", {site: {value: "javascript:alert(1)"}});
    if (u.includes('"5300108"')) return {results: {bindings: []}};
    return wdRows("Evandro Leal", "Prefeito Antigo", {site: {value: "https://www.fortaleza.ce.gov.br/"}});
  }
  if (u.includes("pt.wikipedia.org/api/rest_v1/page/summary/Fortaleza")) return {title: "Fortaleza", extract: "Fortaleza é a capital do Ceará. <img src=x onerror=alert(1)>",
    content_urls: {desktop: {page: "https://pt.wikipedia.org/wiki/Fortaleza"}}};
  if (u.includes("pt.wikipedia.org/api/rest_v1/page/summary/Evandro_Leal")) return {title: "Evandro Leal", extract: "Evandro Leal é um político brasileiro.",
    content_urls: {desktop: {page: "https://pt.wikipedia.org/wiki/Evandro_Leal"}}};
  if (u.includes("data/prefeitos/ce.json")) return TSE_CE;
  if (u.includes("data/capag/ce.json")) return CAPAG_CE;
  if (u.includes("data/capag/_distribuicao.json")) return {posicoes: [
    {posicao: "2026-06-01", brasil: {A: 1000, B: 2000, C: 2000, D: 300, "n.d.": 270}, ufs: {CE: {A: 40, B: 60, C: 70, D: 4}}},
    {posicao: "2026-09-01", brasil: {"A+": 200, A: 900, B: 2100, C: 1900, D: 300, "n.d.": 170}, ufs: {CE: {A: 50, B: 70, C: 50, D: 4}}}]};
  if (u.includes("apidatalake.tesouro.gov.br")) {
    const ano = String(new Date().getFullYear());
    if (u.includes("extrato_entregas")) {
      if (u.includes("id_ente=2303709") || !u.includes(`an_referencia=${ano}`)) return {items: [], hasMore: false};
      return {items: [
        {exercicio: +ano, instituicao: "Câmara Municipal de Fortaleza", entregavel: "Relatório de Gestão Fiscal", periodo: 3, periodicidade: "Q", status_relatorio: "HO"},
        {exercicio: +ano, instituicao: "Prefeitura Municipal de Fortaleza - CE", entregavel: "Relatório de Gestão Fiscal", periodo: 2, periodicidade: "Q", status_relatorio: "HO", data_status: `${ano}-05-28`},
        {exercicio: +ano, instituicao: "Prefeitura Municipal de Fortaleza - CE", entregavel: "Relatório de Gestão Fiscal", periodo: 1, periodicidade: "Q", status_relatorio: "HO"},
        {exercicio: +ano, instituicao: "Prefeitura Municipal de Fortaleza - CE", entregavel: "Relatório Resumido de Execução Orçamentária", periodo: 4, periodicidade: "B", status_relatorio: "HO"},
      ], hasMore: false};
    }
    if (/\/rreo\?/.test(u)) {
      if (/Simplificado/.test(u.replace(/\+/g, " "))) return {items: []};
      if (/Anexo[+ ]01/.test(u)) return {items: [
        {conta: "RECEITAS (EXCETO INTRA-ORÇAMENTÁRIAS) (I)", coluna: "PREVISÃO ATUALIZADA (a)", valor: 12000000000},
        {conta: "RECEITAS (EXCETO INTRA-ORÇAMENTÁRIAS) (I)", coluna: "Até o Bimestre (c)", valor: 8000000000},
        {conta: "RECEITAS (EXCETO INTRA-ORÇAMENTÁRIAS) (I)", coluna: "% (c/a)", valor: 66.7},
        {conta: "RECEITAS CORRENTES", coluna: "Até o Bimestre (c)", valor: 7500000000},
        {conta: "RECEITAS CORRENTES", coluna: "Até o Bimestre (c)", valor: 90000000},     // repetição intraorçamentária
        {conta: "Impostos, Taxas e Contribuições de Melhoria", coluna: "Até o Bimestre (c)", valor: 2250000000},
        {conta: "Transferências Correntes", coluna: "Até o Bimestre (c)", valor: 4500000000},
        {conta: "DESPESAS (EXCETO INTRA-ORÇAMENTÁRIAS) (VIII)", coluna: "DOTAÇÃO ATUALIZADA (e)", valor: 12500000000},
        {conta: "DESPESAS (EXCETO INTRA-ORÇAMENTÁRIAS) (VIII)", coluna: "DESPESAS EMPENHADAS ATÉ O BIMESTRE (f)", valor: 8400000000},
        {conta: "DESPESAS (EXCETO INTRA-ORÇAMENTÁRIAS) (VIII)", coluna: "DESPESAS LIQUIDADAS ATÉ O BIMESTRE (h)", valor: 7000000000},
        {conta: "PESSOAL E ENCARGOS SOCIAIS", coluna: "DESPESAS LIQUIDADAS ATÉ O BIMESTRE (h)", valor: 3500000000},
        {conta: "INVESTIMENTOS", coluna: "DESPESAS LIQUIDADAS ATÉ O BIMESTRE (h)", valor: 560000000},
      ]};
      if (/Anexo[+ ]12/.test(u)) return {items: [
        {conta: "PERCENTUAL DA RECEITA DE IMPOSTOS E TRANSFERÊNCIAS CONSTITUCIONAIS E LEGAIS APLICADO EM ASPS", coluna: "DESPESAS LIQUIDADAS (e)", valor: 21.4},
        {conta: "PERCENTUAL DA RECEITA DE IMPOSTOS E TRANSFERÊNCIAS CONSTITUCIONAIS E LEGAIS APLICADO EM ASPS", coluna: "DESPESAS EMPENHADAS (d)", valor: 23.9},
      ]};
      if (/Anexo[+ ]08/.test(u)) return {items: [
        {conta: "PERCENTUAL DE APLICAÇÃO EM MDE SOBRE A RECEITA LÍQUIDA DE IMPOSTOS", coluna: "VALOR", valor: 19.8},
        {conta: "VALOR APLICADO EM MDE", coluna: "VALOR", valor: 980000000},
      ]};
      return {items: []};
    }
    if (/\/rgf\?/.test(u)) {
      CHAMADAS_RGF.push(u);
      if (!/co_tipo_demonstrativo=RGF&/.test(u.replace(/\+/g, " ").replace("RGF Simplificado", "X"))) return {items: []};
      if (/Anexo[+ ]01/.test(u)) return {items: [
        {anexo: "RGF-Anexo 01", cod_conta: "DespesaTotalComPessoal", conta: "DESPESA TOTAL COM PESSOAL - DTP (VIII) = (IIIa + IIIb)", coluna: "VALOR", valor: 4973000000},
        {anexo: "RGF-Anexo 01", cod_conta: "DespesaTotalComPessoal", conta: "DESPESA TOTAL COM PESSOAL - DTP (VIII) = (IIIa + IIIb)", coluna: "% SOBRE A RCL AJUSTADA", valor: 49.73},
        {anexo: "RGF-Anexo 01", cod_conta: "LimiteMaximo", conta: "LIMITE MÁXIMO (VII) (incisos I, II e III, art. 20 da LRF)", coluna: "% SOBRE A RCL AJUSTADA", valor: 54},
        {anexo: "RGF-Anexo 01", cod_conta: "ReceitaCorrenteLiquidaAjustada", conta: "RECEITA CORRENTE LÍQUIDA AJUSTADA (VI)", coluna: "VALOR", valor: 10000000000},
      ]};
      if (/Anexo[+ ]02/.test(u)) return {items: [
        {anexo: "RGF-Anexo 02", conta: "% DA DCL SOBRE A RCL AJUSTADA (III/RCL)", coluna: "SALDO DO EXERCÍCIO ANTERIOR", valor: 30.1},
        {anexo: "RGF-Anexo 02", conta: "% DA DCL SOBRE A RCL AJUSTADA (III/RCL)", coluna: "Até o 1º Quadrimestre", valor: 28.5},
        {anexo: "RGF-Anexo 02", conta: "% DA DCL SOBRE A RCL AJUSTADA (III/RCL)", coluna: "Até o 2º Quadrimestre", valor: 27.9},
        {anexo: "RGF-Anexo 02", conta: "% DA DCL SOBRE A RCL AJUSTADA (III/RCL)", coluna: "Até o 3º Quadrimestre", valor: null},
      ]};
    }
    return {items: []};
  }
  return {__status: 404};
}

const chamadas = [];
function fetchMock(url) {
  chamadas.push(url);
  const r = respostas(url);
  const atraso = Object.entries(atrasos).find(([k]) => decodeURIComponent(url).includes(k))?.[1] || 5;
  return new Promise(res => setTimeout(() => {
    const st = r && r.__status;
    res({ok: !st, status: st || 200, json: async () => JSON.parse(JSON.stringify(r))});
  }, atraso));
}

async function esperar(win, cond, ms = 3000, rotulo = "condição") {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) { if (cond()) return; await sleep(20); }
  throw new Error("timeout esperando " + rotulo);
}

(async () => {
  const alertas = [];
  const dom = new JSDOM(html, {
    url: "http://localhost/#/CE/2304400", runScripts: "dangerously", pretendToBeVisual: true,
    beforeParse(w) {
      w.fetch = fetchMock;
      w.alert = (m) => alertas.push(m);
      w.matchMedia = () => ({matches: false});
      w.HTMLElement.prototype.scrollIntoView = function () {};
      Object.defineProperty(w.navigator, "clipboard", {value: {writeText: async (t) => { w.__copiado = t; }}});
      w.print = () => { w.__impresso = true; };
    },
  });
  const w = dom.window, d = w.document, BM = () => w.__BM__;

  // ---------- unidade: comparação de nomes
  const nc = BM().nomesCompativeis;
  assert(nc("EVANDRO TESTE LEAL", "Evandro Leal"), "nome completo x rótulo curto");
  assert(nc("EVANDRO", "EVANDRO TESTE LEAL"), "nome de urna x completo");
  assert(!nc("FULANO ORIGINAL SILVA", "Beltrano Substituto"), "pessoas diferentes");
  assert(!nc("JOSE DA SILVA", "MARIA DA SILVA"), "partícula e sobrenome comum não bastam");
  assert.strictEqual(BM().slug("Pau d'Arco"), "pau-darco");
  assert.strictEqual(BM().slug("São João del-Rei"), "sao-joao-del-rei");

  // ---------- fluxo principal via hash
  await esperar(w, () => d.querySelector("#s-gov h4"), 3000, "card do prefeito");
  await esperar(w, () => /Consistente/.test(d.querySelector("#s-mand").textContent), 3000, "reconciliação");
  assert.strictEqual(BM().S.secoes.ibge.status, "ok", "veredito só depois de todas as fontes");
  assert.strictEqual(d.querySelector(".uf[data-uf=CE]").getAttribute("aria-pressed"), "true");
  assert.strictEqual(d.querySelector("#munlist li[aria-selected=true]").dataset.id, "2304400", "lista destaca o município aberto");
  assert.strictEqual(d.querySelector("#s-head h2").textContent, "Fortaleza");
  assert(/gentílico fortalezense/.test(d.querySelector("#s-head").textContent), "gentílico do IBGE");
  assert.strictEqual(d.querySelector("#s-gov h4").textContent, "Evandro");
  const gov = d.querySelector("#s-gov").textContent;
  assert(/650\.000/.test(gov) && /65,0% dos válidos/.test(gov), "votos formatados");
  assert(/R\$\s?351\.501/.test(gov) || /R\$\s?351\.500/.test(gov), "bens formatados: " + gov.match(/R\$[^e]+/));
  assert(/2º turno/.test(gov) && /Gabriella/.test(gov), "turno e vice");
  assert.strictEqual(d.querySelector("#s-gov img.portrait").getAttribute("src"), "./data/fotos/2304400.webp?v=60001", "foto oficial do TSE tem prioridade");
  assert(/TSE/.test(d.querySelector("#s-gov figcaption").textContent), "crédito da foto");
  const fT = BM().fotoTSE;
  assert(!fT({foto: "../../etc/passwd"}) && !fT({foto: "javascript:alert(1)"}) && !fT({foto: "fotos/123.webp"}) && !fT({}), "caminho de foto validado");
  assert.strictEqual(d.querySelectorAll("#s-mand .tag.ok").length, 2, "IBGE e Wikidata conferem com o TSE");
  // indicadores: valor "-" ignorado, ano mais recente válido, 1 indicador com HTTP 500 fica de fora
  await esperar(w, () => d.querySelector("#s-ind .grid-ind"), 3000, "indicadores");
  const ind = d.querySelector("#s-ind").textContent;
  assert(/2\.428\.708/.test(ind) && /2022/.test(ind), "ano mais recente com valor");
  assert(/1 indicador\(es\) não responderam/.test(ind), "falha parcial informada");
  assert(/salários mínimos/.test(ind), "unidade correta do salário médio");
  // wikipedia: XSS neutralizado
  await esperar(w, () => /capital do Ceará/.test(d.querySelector("#s-sobre").textContent), 3000, "resumo wikipedia");
  assert(!d.querySelector("#s-sobre img"), "HTML do extrato não pode virar elemento");
  assert(/&lt;img/.test(d.querySelector("#s-sobre").innerHTML), "extrato escapado");
  assert(/Eleito por quociente partidário/.test(d.querySelector("#s-traj").textContent), "resultado formatado");
  assert(/Vereador/.test(d.querySelector("#s-traj").textContent) && /político brasileiro/.test(d.querySelector("#s-traj").textContent), "trajetória TSE + wikipedia");
  assert(d.querySelector('#s-head a[href="https://www.fortaleza.ce.gov.br/"]'), "site oficial");
  // a mesma URL do TSE pedida por duas seções gera uma única requisição
  assert.strictEqual(chamadas.filter(u => u.includes("data/prefeitos/ce.json")).length, 1, "dedupe do TSE");

  // ---------- veredito não sai com fonte pendente
  atrasos["/indicadores/29170/"] = 500;
  BM().limparCache();
  BM().abrirMunicipio("CE", "2304400");
  await esperar(w, () => /Conferindo/.test(d.querySelector("#s-mand").textContent), 2000, "estado intermediário");
  await esperar(w, () => /Consistente/.test(d.querySelector("#s-mand").textContent), 3000, "veredito final");
  delete atrasos["/indicadores/29170/"];

  // ---------- seção fiscal: CAPAG + SICONFI
  await esperar(w, () => /elegível/.test(d.querySelector("#s-fisc")?.textContent || "") && /quadrimestre/.test(d.querySelector("#s-fisc").textContent), 3000, "seção fiscal");
  const fisc = d.querySelector("#s-fisc").textContent.replace(/\s+/g, " ");
  assert(/set\/2026/.test(fisc) && d.querySelector("#s-fisc .letra").textContent === "B", "nota da posição mais recente");
  const hist = [...d.querySelectorAll("#s-fisc table.hist tbody tr")].map(r => [...r.cells].map(c => c.textContent.trim()));
  assert.strictEqual(hist.length, 2, "histórico com as duas posições");
  assert.deepStrictEqual(hist[0].slice(0, 6), ["set/2026", "2025", "B", "A", "B", "A"], "posição mais recente primeiro, com nota por indicador");
  assert.strictEqual(hist[1][2], "C"); assert.strictEqual(hist[1][7], "não elegível");
  assert.strictEqual(d.querySelectorAll("#s-fisc .ind-capag").length, 4, "quatro cartões de indicador");
  assert(/Liquidez relativa/.test(fisc) && /\(Caixa bruta − obrigações financeiras\) ÷ RCL/.test(fisc), "fórmula vigente da liquidez");
  assert(/35,2%/.test(fisc) && /91,2%/.test(fisc), "indicadores da CAPAG em %");
  assert(/ODbL/.test(fisc) && /não vincula/.test(fisc), "licença e aviso do Tesouro");
  assert(/Origem da nota final: Indicadores\. Sem ressalvas\./.test(fisc) && /Qualidade da informação \(ICF\)/.test(fisc), "origem, observação e ICF");
  assert(/Portaria Normativa MF nº 1\.583\/2023/.test(fisc), "base legal citada");
  // distribuição: UF por padrão, com a nota do município marcada; troca para Brasil
  const segUF = [...d.querySelectorAll("#s-fisc .barra-dist")][0];
  assert(segUF && segUF.querySelector(".seg.minha") && /B/.test(segUF.querySelector(".seg.minha").textContent), "nota do município marcada");
  assert(/174 municípios/.test(d.querySelector("#s-fisc .dist").textContent), "total da UF na posição mais recente");
  d.querySelector('#s-fisc [data-escopo="br"]').click();
  assert(/5\.570 municípios/.test(d.querySelector("#s-fisc .dist").textContent), "troca para Brasil");
  d.querySelector('#s-fisc [data-escopo="uf"]').click();
  // execução orçamentária e pisos
  const ex = [...d.querySelectorAll("#s-fisc .card")].find(c => /Execução orçamentária/.test(c.textContent)).textContent.replace(/\s+/g, " ");
  assert(/4º bimestre/.test(ex) && /R\$ 8,00 bi/.test(ex) && /66,7% da previsão/.test(ex), "receita realizada e % da previsão: " + ex.slice(0, 300));
  assert(/Déficit de R\$ 400,0 mi/.test(ex), "resultado = receita realizada − empenhada");
  assert(/Dependência de transferências\s*60,0%/.test(ex), "transferências ÷ correntes, sem somar a linha intraorçamentária: " + ex.slice(ex.indexOf("Dependência"), ex.indexOf("Dependência") + 80));
  assert(/Receita própria\s*30,0%/.test(ex) && /Pessoal e encargos\s*50,0%/.test(ex) && /Investimentos\s*8,0%/.test(ex));
  assert(/Saúde \(ASPS\)\s*21,4%/.test(ex) && /Educação \(MDE\)\s*19,8%/.test(ex), "pisos: saúde pela coluna liquidada");
  assert(d.querySelectorAll("#s-fisc .card .medidor.m-acima").length === 1, "educação abaixo de 25% sinalizada");
  assert(/49,7%/.test(fisc) && d.querySelector("#s-fisc .medidor.m-alerta"), "pessoal 49,7% na faixa de alerta");
  assert(!d.querySelector("#s-fisc .alerta"), "medidor não reutiliza a classe do banner de divergência");
  assert(/27,9%/.test(fisc), "DCL do último quadrimestre preenchido (não o vazio, não o exercício anterior)");
  assert(/2º quadrimestre/.test(fisc), "período do RGF");
  assert(CHAMADAS_RGF.length >= 2 && CHAMADAS_RGF.every(u => /nr_periodo=2/.test(u) && /co_poder=E/.test(u)), "RGF do Executivo, não o da Câmara");
  assert(/RCL ajustada: R\$\s?10\.000\.000\.000/.test(fisc), "RCL");
  // verificação das fontes: recolhida por padrão, resumo visível, escolha lembrada
  assert(d.querySelector("#verif-corpo").hidden, "detalhes técnicos recolhidos por padrão");
  assert(/Consistente em 3 fontes\. \d+ de 9 fontes responderam/.test(d.querySelector("#verif-resumo").textContent), d.querySelector("#verif-resumo").textContent);
  d.querySelector("#b-verif").click();
  assert(!d.querySelector("#verif-corpo").hidden && d.querySelector("#b-verif").textContent === "Ocultar detalhes");
  assert.strictEqual(w.localStorage.getItem("bm:verif"), "1", "preferência lembrada");
  d.querySelector("#b-verif").click();
  assert(d.querySelector("#verif-corpo").hidden && w.localStorage.getItem("bm:verif") === "0");

  // selo de completude e indicadores-chave
  if (BM().S.secoes.ind.status === "carregando") {
    const parcial = BM().completude();
    assert(parcial.grupos.find(x => x.nome === "Município (IBGE)").pendente, "fonte respondendo é pendente, não falta");
    assert(/Consultando as fontes/.test(d.querySelector("#s-selo").textContent));
  }
  await esperar(w, () => BM().S.secoes.ind.status === "ok", 4000, "indicadores");
  await sleep(30);
  const comp = BM().completude();
  const g = Object.fromEntries(comp.grupos.map(x => [x.nome, x]));
  assert.strictEqual(g["Eleição (TSE)"].ok, 8, "TSE completo, inclusive foto");
  assert.strictEqual(g["Município (IBGE)"].ok, 9, "IBGE: mortalidade (HTTP 500) falta");
  assert.strictEqual(JSON.stringify(g["Município (IBGE)"].faltando), '["mortalidade infantil"]');
  assert.strictEqual(g["Fiscal (Tesouro)"].ok, 4);
  assert.strictEqual(comp.ok, 24); assert.strictEqual(comp.total, 25);
  assert.strictEqual(d.querySelector("#s-selo .selo .n").textContent, "24", "número no centro do selo");
  assert(/de 25/.test(d.querySelector("#s-selo").textContent) && /mortalidade infantil/.test(d.querySelector("#s-selo details").textContent), "o que falta, nomeado");
  assert.strictEqual(d.querySelectorAll("#s-selo circle.cheio").length, 4, "um arco por fonte");
  const kpis = [...d.querySelectorAll("#s-kpi .kpi")].map(k => k.textContent.replace(/\s+/g, " ").trim());
  assert.strictEqual(kpis.length, 4);
  assert(/População\s*2\.428\.708\s*IBGE, 2022/.test(kpis[0]), kpis[0]);
  assert(/Capacidade de pagamento\s*B\s*CAPAG, set\/2026/.test(kpis[2]) && d.querySelector("#s-kpi .kpi:nth-child(3) .k-val.ok"), kpis[2]);
  assert(/49,7%/.test(kpis[3]) && d.querySelector("#s-kpi .kpi:nth-child(4) .k-val.alerta-k"), "pessoal em faixa de alerta na cor de alerta");
  assert(!/·/.test(d.querySelector("#doc").textContent), "sem separadores de template");

  // leituras alternativas
  const LP = BM().lerPessoal, LD = BM().lerDCL;
  assert(Math.abs(LP([{conta: "DESPESA TOTAL COM PESSOAL - DTP", coluna: "% SOBRE A RCL", valor: 0.4973}]).pct - 49.73) < 1e-9, "fração vira %");
  const calc = LP([{conta: "DESPESA TOTAL COM PESSOAL - DTP", coluna: "VALOR", valor: 50}, {conta: "RECEITA CORRENTE LÍQUIDA AJUSTADA", coluna: "VALOR", valor: 100}]);
  assert(calc.pct === 50 && /calculado/.test(calc.origem), "pessoal calculado sem a linha de %");
  assert(LP([{conta: "LIMITE MÁXIMO", coluna: "% SOBRE A RCL", valor: 54}]).reconhecido === false, "linha de limite não é a despesa");
  const dcl = LD([{conta: "DÍVIDA CONSOLIDADA LÍQUIDA (DCL) (III) = (I - II)", coluna: "Até o 1º Quadrimestre", valor: -20},
                  {conta: "RECEITA CORRENTE LÍQUIDA AJUSTADA PARA CÁLCULO DOS LIMITES DE ENDIVIDAMENTO", coluna: "Até o 1º Quadrimestre", valor: 200}]);
  assert(dcl.pct === -10, "DCL negativa calculada (caixa maior que a dívida)");
  assert(LD([{conta: "% DA DCL SOBRE A RCL", coluna: "Até o 2º Quadrimestre", valor: ""}]).reconhecido === false, "vazio não vira 0%");
  assert(LD([{conta: "% DA DCL SOBRE A RCL", coluna: "Até o 1º Quadrimestre", valor: 0}]).pct === 0, "zero de verdade continua zero");
  d.querySelector("#b-md").click(); await sleep(30);
  assert(/CAPAG: B/.test(w.__copiado) && /pessoal: 49,7%/.test(w.__copiado), "markdown com a seção fiscal");

  // ---------- IBGE com prefeito do mandato anterior: desatualizado, não divergente
  BM().limparCache();
  respostasExtras["29170/resultados/2304400"] = [{id: 29170, res: [{localidade: "2304400", res: {"2021": "PREFEITO ANTERIOR"}}]}];
  BM().abrirMunicipio("CE", "2304400");
  await esperar(w, () => /desatualizado/.test(d.querySelector("#s-mand").textContent), 3000, "IBGE antigo");
  assert(!/divergem/.test(d.querySelector("#s-mand").textContent), "IBGE antigo não gera alarme de divergência");
  assert(/Consistente em 3 fontes|Consistente/.test(d.querySelector("#s-mand").textContent) || /Wikidata/.test(d.querySelector("#s-mand").textContent));
  delete respostasExtras["29170/resultados/2304400"];
  BM().limparCache();

  // ---------- divergência + URL perigosa
  w.location.hash = "#/CE/2303709";
  await esperar(w, () => /divergem/.test(d.querySelector("#s-mand").textContent), 3000, "divergência");
  assert.strictEqual(d.querySelectorAll("#s-mand .tag.warn").length, 2, "IBGE e Wikidata divergem do TSE");
  assert(![...d.querySelectorAll("a")].some(a => /^javascript:/i.test(a.getAttribute("href"))), "URL javascript: bloqueada");
  assert(d.querySelector("#s-gov .portrait:not(img)"), "foto da Wikidata de outra pessoa não é usada");
  await esperar(w, () => /Nenhum RGF/.test(d.querySelector("#s-fisc")?.textContent || ""), 3000, "sem RGF");
  assert(/não calculada/.test(d.querySelector("#s-fisc").textContent) && /n\.d\./.test(d.querySelector("#s-fisc").textContent), "CAPAG não calculada explicada");
  assert(/IBGE Cidades e Wikidata indicam Beltrano Substituto/.test(d.querySelector("#s-gov .alerta").textContent), "alerta no card");
  assert(/Eleito em 2024, segundo o TSE/.test(d.querySelector("#s-gov .role").textContent), "rótulo do cargo rebaixado");

  // ---------- corrida: A lento, B rápido; A não pode sobrescrever B
  atrasos["/localidades/municipios/2304400"] = 400;
  BM().abrirMunicipio("CE", "2304400");
  await sleep(30);
  BM().abrirMunicipio("CE", "2303709");
  await sleep(700);
  assert.strictEqual(d.querySelector("#s-head h2").textContent, "Caucaia", "resposta atrasada descartada");
  delete atrasos["/localidades/municipios/2304400"];

  // ---------- exportação
  d.querySelector("#b-md").click(); await sleep(30);
  assert(/^# Briefing — Caucaia\/CE/.test(w.__copiado) && /DIVERGE/.test(w.__copiado), "markdown");
  d.querySelector("#b-print").click(); assert(w.__impresso, "imprimir");

  // ---------- busca acentuada e teclado
  const q = d.querySelector("#q");
  q.value = "fortaleza"; q.dispatchEvent(new w.Event("input"));
  assert.strictEqual(d.querySelectorAll("#munlist li").length, 1);
  q.value = "CAUCAIA"; q.dispatchEvent(new w.Event("input"));
  assert.strictEqual(d.querySelector("#munlist li").dataset.id, "2303709");

  // ---------- DF
  d.querySelector(".uf[data-uf=DF]").click();
  await esperar(w, () => /Distrito Federal não tem prefeito/.test(d.querySelector("#s-gov")?.textContent || ""), 3000, "DF");
  assert(/regime fiscal de estado/.test(d.querySelector("#s-fisc").textContent), "DF sem seção fiscal municipal");
  await sleep(100);
  assert(!BM().completude().grupos.some(x => /TSE|Tesouro/.test(x.nome)), "DF: selo sem grupos que não se aplicam");
  assert(/não se aplica ao DF/.test(d.querySelector("#s-kpi").textContent));
  await sleep(100);
  assert(/não se aplica/.test(d.querySelector("#s-fontes").textContent), "TSE não se aplica ao DF");
  assert(!chamadas.some(u => u.includes("data/prefeitos/df.json") && false));

  assert.strictEqual(alertas.length, 0, "nenhum script injetado executou");
  // ---------- modo incorporado (ficha da listagem)
  const dom2 = new JSDOM(html, {
    url: "http://localhost/index.html?embed=1#/CE/2304400", runScripts: "dangerously", pretendToBeVisual: true,
    beforeParse(w2) {
      w2.fetch = fetchMock; w2.matchMedia = () => ({matches: false});
      w2.HTMLElement.prototype.scrollIntoView = function () {};
    },
  });
  const w2 = dom2.window, d2 = w2.document;
  await esperar(w2, () => d2.querySelector("#s-gov h4"), 3000, "briefing incorporado");
  assert(d2.documentElement.classList.contains("embed") && w2.__BM__.EMBED, "classe do modo incorporado");
  const css2 = [...d2.querySelectorAll("style")].map(s => s.textContent).join("");
  assert(/html\.embed \.rail, html\.embed \.toolbar\{display:none\}/.test(css2), "barra lateral e ferramentas escondidas");
  const recebidas = [];
  w2.addEventListener("message", e => recebidas.push(e.data));
  d2.dispatchEvent(new w2.KeyboardEvent("keydown", {key: "Escape", bubbles: true}));
  d2.dispatchEvent(new w2.KeyboardEvent("keydown", {key: "ArrowRight", bubbles: true}));
  await sleep(30);
  assert.strictEqual(JSON.stringify(recebidas), JSON.stringify([{tipo: "bm-tecla", tecla: "Escape"}, {tipo: "bm-tecla", tecla: "ArrowRight"}]),
    "teclas repassadas à listagem");
  // só fecha depois de todas as fontes responderem (fechar antes deixa respostas chegando num documento descartado)
  await esperar(w2, () => Object.values(w2.__BM__.S.secoes).every(s => s.status !== "carregando"), 5000, "fontes do incorporado");
  await sleep(50);
  w2.close();

  console.log("TODOS OS TESTES DA INTERFACE PASSARAM (" + chamadas.length + " requisições simuladas)");
  w.close();
})().catch(e => { console.error("FALHOU:", e.message); process.exit(1); });
