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

  // ================= ficha em modal
  const CAPAG_CE = {municipios: {"2300001": [{posicao: "2026-06-01", ano_base: 2025, capag: "C"},
    {posicao: "2026-09-01", ano_base: 2025, capag: "B", endividamento: 0.352, nota_endividamento: "A"}]}};
  const comCapag = (u) => u.includes("todos.json") ? TODOS : u.includes("capag/ce.json") ? CAPAG_CE : null;
  ({w, d, erros} = montar("http://app.local/prefeitos.html", comCapag));
  await esperar(() => linhas(d).length === 50, 3000, "lista para a ficha");
  const fundo = () => d.querySelector("#ficha-fundo");
  assert(fundo().hidden, "modal começa fechado");
  // abre pela linha (clique no nome do município)
  const ancora = d.querySelector('#tbody tr[data-cod="2300001"] a') || linhas(d).find(l => l.dataset.cod === "2300001")?.querySelector("a");
  ancora.focus(); ancora.click();
  assert(!fundo().hidden, "abre ao clicar na linha");
  assert.strictEqual(d.querySelector("#f-nome").textContent, "Prefeito 1");
  assert(/ficha=2300001/.test(w.location.hash), "link próprio da ficha");
  assert.strictEqual(d.body.style.overflow, "hidden", "rolagem da página travada");
  assert.strictEqual(d.activeElement, d.querySelector("#ficha"), "foco vai para o modal");
  await esperar(() => /Posição de set\/2026/.test(d.querySelector("#ficha").textContent), 2000, "CAPAG na ficha");
  const fic = d.querySelector("#ficha").textContent.replace(/\s+/g, " ");
  assert(/jun\/2026 C/.test(fic) && /35,2%/.test(fic) && /elegível/.test(fic), "CAPAG e trajetória");
  assert(/Mandato consistente/.test(fic) && /3\.000/.test(fic), "situação do mandato e população");
  assert(/não informado/.test(fic), "campo ausente dito como ausente");
  const pos1 = d.querySelector("#ficha .pos").textContent;
  // setas navegam pela ordem filtrada
  const idx0 = P().S.filtrados.findIndex(r => r.codigo_ibge === "2300001");
  d.dispatchEvent(new w.KeyboardEvent("keydown", {key: "ArrowRight", bubbles: true}));
  assert.strictEqual(P().S.ficha.r.codigo_ibge, P().S.filtrados[idx0 + 1].codigo_ibge, "seta direita = próximo");
  assert.notStrictEqual(d.querySelector("#ficha .pos").textContent, pos1);
  d.dispatchEvent(new w.KeyboardEvent("keydown", {key: "ArrowLeft", bubbles: true}));
  assert.strictEqual(P().S.ficha.r.codigo_ibge, "2300001");
  // navegar além da página leva a tabela junto
  P().abrirFicha(P().S.filtrados[49].codigo_ibge);
  d.dispatchEvent(new w.KeyboardEvent("keydown", {key: "ArrowRight", bubbles: true}));
  assert(/51–100/.test(txt(d, "#pager")), "a tabela acompanha a ficha para a página 2");
  // primeiro item: "Anterior" desabilitado
  P().abrirFicha(P().S.filtrados[0].codigo_ibge);
  assert(d.querySelector('#ficha [data-nav="-1"]').disabled);
  // foco preso: Tab no último elemento volta ao primeiro
  const foc = P().focaveis();
  assert.strictEqual(foc[0].tagName, "BUTTON", "primeiro focável é o da barra (ordem de documento)");
  assert.strictEqual(foc[foc.length - 1].textContent, "Abrir briefing completo", "último é a ação principal");
  foc[foc.length - 1].focus();
  d.dispatchEvent(new w.KeyboardEvent("keydown", {key: "Tab", bubbles: true}));
  assert.strictEqual(d.activeElement, foc[0], "Tab circula dentro do modal");
  // Esc fecha e devolve o foco a quem abriu
  d.dispatchEvent(new w.KeyboardEvent("keydown", {key: "Escape", bubbles: true}));
  assert(fundo().hidden && !/ficha=/.test(w.location.hash) && d.body.style.overflow === "", "Esc fecha e limpa o link");
  const ultimo = d.querySelector(`#tbody tr[data-cod="${P().S.filtrados[0].codigo_ibge}"] a`);
  assert.strictEqual(d.activeElement, ultimo, "tabela redesenhada: foco volta ao município visto por último");
  // caso simples: abrir e fechar sem navegar devolve o foco ao próprio link
  const a2 = d.querySelector("#tbody tr[data-cod] a"); a2.focus(); a2.click();
  d.dispatchEvent(new w.KeyboardEvent("keydown", {key: "Escape", bubbles: true}));
  assert.strictEqual(d.activeElement, a2, "foco volta ao link que abriu a ficha");
  // Ctrl-clique mantém o comportamento de link (nova aba), sem modal
  const ev = new w.MouseEvent("click", {bubbles: true, cancelable: true, ctrlKey: true});
  a2.dispatchEvent(ev);
  assert(fundo().hidden, "Ctrl-clique não abre o modal");
  // completude da ficha: DF sem grupos que não se aplicam
  const gDF = P().gruposFicha(P().S.todos.find(r => r.uf === "DF"), undefined);
  assert(!gDF.some(g => /TSE|Tesouro/.test(g.nome)), "DF sem eleição e sem fiscal no selo");
  const g1 = P().gruposFicha(P().S.todos.find(r => r.codigo_ibge === "2300001"), {ultima: {capag: "B"}});
  assert.strictEqual(g1.find(g => g.nome === "Fiscal (Tesouro)").ok, 1);
  assert(g1.find(g => g.nome === "Fiscal (Tesouro)").pendente === false);
  assert.deepStrictEqual(erros, [], "sem exceções na página");
  w.close();

  // ================= produção atual: consolidado SEM dados do município (só as 26 colunas base)
  const BASE26 = COLS_PUBLICADAS.slice(0, 26);
  const TODOS_BASE = {schema: 1, gerado_em: TODOS.gerado_em, total: 5, colunas: BASE26,
    registros: TODOS.registros.slice(0, 5).map(r => Object.fromEntries(BASE26.map(k => [k, r[k]])))};
  const CAPAG_OBS = {municipios: {"2300001": [{posicao: "2026-09-01", ano_base: 2025, capag: "B", qualidade_informacao: "A",
    origem_nota: "ICF", observacao: "Nota rebaixada em razão do ICF"}]}};
  ({w, d, erros} = montar("http://app.local/prefeitos.html", u => u.includes("todos.json") ? TODOS_BASE : u.includes("capag/ce.json") ? CAPAG_OBS : null));
  await esperar(() => linhas(d).length === 5, 3000, "lista sem dados do município");
  assert(/Clique em um município para abrir a ficha/.test(d.querySelector("#dica").textContent), "dica de uso visível");
  linhas(d).find(l => l.dataset.cod === "2300001").querySelector("td:nth-child(3)").click();   // clique fora do link também abre
  assert(!d.querySelector("#ficha-fundo").hidden, "ficha abre com o consolidado de produção");
  await esperar(() => /Origem da nota final: ICF/.test(d.querySelector("#ficha").textContent), 2000, "CAPAG completa");
  const fb = d.querySelector("#ficha").textContent.replace(/\s+/g, " ");
  assert(/Nota rebaixada em razão do ICF/.test(fb) && /Qualidade da informação \(ICF\)\s*A/.test(fb), "ICF e observação: " + fb.slice(fb.indexOf("Liquidez"), fb.indexOf("Liquidez") + 200));
  assert((fb.match(/não informado/g) || []).length >= 6, "campos do IBGE ausentes ditos como ausentes");
  const gb = P().gruposFicha(P().S.ficha.r, P().S.ficha.capag);
  assert.strictEqual(gb.find(g => g.nome === "Município (IBGE)").ok, 0, "selo mostra o IBGE vazio");
  // PDF da ficha: imprime só o modal e restaura a página depois
  let imprimiu = null;
  w.print = () => { imprimiu = {classe: d.body.classList.contains("imprimindo-ficha"), titulo: d.title}; w.dispatchEvent(new w.Event("afterprint")); };
  d.querySelector("#ficha [data-pdf]").click();
  assert(imprimiu && imprimiu.classe, "modo de impressão da ficha ativo durante o print");
  assert(/^Ficha Cidade 001 CE$/.test(imprimiu.titulo), "nome sugerido do PDF: " + imprimiu.titulo);
  assert(!d.body.classList.contains("imprimindo-ficha") && !/^Ficha/.test(d.title), "página restaurada após imprimir");
  assert.deepStrictEqual(erros, [], "sem exceções com o formato de produção");
  w.close();

  // ================= link direto para a ficha + escape de HTML
  const TODOS_XSS = JSON.parse(JSON.stringify(TODOS));
  TODOS_XSS.registros[0].prefeito = "<img src=x onerror=alert(1)>";
  ({w, d, erros} = montar("http://app.local/prefeitos.html#ficha=2300001", u => u.includes("todos.json") ? TODOS_XSS : null));
  await esperar(() => d.querySelector("#ficha-fundo") && !d.querySelector("#ficha-fundo").hidden, 3000, "ficha pelo link");
  assert(!d.querySelector("#ficha img[src='x']") && /&lt;img/.test(d.querySelector("#f-nome").innerHTML), "nome escapado");
  await esperar(() => /CAPAG indisponível/.test(d.querySelector("#ficha").textContent), 2000, "erro de CAPAG explicado");
  assert(!w.__alerta);
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
