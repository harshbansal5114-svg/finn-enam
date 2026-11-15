// Keeps public/fallback_bundle.json fresh. Never fails the build: if no source is
// reachable we keep whatever file is already committed (that file IS the safety net).
//
// Source order:  1) ../backend/data/fallback_bundle.json (local monorepo)
//                2) GET ${NEXT_PUBLIC_API_URL}/api/fallback-bundle (4s timeout)
import { copyFileSync, existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const dest = resolve(root, "public", "fallback_bundle.json");
const local = resolve(root, "..", "backend", "data", "fallback_bundle.json");
const api = (process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000").replace(/\/+$/, "");

mkdirSync(dirname(dest), { recursive: true });

const valid = (txt) => {
  try {
    const j = JSON.parse(txt);
    return j && typeof j === "object" && "pulls" in j;
  } catch {
    return false;
  }
};

const keep = (why) => {
  console.log(`[sync-bundle] ${why}; ${existsSync(dest) ? "keeping existing public/fallback_bundle.json" : "no bundle present (UI will run live-only)"}`);
  process.exit(0);
};

if (existsSync(local) && valid(readFileSync(local, "utf8"))) {
  copyFileSync(local, dest);
  console.log(`[sync-bundle] copied ${local}`);
  process.exit(0);
}

try {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), 4000);
  const res = await fetch(`${api}/api/fallback-bundle`, { signal: ctrl.signal });
  clearTimeout(t);
  if (!res.ok) keep(`backend answered ${res.status}`);
  const txt = await res.text();
  if (!valid(txt)) keep("backend returned an invalid bundle");
  writeFileSync(dest, txt);
  console.log(`[sync-bundle] downloaded bundle from ${api}`);
} catch (e) {
  keep(`backend unreachable (${e?.name || e})`);
}
