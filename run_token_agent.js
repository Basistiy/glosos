#!/usr/bin/env node

const fs = require("fs");
const path = require("path");
const { spawn } = require("child_process");
const { execSync } = require("child_process");
const { initializeApp } = require("@firebase/app");
const { getAuth, signInWithEmailAndPassword } = require("@firebase/auth");
const { getFirestore, doc, onSnapshot } = require("@firebase/firestore");

function loadDotEnv(filePath) {
  if (!fs.existsSync(filePath)) {
    return;
  }

  const content = fs.readFileSync(filePath, "utf8");
  for (const line of content.split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) {
      continue;
    }

    const eqIndex = trimmed.indexOf("=");
    if (eqIndex <= 0) {
      continue;
    }

    const key = trimmed.slice(0, eqIndex).trim();
    let value = trimmed.slice(eqIndex + 1).trim();
    if (
      (value.startsWith('"') && value.endsWith('"')) ||
      (value.startsWith("'") && value.endsWith("'"))
    ) {
      value = value.slice(1, -1);
    }

    if (process.env[key] === undefined) {
      process.env[key] = value;
    }
  }
}

function requiredEnv(name) {
  const value = (process.env[name] || "").trim();
  if (!value) {
    throw new Error(`Missing required environment variable: ${name}`);
  }
  return value;
}

function optionalEnv(name, fallback = "") {
  const value = (process.env[name] || "").trim();
  return value || fallback;
}

loadDotEnv(path.resolve(__dirname, "config", ".env"));

const firebaseConfig = {
  apiKey: requiredEnv("FIREBASE_WEB_API_KEY"),
  authDomain: "glosos-103f7.firebaseapp.com",
  projectId: "glosos-103f7",
  storageBucket: "glosos-103f7.firebasestorage.app",
  messagingSenderId: "314422729512",
  appId: "1:314422729512:web:4fb8cb0278e64a5c374e1d",
  measurementId: "G-KL0T4GHC6V",
};

const collectionName = "user_settings";
const tokenEndpointUrl = "https://getlivekittokenagent-wxo2praqea-uc.a.run.app";
const restartOnCleanExit = optionalEnv("RESTART_ON_CLEAN_EXIT", "true").toLowerCase() === "true";
const runCommand = resolveRunCommand();
const projectRoot = path.resolve(__dirname);
const appVersion = resolveAppVersion();

const app = initializeApp(firebaseConfig);
const db = getFirestore(app);
const auth = getAuth(app);

let lastLiveValue = null;
let runningChild = null;
let latestAgentEnv = {};
let desiredLive = false;
let restartTimer = null;
let restartAttempts = 0;
let startRequestSeq = 0;
let childStartedAtMs = 0;
let settingsDocId = "";
let resubscribeTimer = null;
let resubscribeAttempts = 0;
let isSubscribing = false;
const UPTIME_RESET_MS = 15000;
const MIN_CLEAN_RESTART_DELAY_MS = 3000;
const MAX_RESTART_DELAY_MS = 30000;

function nowHms() {
  return new Date().toTimeString().slice(0, 8);
}

function installConsoleTimestampPrefix() {
  const originalLog = console.log.bind(console);
  const originalWarn = console.warn.bind(console);
  const originalError = console.error.bind(console);

  console.log = (...args) => originalLog(`[${nowHms()}]`, ...args);
  console.warn = (...args) => originalWarn(`[${nowHms()}]`, ...args);
  console.error = (...args) => originalError(`[${nowHms()}]`, ...args);
}

function pipeWithTimestampPrefix(readable, writable) {
  if (!readable) {
    return;
  }

  let buffer = "";
  readable.setEncoding("utf8");
  readable.on("data", (chunk) => {
    buffer += chunk;
    const lines = buffer.split(/\r?\n/);
    buffer = lines.pop() || "";
    for (const line of lines) {
      if (/^\[\d{2}:\d{2}:\d{2}\]/.test(line)) {
        writable.write(`${line}\n`);
      } else {
        writable.write(`[${nowHms()}] ${line}\n`);
      }
    }
  });
  readable.on("end", () => {
    if (!buffer) {
      return;
    }
    if (/^\[\d{2}:\d{2}:\d{2}\]/.test(buffer)) {
      writable.write(`${buffer}\n`);
    } else {
      writable.write(`[${nowHms()}] ${buffer}\n`);
    }
    buffer = "";
  });
}

installConsoleTimestampPrefix();

function resolveRunCommand() {
  try {
    execSync("command -v uv", { stdio: "ignore", shell: true });
    return "uv run python token_agent.py --daemon";
  } catch {
    return "python token_agent.py --daemon";
  }
}

function resolveAppVersion() {
  const versionPath = path.resolve(__dirname, "VERSION");
  try {
    if (fs.existsSync(versionPath)) {
      const value = fs.readFileSync(versionPath, "utf8").trim();
      if (value) {
        return value;
      }
    }
  } catch {}
  return "unknown";
}

function buildTokenRequestBody() {
  return { data: {} };
}

function extractLivekitToken(response) {
  const candidateObjects = [];
  if (response && typeof response === "object") {
    if (response.result && typeof response.result === "object") {
      candidateObjects.push(response.result);
    }
    candidateObjects.push(response);
  }

  for (const obj of candidateObjects) {
    for (const key of ["participantToken", "token", "livekitToken", "livekit_token", "jwt"]) {
      const value = typeof obj[key] === "string" ? obj[key].trim() : "";
      if (value) {
        return value;
      }
    }
  }

  throw new Error(
    "Token endpoint response does not include a token field. Expected one of: participantToken, token, livekitToken, livekit_token, jwt."
  );
}

async function fetchLivekitToken() {
  let user = auth.currentUser;
  if (!user) {
    const login =
      optionalEnv("FIREBASE_AUTH_USERNAME") || optionalEnv("FIREBASE_AUTH_EMAIL");
    const password = requiredEnv("FIREBASE_AUTH_PASSWORD");
    if (!login) {
      throw new Error(
        "Missing FIREBASE_AUTH_USERNAME (or FIREBASE_AUTH_EMAIL) in config/.env."
      );
    }
    const credential = await signInWithEmailAndPassword(auth, login, password);
    user = credential.user;
  }

  const idToken = await user.getIdToken();
  if (!idToken) {
    throw new Error("Firebase sign-in succeeded without idToken.");
  }

  const payload = buildTokenRequestBody();
  const response = await fetch(tokenEndpointUrl, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${idToken}`,
    },
    body: JSON.stringify(payload),
  });

  const rawText = await response.text();
  if (!response.ok) {
    throw new Error(
      `HTTP ${response.status} when calling token endpoint: ${rawText}\nToken endpoint request payload was: ${JSON.stringify(
        payload
      )}`
    );
  }

  let parsed;
  try {
    parsed = JSON.parse(rawText);
  } catch (err) {
    throw new Error(`Invalid JSON response from token endpoint: ${rawText.slice(0, 500)}`);
  }

  return extractLivekitToken(parsed);
}

function sendAgentCommand(command) {
  if (!runningChild || runningChild.killed || !runningChild.stdin) {
    return false;
  }
  try {
    runningChild.stdin.write(`${JSON.stringify(command)}\n`);
    return true;
  } catch (err) {
    console.error("[live-watch] failed to write command to token daemon:", err);
    return false;
  }
}

async function ensureAgentProcessRunning() {
  if (runningChild && !runningChild.killed) {
    return;
  }

  if (restartTimer) {
    clearTimeout(restartTimer);
    restartTimer = null;
  }

  console.log(`[live-watch] starting token daemon: ${runCommand}`);
  childStartedAtMs = Date.now();
  runningChild = spawn(runCommand, {
    cwd: projectRoot,
    stdio: ["pipe", "pipe", "pipe"],
    shell: true,
    env: { ...process.env },
  });
  pipeWithTimestampPrefix(runningChild.stdout, process.stdout);
  pipeWithTimestampPrefix(runningChild.stderr, process.stderr);

  runningChild.on("exit", (code, signal) => {
    const uptimeMs = Math.max(0, Date.now() - childStartedAtMs);
    console.log(
      `[live-watch] token daemon exited code=${code ?? "null"} signal=${signal ?? "null"} uptimeMs=${uptimeMs}`
    );
    runningChild = null;

    if (uptimeMs >= UPTIME_RESET_MS) {
      restartAttempts = 0;
    }
    restartAttempts += 1;
    const exponentialDelayMs = Math.min(
      MAX_RESTART_DELAY_MS,
      500 * Math.pow(2, restartAttempts - 1)
    );
    const isCleanExit = code === 0 || signal === "SIGINT";
    if (isCleanExit && !restartOnCleanExit) {
      console.log(
        "[live-watch] clean daemon exit; restart disabled (set RESTART_ON_CLEAN_EXIT=true to enable)"
      );
      restartAttempts = 0;
      return;
    }
    const delayMs = isCleanExit
      ? Math.max(MIN_CLEAN_RESTART_DELAY_MS, exponentialDelayMs)
      : exponentialDelayMs;
    console.log(
      `[live-watch] scheduling daemon restart in ${delayMs}ms (attempt ${restartAttempts})`
    );
    restartTimer = setTimeout(() => {
      restartTimer = null;
      const action = desiredLive ? startAgentSession : ensureAgentProcessRunning;
      action().catch((err) => {
        console.error("[live-watch] failed to restart token daemon:", err);
      });
    }, delayMs);
  });
}

async function startAgentSession() {
  const requestSeq = ++startRequestSeq;
  if (!desiredLive) {
    console.log("[live-watch] desiredLive=false, skip start");
    return;
  }

  await ensureAgentProcessRunning();

  const livekitToken = await fetchLivekitToken();
  if (!desiredLive) {
    console.log("[live-watch] live flipped false while fetching token, abort start");
    return;
  }
  if (requestSeq !== startRequestSeq) {
    console.log("[live-watch] newer start/stop event superseded this start request");
    return;
  }

  const linkedIdentity = optionalEnv("LIVEKIT_CLIENT_IDENTITY");
  const sent = sendAgentCommand({
    cmd: "start",
    token: livekitToken,
    linked_identity: linkedIdentity,
    agent_env: latestAgentEnv,
  });
  if (!sent) {
    throw new Error("Token daemon is not available to receive start command.");
  }
  restartAttempts = 0;
  console.log("[live-watch] start command sent to token daemon");
}

async function stopAgentSession(reason = "live set to false") {
  desiredLive = false;
  // Invalidate any in-flight async start request.
  startRequestSeq += 1;
  if (restartTimer) {
    clearTimeout(restartTimer);
    restartTimer = null;
  }

  if (!runningChild || runningChild.killed) {
    console.log("[live-watch] token daemon is not running; nothing to stop");
    return;
  }

  const sent = sendAgentCommand({ cmd: "stop", reason });
  if (sent) {
    console.log(`[live-watch] stop command sent: ${reason}`);
  } else {
    console.log("[live-watch] failed to send stop command");
  }
}

function firstString(...values) {
  for (const value of values) {
    if (typeof value === "string") {
      const trimmed = value.trim();
      if (trimmed) {
        return trimmed;
      }
    }
  }
  return "";
}

function normalizeAgentGender(data) {
  return firstString(data.agentGender).toLowerCase();
}

function normalizeAgentLanguage(data) {
  return firstString(data.agentLanguage).toLowerCase();
}

function normalizeAgentName(data) {
  return firstString(data.agentName);
}

function buildAgentEnv(data) {
  const env = {};
  const agentGender = normalizeAgentGender(data);
  const agentLanguage = normalizeAgentLanguage(data);
  const agentName = normalizeAgentName(data);
  if (agentGender) {
    env.AGENT_GENDER = agentGender;
  }
  if (agentLanguage) {
    env.AGENT_LANGUAGE = agentLanguage;
  }
  if (agentName) {
    env.AGENT_NAME = agentName;
  }
  return env;
}

console.log(
  `[live-watch] firebase initialized for project=${firebaseConfig.projectId}`
);
console.log(`[live-watch] app version=${appVersion}`);

let unsubscribe = null;

function scheduleSettingsResubscribe(reason = "snapshot error") {
  if (resubscribeTimer) {
    return;
  }
  resubscribeAttempts += 1;
  const delayMs = Math.min(30000, 500 * Math.pow(2, resubscribeAttempts - 1));
  console.warn(
    `[live-watch] scheduling Firestore resubscribe in ${delayMs}ms (attempt ${resubscribeAttempts}): ${reason}`
  );
  resubscribeTimer = setTimeout(() => {
    resubscribeTimer = null;
    subscribeToSettings(settingsDocId).catch((err) => {
      console.error("[live-watch] resubscribe failed:", err);
      scheduleSettingsResubscribe("resubscribe failed");
    });
  }, delayMs);
}

function cleanupAndExit(signal) {
  console.log(`[live-watch] received ${signal}, shutting down`);
  if (typeof unsubscribe === "function") {
    unsubscribe();
  }

  if (runningChild && !runningChild.killed) {
    sendAgentCommand({ cmd: "shutdown" });
    runningChild.kill("SIGINT");
  }

  process.exit(0);
}

process.on("SIGINT", () => cleanupAndExit("SIGINT"));
process.on("SIGTERM", () => cleanupAndExit("SIGTERM"));

async function resolveDocId() {
  const login =
    optionalEnv("FIREBASE_AUTH_USERNAME") || optionalEnv("FIREBASE_AUTH_EMAIL");
  const password = requiredEnv("FIREBASE_AUTH_PASSWORD");

  if (!login) {
    throw new Error(
      "Missing FIREBASE_AUTH_USERNAME (or FIREBASE_AUTH_EMAIL) in config/.env."
    );
  }

  const credential = await signInWithEmailAndPassword(auth, login, password);
  const uid = credential.user && credential.user.uid ? credential.user.uid : "";
  if (!uid) {
    throw new Error("Firebase sign-in succeeded but no uid was returned.");
  }
  console.log(`[live-watch] signed in as uid=${uid}`);
  return uid;
}

async function subscribeToSettings(docIdHint = "") {
  if (isSubscribing) {
    return;
  }
  isSubscribing = true;
  try {
    if (typeof unsubscribe === "function") {
      unsubscribe();
      unsubscribe = null;
    }

    const docId = docIdHint || (await resolveDocId());
    settingsDocId = docId;
    const settingsRef = doc(db, collectionName, docId);
    console.log(
      `[live-watch] listening to ${collectionName}/${docId} in project=${firebaseConfig.projectId}`
    );

    unsubscribe = onSnapshot(
    settingsRef,
    (snapshot) => {
      if (!snapshot.exists()) {
        console.warn("[live-watch] user_settings doc does not exist");
        return;
      }

      const data = snapshot.data() || {};
      const live = data.live === true;
      desiredLive = live;
      latestAgentEnv = buildAgentEnv(data);
      console.log(`[live-watch] live=${live}`);
      if (Object.keys(latestAgentEnv).length > 0) {
        console.log(`[live-watch] agent env: ${JSON.stringify(latestAgentEnv)}`);
      } else {
        console.log("[live-watch] agent env: none");
      }

      if (live && lastLiveValue !== true) {
        startAgentSession().catch((err) => {
          console.error("[live-watch] failed to start agent:", err);
        });
      } else if (live && !restartTimer) {
        startAgentSession().catch((err) => {
          console.error("[live-watch] failed to ensure running agent:", err);
        });
      } else if (!live && lastLiveValue === true) {
        stopAgentSession("live changed true -> false").catch((err) => {
          console.error("[live-watch] failed to stop agent:", err);
        });
      }

      lastLiveValue = live;
    },
    (err) => {
      console.error("[live-watch] snapshot error:", err);
      const code = String((err && err.code) || "").toLowerCase();
      if (code.includes("unauthenticated") || String(err).includes("UNAUTHENTICATED")) {
        auth
          .signOut()
          .catch(() => {})
          .finally(() => {
            scheduleSettingsResubscribe("unauthenticated; re-auth required");
          });
        return;
      }
      scheduleSettingsResubscribe("snapshot stream error");
    }
  );
    resubscribeAttempts = 0;
  } finally {
    isSubscribing = false;
  }
}

async function main() {
  settingsDocId = await resolveDocId();
  await ensureAgentProcessRunning();
  await subscribeToSettings(settingsDocId);
}

main().catch((err) => {
  console.error("[live-watch] startup failed:", err);
  process.exit(1);
});
