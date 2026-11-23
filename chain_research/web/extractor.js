// Estrae dall'HTML il testo principale e completo della pagina (senza menu, pubblicità, piè di pagina)
// e i collegamenti interni al contenuto. Restituisce {titolo, testo, collegamenti}.
(() => {
  const candidati = ["#mw-content-text .mw-parser-output", ".mw-parser-output", "article", "main", "[role=main]",
    "#content", ".post-content", ".entry-content", ".article-body", "#main"];
  let radice = null;
  for (const s of candidati) {
    const e = document.querySelector(s);
    if (e && (e.innerText || "").trim().length > 400) { radice = e; break; }
  }
  if (!radice) {
    // Il blocco con più testo in paragrafi.
    let migliore = document.body, punti = 0;
    for (const e of document.querySelectorAll("div, section")) {
      const p = [...e.querySelectorAll(":scope > p")].reduce((n, x) => n + (x.innerText || "").length, 0);
      if (p > punti) { punti = p; migliore = e; }
    }
    radice = migliore;
  }
  const copia = radice.cloneNode(true);
  const via = "script,style,noscript,template,nav,header,footer,aside,form,iframe,svg,button,figure figcaption," +
    "[role=navigation],[role=banner],[role=contentinfo],[aria-hidden=true],.navbox,.vertical-navbox,.infobox-nav," +
    ".mw-editsection,.reference,.references,.reflist,sup.reference,.mw-references-wrap,.toc,#toc,.catlinks," +
    ".printfooter,.noprint,.ad,.ads,.advert,[class*=advert],[id*=advert],.cookie,[class*=cookie],.share,.social," +
    ".related,.comments,#comments,.wds-global-footer,.page-footer,.fandom-community-header";
  copia.querySelectorAll(via).forEach((e) => e.remove());
  // Titoli e voci di elenco su righe proprie, per conservare la struttura.
  copia.querySelectorAll("h1,h2,h3,h4").forEach((h) => { h.textContent = "\n## " + h.textContent.trim() + "\n"; });
  copia.querySelectorAll("li").forEach((li) => { li.textContent = "\n- " + li.textContent.trim(); });
  document.body.appendChild(copia);
  copia.style.position = "absolute"; copia.style.left = "-99999px";
  let testo = (copia.innerText || copia.textContent || "");
  copia.remove();
  testo = testo.replace(/\[\d+\]/g, "").replace(/[ \t]+/g, " ").replace(/\n{3,}/g, "\n\n").trim();
  // Con il testo preso dall'archivio della wiki la pagina è "about:blank" con <base>: si usa l'indirizzo di base.
  const indirizzo = new URL(document.baseURI);
  const base = indirizzo.origin;
  const visti = new Set();
  const collegamenti = [];
  for (const a of radice.querySelectorAll("a[href]")) {
    const url = a.href.split("#")[0];
    const t = (a.innerText || "").trim();
    if (!url.startsWith(base) || url === indirizzo.href.split("#")[0] || !t || t.length < 3 || visti.has(url)) continue;
    if (/[?&](action|oldid|diff)=|\/(Special|Speciale|File|Category|Categoria|Template|Help|Aiuto|User|Utente|Talk):/i.test(url)) continue;
    visti.add(url);
    collegamenti.push({ url, testo: t.slice(0, 80) });
    if (collegamenti.length >= 150) break;
  }
  const verifica = /just a moment|ci siamo quasi|attention required|verifica di sicurezza|security (check|verification)|checking your browser/i
    .test(document.title + " " + (document.body.innerText || "").slice(0, 600));
  return { titolo: document.title, testo: testo.slice(0, 200000), collegamenti, verifica_antibot: verifica };
})()
