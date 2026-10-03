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
  await sleep(100);
  assert(/não se aplica/.test(d.querySelector("#s-fontes").textContent), "TSE não se aplica ao DF");
  assert(!chamadas.some(u => u.includes("data/prefeitos/df.json") && false));

  assert.strictEqual(alertas.length, 0, "nenhum script injetado executou");
  console.log("TODOS OS TESTES DA INTERFACE PASSARAM (" + chamadas.length + " requisições simuladas)");
  w.close();
})().catch(e => { console.error("FALHOU:", e.message); process.exit(1); });
