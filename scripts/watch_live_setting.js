#!/usr/bin/env node

const fs = require("fs");
const path = require("path");
const { spawn } = require("child_process");
const { initializeApp } = require("firebase/app");
const { getAuth, signInWithEmailAndPassword } = require("firebase/auth");
const { getFirestore, doc, onSnapshot } = require("firebase/firestore");

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

loadDotEnv(path.resolve(__dirname, "..", "config", ".env"));

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
const runCommand = "uv run python run_token_agent_firebase.py";
const projectRoot = path.resolve(__dirname, "..");

const app = initializeApp(firebaseConfig);
const db = getFirestore(app);
const auth = getAuth(app);

let lastLiveValue = null;
let runningChild = null;

function startAgentProcess() {
  if (runningChild && !runningChild.killed) {
    console.log("[live-watch] agent process already running, skip start");
    return;
  }

  console.log(`[live-watch] starting agent: ${runCommand}`);
  runningChild = spawn(runCommand, {
    cwd: projectRoot,
    stdio: "inherit",
    shell: true,
    env: process.env,
  });

  runningChild.on("exit", (code, signal) => {
    console.log(
      `[live-watch] agent process exited code=${code ?? "null"} signal=${signal ?? "null"}`
    );
    runningChild = null;
  });
}

function stopAgentProcess(reason = "live set to false") {
  if (!runningChild || runningChild.killed) {
    console.log("[live-watch] no running agent process to stop");
    return;
  }

  console.log(`[live-watch] stopping agent process: ${reason}`);
  runningChild.kill("SIGTERM");
}

console.log(
  `[live-watch] firebase initialized for project=${firebaseConfig.projectId}`
);

let unsubscribe = null;

function cleanupAndExit(signal) {
  console.log(`[live-watch] received ${signal}, shutting down`);
  if (typeof unsubscribe === "function") {
    unsubscribe();
  }

  if (runningChild && !runningChild.killed) {
    runningChild.kill("SIGTERM");
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

async function main() {
  const docId = await resolveDocId();
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
      console.log(`[live-watch] live=${live}`);

      if (live && lastLiveValue !== true) {
        startAgentProcess();
      } else if (!live && lastLiveValue === true) {
        stopAgentProcess("live changed true -> false");
      }

      lastLiveValue = live;
    },
    (err) => {
      console.error("[live-watch] snapshot error:", err);
    }
  );
}

main().catch((err) => {
  console.error("[live-watch] startup failed:", err);
  process.exit(1);
});
