"use strict";
// Main process of the Chain Research app.
// Starts the server (Python, 127.0.0.1 only) with a random secret key, hands it to the window
// through an HttpOnly cookie and opens the interface with all of Electron's protections on.

const { app, BrowserWindow, dialog, session, shell } = require("electron");
const { spawn } = require("node:child_process");
const crypto = require("node:crypto");
const path = require("node:path");
const readline = require("node:readline");

const PROJECT = path.resolve(__dirname, "..");
const PYTHON = path.join(PROJECT, "venv", "bin", "python");
const TEST_MODE = process.env.CHAIN_TEST === "1"; // opens, saves a screenshot of the window to a file and quits

let server = null;
let appWindow = null;

function startServer(token) {
  return new Promise((resolve, reject) => {
    server = spawn(PYTHON, ["-m", "chain_research.server"], {
      cwd: PROJECT,
      env: { ...process.env, CHAIN_TOKEN: token },
      stdio: ["ignore", "pipe", "inherit"],
    });
    const timeout = setTimeout(() => reject(new Error("The server did not start within 30 seconds.")), 30000);
    server.once("error", (error) => {
      clearTimeout(timeout);
      reject(error);
    });
    server.once("exit", (code) => {
      clearTimeout(timeout);
      reject(new Error(`The server exited (code ${code}).`));
    });
    readline.createInterface({ input: server.stdout }).once("line", (line) => {
      clearTimeout(timeout);
      try {
        resolve(JSON.parse(line).port);
      } catch {
        reject(new Error("Invalid response from the server."));
      }
    });
  });
}

function protect(origin) {
  // No browser permissions (camera, microphone, notifications, location...).
  session.defaultSession.setPermissionRequestHandler((_wc, _permission, respond) => respond(false));
  session.defaultSession.setPermissionCheckHandler(() => false);
  app.on("web-contents-created", (_e, contents) => {
    // The window cannot navigate elsewhere or open other windows.
    contents.on("will-navigate", (event, url) => {
      if (!url.startsWith(origin + "/")) event.preventDefault();
    });
    contents.setWindowOpenHandler(({ url }) => {
      if (url.startsWith("https://")) shell.openExternal(url); // external links in the system browser
      return { action: "deny" };
    });
    contents.on("will-attach-webview", (event) => event.preventDefault());
  });
}

async function openApp() {
  const token = crypto.randomBytes(32).toString("hex");
  let origin;
  try {
    origin = `http://127.0.0.1:${await startServer(token)}`;
  } catch (error) {
    dialog.showErrorBox("Chain Research", `${error.message}\n\nCheck that ${PYTHON} exists.`);
    app.quit();
    return;
  }
  protect(origin);
  await session.defaultSession.cookies.set({
    url: origin, name: "token", value: token, httpOnly: true, sameSite: "strict",
  });
  appWindow = new BrowserWindow({
    width: 1280,
    height: 860,
    minWidth: 900,
    minHeight: 600,
    title: "Chain Research",
    autoHideMenuBar: true,
    show: !TEST_MODE,
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      webSecurity: true,
      allowRunningInsecureContent: false,
      spellcheck: false,
      offscreen: TEST_MODE,
    },
  });
  await appWindow.loadURL(origin + "/");
  if (TEST_MODE) await selfTest();
}

async function selfTest() {
  // Automatic check: the interface answers, the server accepts the key; screenshot of the window.
  const fs = require("node:fs");
  await new Promise((r) => setTimeout(r, 2500));
  const outcome = await appWindow.webContents.executeJavaScript(
    `fetch("/api/settings").then(r => r.json()).then(s => JSON.stringify({ok: true, models: s.models}))`,
  );
  console.log("TEST " + outcome);
  const output = process.env.CHAIN_TEST_IMAGE;
  if (output) {
    const image = await appWindow.webContents.capturePage();
    fs.writeFileSync(output, image.toPNG());
  }
  app.quit();
}

function stopServer() {
  if (server && server.exitCode === null) {
    server.kill("SIGTERM");
    setTimeout(() => server?.exitCode === null && server.kill("SIGKILL"), 25000).unref();
  }
}

if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on("second-instance", () => {
    if (appWindow) {
      if (appWindow.isMinimized()) appWindow.restore();
      appWindow.focus();
    }
  });
  app.whenReady().then(openApp);
  app.on("window-all-closed", () => app.quit());
  app.on("before-quit", stopServer);
  process.on("SIGINT", () => app.quit());
  process.on("SIGTERM", () => app.quit());
}
