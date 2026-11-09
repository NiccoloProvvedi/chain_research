"use strict";
// Processo principale dell'app Ricerca a catena.
// Avvia il server (Python, solo 127.0.0.1) con una chiave segreta casuale, la consegna alla finestra
// tramite un cookie HttpOnly e apre l'interfaccia con tutte le protezioni di Electron attive.

const { app, BrowserWindow, dialog, session, shell } = require("electron");
const { spawn } = require("node:child_process");
const crypto = require("node:crypto");
const path = require("node:path");
const readline = require("node:readline");

const PROGETTO = path.resolve(__dirname, "..");
const PYTHON = path.join(PROGETTO, "venv", "bin", "python");
const MODO_PROVA = process.env.RICERCA_PROVA === "1"; // apre, fotografa la finestra in un file e chiude

let server = null;
let finestra = null;

function avviaServer(token) {
  return new Promise((resolve, reject) => {
    server = spawn(PYTHON, ["-m", "ricerca_catena.server"], {
      cwd: PROGETTO,
      env: { ...process.env, RICERCA_TOKEN: token },
      stdio: ["ignore", "pipe", "inherit"],
    });
    const scadenza = setTimeout(() => reject(new Error("Il server non si è avviato entro 30 secondi.")), 30000);
    server.once("error", (errore) => {
      clearTimeout(scadenza);
      reject(errore);
    });
    server.once("exit", (codice) => {
      clearTimeout(scadenza);
      reject(new Error(`Il server si è chiuso (codice ${codice}).`));
    });
    readline.createInterface({ input: server.stdout }).once("line", (riga) => {
      clearTimeout(scadenza);
      try {
        resolve(JSON.parse(riga).porta);
      } catch {
        reject(new Error("Risposta del server non valida."));
      }
    });
  });
}

function proteggi(origine) {
  // Nessun permesso del browser (fotocamera, microfono, notifiche, posizione...).
  session.defaultSession.setPermissionRequestHandler((_wc, _permesso, rispondi) => rispondi(false));
  session.defaultSession.setPermissionCheckHandler(() => false);
  app.on("web-contents-created", (_e, contenuti) => {
    // La finestra non può navigare altrove né aprire altre finestre.
    contenuti.on("will-navigate", (evento, url) => {
      if (!url.startsWith(origine + "/")) evento.preventDefault();
    });
    contenuti.setWindowOpenHandler(({ url }) => {
      if (url.startsWith("https://")) shell.openExternal(url); // link esterni nel browser di sistema
      return { action: "deny" };
    });
    contenuti.on("will-attach-webview", (evento) => evento.preventDefault());
  });
}

async function apri() {
  const token = crypto.randomBytes(32).toString("hex");
  let origine;
  try {
    origine = `http://127.0.0.1:${await avviaServer(token)}`;
  } catch (errore) {
    dialog.showErrorBox("Ricerca a catena", `${errore.message}\n\nControlla che ${PYTHON} esista.`);
    app.quit();
    return;
  }
  proteggi(origine);
  await session.defaultSession.cookies.set({
    url: origine, name: "token", value: token, httpOnly: true, sameSite: "strict",
  });
  finestra = new BrowserWindow({
    width: 1280,
    height: 860,
    minWidth: 900,
    minHeight: 600,
    title: "Ricerca a catena",
    autoHideMenuBar: true,
    show: !MODO_PROVA,
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      webSecurity: true,
      allowRunningInsecureContent: false,
      spellcheck: false,
      offscreen: MODO_PROVA,
    },
  });
  await finestra.loadURL(origine + "/");
  if (MODO_PROVA) await prova();
}

async function prova() {
  // Verifica automatica: l'interfaccia risponde, il server accetta la chiave; fotografia della finestra.
  const fs = require("node:fs");
  await new Promise((r) => setTimeout(r, 2500));
  const esito = await finestra.webContents.executeJavaScript(
    `fetch("/api/impostazioni").then(r => r.json()).then(s => JSON.stringify({ok: true, modelli: s.modelli}))`,
  );
  console.log("PROVA " + esito);
  const uscita = process.env.RICERCA_PROVA_IMMAGINE;
  if (uscita) {
    const immagine = await finestra.webContents.capturePage();
    fs.writeFileSync(uscita, immagine.toPNG());
  }
  app.quit();
}

function chiudiServer() {
  if (server && server.exitCode === null) {
    server.kill("SIGTERM");
    setTimeout(() => server?.exitCode === null && server.kill("SIGKILL"), 25000).unref();
  }
}

if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on("second-instance", () => {
    if (finestra) {
      if (finestra.isMinimized()) finestra.restore();
      finestra.focus();
    }
  });
  app.whenReady().then(apri);
  app.on("window-all-closed", () => app.quit());
  app.on("before-quit", chiudiServer);
  process.on("SIGINT", () => app.quit());
  process.on("SIGTERM", () => app.quit());
}
