"use strict";
// Interfaccia della Ricerca a catena: nuova indagine, elenco, indagine scelta con eventi in diretta, impostazioni.

const $ = (id) => document.getElementById(id);
const ETICHETTE = { in_attesa: "in attesa", in_corso: "in corso", finita: "finita", fermata: "fermata",
  interrotta: "interrotta", errore: "errore", risposta: "trovata", non_trovata: "non trovata" };
let scelta = null;      // id dell'indagine mostrata
let flusso = null;      // EventSource degli eventi
let ricarica = null;    // ricarica dello stato, al massimo una ogni secondo
let impostazioni = null;
let limitiMostrati = null; // limiti dell'indagine mostrata

function crea(tag, testo, classe) {
  const e = document.createElement(tag);
  if (testo !== undefined && testo !== null) e.textContent = testo;
  if (classe) e.className = classe;
  return e;
}

function avviso(testo, errore = false) {
  const box = $("avviso");
  box.textContent = testo;
  box.className = "avviso" + (errore ? " errore" : "");
  box.hidden = false;
  clearTimeout(avviso.timer);
  avviso.timer = setTimeout(() => { box.hidden = true; }, errore ? 8000 : 4000);
}

async function api(percorso, dati) {
  const opzioni = dati === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(dati) };
  const risposta = await fetch(percorso, opzioni);
  const json = await risposta.json().catch(() => ({}));
  if (!risposta.ok) throw new Error(json.errore || `Errore ${risposta.status}`);
  return json;
}

async function prova(azione, successo) {
  try {
    const esito = await azione();
    if (successo) avviso(successo);
    return esito;
  } catch (e) {
    avviso(e.message, true);
    return null;
  }
}

// Solo **grassetto** dal testo delle risposte: il resto resta testo (niente HTML dalle risposte).
function conGrassetto(testo) {
  const p = crea("div", null, "risposta");
  String(testo || "").split(/(\*\*[^*]+\*\*)/).forEach((pezzo) => {
    if (/^\*\*[^*]+\*\*$/.test(pezzo)) p.append(crea("strong", pezzo.slice(2, -2)));
    else if (pezzo) p.append(document.createTextNode(pezzo));
  });
  return p;
}

function riga(testo, etichetta) {
  const m = new RegExp(`\\*\\*${etichetta}:?\\*\\*:?\\s*(.+)`).exec(testo || "");
  return m ? m[1].trim() : "";
}

function dollari(n) { return `${(n || 0).toFixed(2)} $`; }
function durata(secondi) {
  const m = Math.round((secondi || 0) / 60);
  return m >= 60 ? `${Math.floor(m / 60)} h ${m % 60} min` : `${m} min`;
}
function data(iso) {
  try { return new Date(iso).toLocaleString("it-IT", { dateStyle: "medium", timeStyle: "short" }); }
  catch { return iso; }
}

// ---------- nuova indagine ----------
function aggiornaLimiti() {
  const senza = $("senza-limiti").checked;
  $("budget").disabled = senza;
  $("minuti").disabled = senza;
}
$("senza-limiti").addEventListener("change", aggiornaLimiti);

const AVVISO_SENZA_LIMITI = "Senza limiti l'indagine può arrivare a circa 160 domande e durare molte ore. Continuare?";

$("nuova").addEventListener("submit", async (e) => {
  e.preventDefault();
  if ($("senza-limiti").checked && !window.confirm(AVVISO_SENZA_LIMITI)) return;
  const esito = await prova(() => api("/api/indagini", {
    argomento: $("argomento").value, modello_regia: $("modello").value, budget_usd: $("budget").value,
    max_minuti: $("minuti").value, senza_limiti: $("senza-limiti").checked,
  }), "Indagine avviata.");
  if (esito) {
    $("argomento").value = "";
    await caricaElenco();
    mostra(esito.id);
  }
});

// ---------- elenco ----------
async function caricaElenco() {
  const voci = await prova(() => api("/api/indagini"));
  if (!voci) return;
  $("elenco-vuoto").hidden = voci.length > 0;
  $("elenco").replaceChildren(...voci.map((v) => {
    const li = crea("li");
    li.tabIndex = 0;
    li.setAttribute("aria-current", String(v.id === scelta));
    li.append(crea("span", v.titolo || v.argomento, "titolo"));
    const meta = crea("span", null, "meta");
    meta.append(crea("span", ETICHETTE[v.stato] || v.stato, `chip ${v.stato}`), crea("span", data(v.creata)),
      crea("span", `${v.trovate}/${v.domande} risposte`), crea("span", dollari(v.speso_usd)));
    li.append(meta);
    li.addEventListener("click", () => mostra(v.id));
    li.addEventListener("keydown", (ev) => { if (ev.key === "Enter") mostra(v.id); });
    return li;
  }));
}

// ---------- indagine scelta ----------
function mostra(id) {
  scelta = id;
  limitiMostrati = null;              // quelli dell'indagine di prima non valgono
  $("b-riprendi").hidden = true;      // si mostra quando lo stato di questa indagine è arrivato
  document.querySelectorAll("#elenco li").forEach((li) => li.setAttribute("aria-current", "false"));
  $("nessuna").hidden = true;
  $("vista").hidden = false;
  $("v-attivita").replaceChildren();
  if (flusso) flusso.close();
  carica();
  flusso = new EventSource(`/api/indagini/${encodeURIComponent(id)}/eventi`);
  flusso.onmessage = (m) => {
    let ev;
    try { ev = JSON.parse(m.data); } catch { return; }
    attivita(ev);
    if (["piano", "domanda", "valutazione", "fine", "inizio", "fase"].includes(ev.tipo)) caricaPresto();
    if (ev.tipo === "fine") caricaElenco();
  };
  // Si chiude solo su richiesta del server: nel flusso ci sono anche i «fine» delle corse passate.
  flusso.addEventListener("chiuso", (m) => { m.target.close(); });
}

function caricaPresto() {
  if (ricarica) return;
  ricarica = setTimeout(() => { ricarica = null; carica(); }, 800);
}

function attivita(ev) {
  const testi = {
    inizio: () => `Indagine avviata (regia: ${ev.modello_regia})`,
    fase: () => ev.testo,
    piano: () => `Piano pronto: ${ev.piano.sezioni.length} sezioni, ${ev.domande.length} domande`,
    domanda: () => ev.stato === "in_corso" ? `${ev.id} alla catena: ${ev.domanda}` :
      `${ev.id} ${ETICHETTE[ev.stato] || ev.stato}${ev.risposta ? ": " + ev.risposta.replace(/\*\*/g, "") : ""}`,
    domanda_fase: () => `${ev.id} · ${ev.testo}`,
    domanda_pagina: () => `${ev.id} · letta: ${ev.titolo || ev.url}`,
    valutazione: () => `Valutazione del giro ${ev.giro}: ${ev.conoscenza_solida ? "conoscenza solida" : `${ev.domande.length} domande nuove`} — ${ev.motivo}`,
    documento: () => ev.testo,
    fine: () => `Fine (${ETICHETTE[ev.stato] || ev.stato}): ${ev.motivo || ""}`,
  };
  const testo = testi[ev.tipo]?.();
  if (!testo) return;
  const li = crea("li", `${(ev.quando || "").slice(11, 16)}  ${testo}`);
  const lista = $("v-attivita");
  lista.prepend(li);
  while (lista.children.length > 300) lista.lastChild.remove();
}

async function carica() {
  if (!scelta) return;
  const id = scelta;
  const s = await prova(() => api(`/api/indagini/${encodeURIComponent(id)}`));
  if (!s || id !== scelta) return;
  const domande = s.domande || [];
  const trovate = domande.filter((d) => d.stato === "risposta").length;
  const speso = (s.speso_regia_usd || 0) + domande.reduce((t, d) => t + (d.costo_usd || 0), 0);
  $("v-data").textContent = `${data(s.creata)} · regia ${s.modello_regia}`;
  $("v-titolo").textContent = s.piano?.titolo || "Preparo il piano…";
  $("v-argomento").textContent = s.argomento;
  $("v-stato").textContent = ETICHETTE[s.stato] || s.stato;
  $("v-stato").className = `chip ${s.stato}`;
  const limite = s.limiti.senza_limiti ? "nessun limite" : `${dollari(s.limiti.budget_usd)} · ${s.limiti.max_minuti} min`;
  $("v-numeri").replaceChildren(...[[s.giro || 0, "giro"], [`${trovate}/${domande.length}`, "risposte trovate"],
    [dollari(speso), "spesa di Claude"], [durata(s.secondi), "durata"], [limite, "limiti"]].map(([v, t]) => {
    const d = crea("div");
    d.append(crea("b", String(v)), crea("span", t));
    return d;
  }));
  $("v-fase").textContent = s.stato === "in_corso" ? s.fase : (s.motivo_fine ? `Conclusa: ${s.motivo_fine}` : "");
  const attiva = s.stato === "in_corso";
  $("b-apri").hidden = !s.documento?.pdf;
  $("b-ferma").hidden = !attiva;
  $("b-riprendi").hidden = attiva || s.stato === "finita";
  $("b-documento").hidden = attiva || !domande.some((d) => d.stato === "risposta");
  $("b-pulisci").hidden = attiva;
  limitiMostrati = s.limiti;
  sezioni(s);
  elencoDomande(s);
}

function sezioni(s) {
  const valutazioni = s.valutazioni || [];
  const ultima = valutazioni[valutazioni.length - 1];
  const coperture = Object.fromEntries((ultima?.sezioni || []).map((x) => [x.id, x]));
  $("v-sezioni-box").hidden = !s.piano;
  if (!s.piano) return;
  $("v-sezioni").replaceChildren(...s.piano.sezioni.map((x) => {
    const li = crea("li");
    const c = coperture[x.id];
    const barra = crea("div", null, "copertura");
    barra.setAttribute("aria-label", c ? `copertura ${c.copertura} su 3` : "non ancora valutata");
    for (let i = 0; i < 3; i++) barra.append(crea("i", null, c && i < c.copertura ? "piena" : ""));
    li.append(crea("span", x.titolo), barra);
    if (c?.nota) li.append(crea("span", c.nota, "nota-sezione"));
    return li;
  }));
  $("v-valutazione").textContent = ultima ? `Giro ${ultima.giro}: ${ultima.motivo}` : "";
}

function elencoDomande(s) {
  const perGiro = {};
  for (const d of s.domande || []) (perGiro[d.giro] ||= []).push(d);
  const titoli = Object.fromEntries((s.piano?.sezioni || []).map((x) => [x.id, x.titolo]));
  $("v-domande").replaceChildren(...Object.keys(perGiro).sort((a, b) => b - a).map((giro) => {
    const box = crea("div", null, "giro");
    box.append(crea("h3", `Giro ${giro}`));
    for (const d of perGiro[giro]) {
      const r = crea("div", null, "domanda");
      r.append(crea("span", d.domanda, "testo"), crea("span", ETICHETTE[d.stato] || d.stato, `chip ${d.stato}`));
      const risposta = riga(d.risposta, "Risposta");
      if (risposta) r.append(conGrassetto(risposta));
      const info = [titoli[d.sezione], d.costo_usd ? dollari(d.costo_usd) : "", d.secondi ? durata(d.secondi) : "",
        riga(d.risposta, "Fiducia") ? `fiducia ${riga(d.risposta, "Fiducia").split(/[,.]/)[0]}` : "",
        d.errore || ""].filter(Boolean).join(" · ");
      if (info) r.append(crea("div", info, "info"));
      box.append(r);
    }
    return box;
  }));
}

for (const [bottone, azione, messaggio] of [["b-ferma", "ferma", "Fermo l'indagine…"],
  ["b-documento", "documento", "Scrivo il documento…"], ["b-apri", "apri", null], ["b-cartella", "cartella", null],
  ["b-pulisci", "pulisci", "Pezzi di pagina tolti."]]) {
  $(bottone).addEventListener("click", async () => {
    if (!scelta) return;
    if (azione === "pulisci" && !window.confirm("Togliere i pezzi di pagina letti da questa indagine?")) return;
    const esito = await prova(() => api(`/api/indagini/${encodeURIComponent(scelta)}/${azione}`, {}), messaggio);
    if (esito && azione === "documento") mostra(scelta);  // riapre il flusso degli eventi
    caricaPresto();
    caricaElenco();
  });
}

$("b-riprendi").addEventListener("click", () => {
  if (!scelta || !limitiMostrati) return;
  $("r-budget").value = limitiMostrati.budget_usd;
  $("r-minuti").value = limitiMostrati.max_minuti;
  $("r-senza-limiti").checked = limitiMostrati.senza_limiti;
  $("riprendi").showModal();
});

$("form-riprendi").addEventListener("submit", async (e) => {
  if (e.submitter?.value !== "riprendi") return;
  e.preventDefault();
  if ($("r-senza-limiti").checked && !window.confirm(AVVISO_SENZA_LIMITI)) return;
  const esito = await prova(() => api(`/api/indagini/${encodeURIComponent(scelta)}/riprendi`, {
    budget_usd: $("r-budget").value, max_minuti: $("r-minuti").value, senza_limiti: $("r-senza-limiti").checked,
  }), "Indagine ripresa.");
  if (esito) {
    $("riprendi").close();
    mostra(scelta);  // riapre il flusso degli eventi
    caricaElenco();
  }
});

// ---------- impostazioni ----------
async function caricaImpostazioni() {
  impostazioni = await prova(() => api("/api/impostazioni"));
  if (!impostazioni) return;
  $("modello").value = impostazioni.modello_regia;
  $("budget").value = impostazioni.budget_usd;
  $("minuti").value = impostazioni.max_minuti;
  $("senza-limiti").checked = impostazioni.senza_limiti;
  aggiornaLimiti();
}

$("apri-impostazioni").addEventListener("click", async () => {
  await caricaImpostazioni();
  if (!impostazioni) return;
  const w = impostazioni.wikipedia;
  $("i-modello").value = impostazioni.modello_regia;
  $("i-budget").value = impostazioni.budget_usd;
  $("i-minuti").value = impostazioni.max_minuti;
  $("i-senza-limiti").checked = impostazioni.senza_limiti;
  $("i-parallelo").value = impostazioni.domande_in_parallelo;
  $("i-modello-catena").value = impostazioni.modello_catena;
  $("i-budget-domanda").value = impostazioni.budget_domanda_usd;
  $("i-pagine-domanda").value = impostazioni.pagine_domanda;
  $("i-minuti-domanda").value = impostazioni.minuti_domanda;
  $("i-wiki-attivo").checked = w.attivo;
  $("i-wiki-contatto").value = w.contatto;
  $("i-wiki-contatto").placeholder = w.origine === "account" ? w.in_uso : "";
  $("impostazioni").showModal();
});

$("form-impostazioni").addEventListener("submit", async (e) => {
  if (e.submitter?.value !== "salva") return;
  e.preventDefault();
  const esito = await prova(() => api("/api/impostazioni", {
    modello_regia: $("i-modello").value, budget_usd: $("i-budget").value, max_minuti: $("i-minuti").value,
    senza_limiti: $("i-senza-limiti").checked, domande_in_parallelo: $("i-parallelo").value,
    modello_catena: $("i-modello-catena").value, budget_domanda_usd: $("i-budget-domanda").value,
    pagine_domanda: $("i-pagine-domanda").value, minuti_domanda: $("i-minuti-domanda").value,
    wikipedia: { attivo: $("i-wiki-attivo").checked, contatto: $("i-wiki-contatto").value },
  }), "Impostazioni salvate.");
  if (esito) {
    $("impostazioni").close();
    caricaImpostazioni();
  }
});

caricaImpostazioni();
caricaElenco().then(() => {
  const prima = document.querySelector("#elenco li");
  if (prima) prima.click();
});
setInterval(caricaElenco, 15000);
