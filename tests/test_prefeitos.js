// Teste funcional do prefeitos.html em jsdom. Uso: npm i jsdom@24 && node tests/test_prefeitos.js
"use strict";
const fs = require("fs");
const path = require("path");
const assert = require("assert");
const { JSDOM, VirtualConsole } = require("jsdom");

const html = fs.readFileSync(path.join(__dirname, "..", "prefeitos.html"), "utf8");
const sleep = (ms) => new Promise(r => setTimeout(r, ms));

function registro(i, extra = {}) {
  return Object.assign({
    codigo_ibge: String(2300000 + i), uf: "CE", regiao: "Nordeste", municipio: `Cidade ${String(i).padStart(3, "0")}`,
    prefeito: `Prefeito ${i}`, nome_completo: `Nome Completo ${i}`, partido: i % 3 ? "PSD" : "PT", partido_nome: null,
    federacao: null, genero: i % 4 ? "Masculino" : "Feminino", nascimento: "1970-01-01", idade: null, instrucao: null,
    ocupacao: null, eleicao_data: "2024-10-06", eleicao_ano: 2024, turno: 1, suplementar: false, votos: 1000,
    votos_pct: 50 + (i % 40), bens_declarados: i * 1000, vice: null, vice_partido: null, foto: null,
    briefing: `index.html#/CE/${2300000 + i}`, codigo_tse: "1",
    tem_prefeito_tse: true, populacao: 3000 * i, situacao_mandato: i % 10 ? "consistente" : "divergente", idhm: 0.7123,
  }, extra);
}
const ESPECIAIS = [
  registro(900, {codigo_ibge: "2700102", uf: "AL", municipio: "Água Branca", prefeito: "=CMD()", partido: "MDB",
    votos_pct: null, bens_declarados: null, nascimento: "1990-06-15", genero: "Feminino", suplementar: true, turno: 2,
    foto: "fotos/2700102.webp?v=9", vice: "José; \"Zé\""}),
  registro(901, {codigo_ibge: "3550308", uf: "SP", regiao: "Sudeste", municipio: "São Paulo", prefeito: "Ricardo",
    partido: "MDB", votos_pct: 59.35, bens_declarados: 1234.5, foto: "javascript:alert(1)"}),
];
const SEM_PREFEITO = registro(902, {codigo_ibge: "5300108", uf: "DF", regiao: "Centro-Oeste", municipio: "Brasília",
  prefeito: null, nome_completo: null, partido: null, genero: null, nascimento: null, votos_pct: null, bens_declarados: null,
  tem_prefeito_tse: false, situacao_mandato: "nao_se_aplica", populacao: 2817068});
const COLS_PUBLICADAS = ["codigo_ibge","uf","regiao","municipio","prefeito","nome_completo","partido","partido_nome","federacao","genero",
  "nascimento","idade","instrucao","ocupacao","eleicao_data","eleicao_ano","turno","suplementar","votos","votos_pct","bens_declarados",
  "vice","vice_partido","foto","briefing","codigo_tse","tem_prefeito_tse","populacao","idhm","situacao_mandato"];
const TODOS = {schema: 1, gerado_em: "2026-10-01T06:00:00+00:00", total: 123, dados_municipio: true, colunas: COLS_PUBLICADAS, registros: [
  ...Array.from({length: 120}, (_, i) => registro(i + 1)), ...ESPECIAIS, SEM_PREFEITO]};

function montar(url, respostas) {
  const chamadas = [], erros = [];
  const vc = new VirtualConsole();
  vc.on("jsdomError", (e) => erros.push(e.message));   // exceção não tratada na página reprova o teste
  const dom = new JSDOM(html, {
    url, runScripts: "dangerously", pretendToBeVisual: true, virtualConsole: vc,
    beforeParse(w) {
      w.HTMLElement.prototype.scrollIntoView = function () {};   // jsdom não implementa
      w.fetch = (u) => {
        chamadas.push(u);
        const r = respostas(u);
        return Promise.resolve({ok: r != null, status: r == null ? 404 : 200,
          headers: {get: () => "application/json"}, json: async () => JSON.parse(JSON.stringify(r))});
      };
      Object.defineProperty(w.navigator, "clipboard", {value: {writeText: async (t) => { w.__copiado = t; }}});
      w.alert = () => { w.__alerta = true; };
    },
  });
  return {dom, w: dom.window, d: dom.window.document, chamadas, erros};
}
async function esperar(fn, ms = 3000, rot = "condição") {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) { if (fn()) return; await sleep(15); }
  throw new Error("timeout: " + rot);
}
const linhas = (d) => [...d.querySelectorAll("#tbody tr")];
const txt = (d, s) => d.querySelector(s).textContent.replace(/\s+/g, " ").trim();

(async () => {
  // ================= caminho 1: consolidado
  let {w, d, chamadas, erros} = montar("http://app.local/prefeitos.html", u => u.includes("todos.json") ? TODOS : null);
  const P = () => w.__PREF__;
  await esperar(() => linhas(d).length === 50, 3000, "primeira página");
  assert(/123 de 123 municípios/.test(txt(d, "#summary")), txt(d, "#summary"));
  assert(/com dados do município/.test(txt(d, "#summary")));
  assert.strictEqual(chamadas.length, 1, "consolidado carregado em uma requisição");
  assert.strictEqual(linhas(d)[0].cells[0].textContent, "Água Branca", "ordem alfabética ignora acento");
  assert(/página 1 de 3/.test(txt(d, "#pager")));
  // idade recalculada hoje e nunca do arquivo
  assert(P().S.todos.every(r => r.nascimento ? Number.isInteger(r.idade) : r.idade === null));

  // segurança: foto com esquema perigoso não vira img; foto válida vira img local
  assert(![...d.querySelectorAll("img")].some(i => /javascript/i.test(i.getAttribute("src"))));
  assert.strictEqual(d.querySelector("#tbody img.thumb").getAttribute("src"), "./data/fotos/2700102.webp?v=9");

  // filtro por UF via chip
  d.querySelector('.chip[data-uf="SP"]').click();
  assert.strictEqual(linhas(d).length, 1);
  assert(/1 de 123/.test(txt(d, "#summary")) && /uf=SP/.test(w.location.hash));
  d.querySelector('.chip[data-uf="SP"]').click();

  // busca sem acento, com debounce
  const q = d.querySelector("#f-q");
  q.value = "agua bran"; q.dispatchEvent(new w.Event("input"));
  await esperar(() => linhas(d).length === 1, 1000, "busca");
  assert.strictEqual(linhas(d)[0].cells[0].textContent, "Água Branca");
  q.value = ""; q.dispatchEvent(new w.Event("input")); await sleep(200);

  // barra de partido filtra; contagem por partido não encolhe ao filtrar partido
  const nPT = P().S.porPartido.find(([p]) => p === "PT")[1];
  d.querySelector('.bar[data-pt="PT"]').click();
  assert.strictEqual(P().S.filtrados.length, nPT);
  assert(P().S.filtrados.every(r => r.partido === "PT"));
  assert(P().S.porPartido.length >= 3, "lista de partidos continua mostrando as opções");
  d.querySelector('.bar[data-pt="PT"]').click();

  // ordenação por votos: decrescente primeiro, nulos sempre no fim (nas duas direções)
  d.querySelector('[data-ord="votos_pct"]').click();
  let f = P().S.filtrados;
  assert(f[0].votos_pct >= f[1].votos_pct && f[f.length - 1].votos_pct === null, "desc com nulo no fim");
  d.querySelector('[data-ord="votos_pct"]').click();
  f = P().S.filtrados;
  assert(f[0].votos_pct <= f[1].votos_pct && f[f.length - 1].votos_pct === null, "asc com nulo no fim");
  assert.strictEqual(d.querySelector('th[aria-sort]').getAttribute("aria-sort"), "ascending");

  // filtros combinados: idade + suplementar + gênero
  const sel = (id, v) => { const e = d.querySelector(id); e.value = v; e.dispatchEvent(new w.Event("change")); };
  sel("#f-imax", "40");
  assert(P().S.filtrados.length === 1 && P().S.filtrados[0].municipio === "Água Branca", "idade máxima");
  sel("#f-imax", "");
  d.querySelector("#f-sup").click();
  assert.strictEqual(P().S.filtrados.length, 1, "só suplementares");
  sel("#f-gen", "Masculino");
  assert.strictEqual(P().S.filtrados.length, 0);
  assert(/Nenhum prefeito/.test(txt(d, "#tbody")));
  d.querySelector("#b-limpar").click();
  assert.strictEqual(P().S.filtrados.length, 123, "limpar volta tudo");
  assert.strictEqual(d.querySelector("#f-sup").checked, false);

  // linha sem prefeito: aparece, explica, não entra na contagem por partido nem no % de prefeitas
  d.querySelector('.chip[data-uf="DF"]').click();
  assert.strictEqual(linhas(d).length, 1);
  assert(/governado pelo governador do DF/.test(txt(d, "#tbody")), "DF explicado");
  assert(/0 partidos/.test(txt(d, "#summary")) && /0% prefeitas/.test(txt(d, "#summary")));
  d.querySelector('.chip[data-uf="DF"]').click();
  // porte e situação do mandato
  sel("#f-pop", "500000-");
  assert(P().S.filtrados.every(r => r.populacao >= 500000) && P().S.filtrados.length > 0, "porte");
  assert(/pop=500000-/.test(w.location.hash));
  sel("#f-pop", "");
  const nDiv = P().S.todos.filter(r => r.situacao_mandato === "divergente").length;
  d.querySelector("#b-div").click();
  assert.strictEqual(P().S.filtrados.length, nDiv, "atalho de divergentes");
  assert.strictEqual(d.querySelector("#f-sit").value, "divergente");
  assert(/fontes divergem/.test(txt(d, "#tbody")));
  d.querySelector("#b-limpar").click();
  // ordenar por população
  d.querySelector('[data-ord="populacao"]').click();
  assert.strictEqual(P().S.filtrados[0].municipio, "Cidade 001", "população crescente: a menor primeiro (3.000)");
  assert.strictEqual(P().S.filtrados[P().S.filtrados.length - 1].municipio, "Brasília", "a maior por último");
  d.querySelector('[data-ord="municipio"]').click();

  d.querySelector('[data-pg="1"]').click();
  assert(/51–100 de 123/.test(txt(d, "#pager")));

  // exportação CSV: BOM, separador, decimal BR, injeção neutralizada, aspas escapadas, booleano
  const csv = P().gerarCSV(P().S.filtrados);
  assert(csv.startsWith("\ufeffcodigo_ibge;uf;regiao;municipio;prefeito"), "cabeçalho");
  const linhaAgua = csv.split("\r\n").find(l => l.startsWith("2700102"));
  assert(linhaAgua.includes(";'=CMD();"), "fórmula neutralizada: " + linhaAgua);
  assert(linhaAgua.includes(';"José; ""Zé""";'), "aspas e ponto e vírgula escapados");
  assert(linhaAgua.includes(";sim;"), "booleano");
  const linhaSP = csv.split("\r\n").find(l => l.startsWith("3550308"));
  assert(linhaSP.includes(";59,35;") && linhaSP.includes(";1234,50;"), "decimal brasileiro");
  assert.strictEqual(csv.trim().split("\r\n").length, 124, "cabeçalho + 123");
  // JSON: só colunas do esquema, sem campos internos
  const js = JSON.parse(P().gerarJSON(P().S.filtrados));
  assert.strictEqual(js.schema, 1); assert.strictEqual(js.total, 123);
  assert(!("_busca" in js.registros[0]) && !("_mun" in js.registros[0]), "campos internos fora do export");
  assert.deepStrictEqual(Object.keys(js.registros[0]), js.colunas);
  assert.deepStrictEqual(js.colunas, COLS_PUBLICADAS, "exporta o esquema publicado, com as colunas novas");
  assert(csv.split("\r\n")[0].endsWith(";populacao;idhm;situacao_mandato"), "CSV com as colunas novas");
  assert(csv.includes(";0,712;"), "IDHM com 3 casas no CSV");
  assert.deepStrictEqual(erros, [], "sem exceções na página");
  w.close();

  // ================= estado na URL restaurado ao abrir link compartilhado
  ({w, d, erros} = montar("http://app.local/prefeitos.html#uf=AL,SP&partido=MDB&ord=bens_declarados:d", u => u.includes("todos.json") ? TODOS : null));
  await esperar(() => w.__PREF__ && w.__PREF__.S.todos.length, 3000, "carga com hash");
  await sleep(50);
  assert.strictEqual(w.__PREF__.S.filtrados.length, 2, "filtros do link aplicados");
  assert.strictEqual(w.__PREF__.S.filtrados[0].municipio, "São Paulo", "ordenação do link aplicada");
  assert.strictEqual(d.querySelector('.chip[data-uf="AL"]').getAttribute("aria-pressed"), "true");
  assert.deepStrictEqual(erros, []);
  w.close();

  // ================= caminho 2: sem consolidado, monta a partir dos arquivos por UF
  const uf = (sigla, muns) => ({uf: sigla, gerado_em: "2026-09-30T00:00:00Z", municipios: muns});
  const reg = (nm, urna, pt) => ({tse: {sg_ue: "1", nm_ue: nm}, eleicao: {ano: 2024, data: "2024-10-06", turno: 1, suplementar: false},
    prefeito: {nome: urna + " DA SILVA", nome_urna: urna, partido: pt, nascimento: "1975-05-05", genero: "MASCULINO", votos_pct: 55.5}, vice: null});
  ({w, d, chamadas, erros} = montar("http://app.local/prefeitos.html", u => {
    if (u.includes("todos.json")) return null;
    if (u.includes("servicodados.ibge.gov.br")) return [{id: 2304400, nome: "Fortaleza"}];
    if (u.endsWith("/ce.json")) return uf("CE", {"2304400": reg("FORTALEZA", "EVANDRO", "PT"), "2303709": reg("CAUCAIA", "NAUMI", "PSB")});
    if (u.endsWith("/pb.json")) return uf("PB", {"2507507": reg("JOÃO PESSOA", "CÍCERO", "PP")});
    return null;
  }));
  await esperar(() => linhas(d).length === 3, 3000, "fallback por UF");
  assert(/3 arquivos por UF|2 arquivos por UF/.test(txt(d, "#summary")), txt(d, "#summary"));
  const S2 = w.__PREF__.S;
  assert.strictEqual(S2.todos.find(r => r.codigo_ibge === "2304400").municipio, "Fortaleza", "nome oficial do IBGE");
  assert.strictEqual(S2.todos.find(r => r.codigo_ibge === "2507507").municipio, "João Pessoa", "fallback em caixa de título");
  assert.strictEqual(S2.todos.find(r => r.codigo_ibge === "2507507").nome_completo, "Cícero da Silva");
  assert(!chamadas.some(u => u.endsWith("/df.json")), "DF não é consultado");
  assert(!w.__alerta, "nenhum script injetado executou");
  assert.deepStrictEqual(erros, []);
  w.close();
  console.log("TODOS OS TESTES DA LISTAGEM PASSARAM");
})().catch(e => { console.error("FALHOU:", e.message); process.exit(1); });
