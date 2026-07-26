import test from "node:test";
import assert from "node:assert/strict";
import { existsSync, readFileSync, statSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = dirname(dirname(fileURLToPath(import.meta.url)));

test("macOS launcher app bundle opens the local desktop client", () => {
  const appRoot = join(root, "AStock Lens.app");
  const executable = join(appRoot, "Contents/MacOS/astock-lens");
  const plist = join(appRoot, "Contents/Info.plist");
  const launcher = join(root, "scripts/launch-local-app.zsh");

  assert(existsSync(appRoot), "missing clickable macOS .app launcher");
  assert(existsSync(plist), "missing macOS launcher Info.plist");
  assert(existsSync(executable), "missing macOS launcher executable");
  assert(existsSync(launcher), "missing local launch script");

  const mode = statSync(executable).mode;
  assert(mode & 0o111, "macOS launcher executable must be executable");

  const plistText = readFileSync(plist, "utf8");
  assert(plistText.includes("<key>CFBundleExecutable</key>"), "Info.plist must declare executable");
  assert(plistText.includes("<string>astock-lens</string>"), "Info.plist must point at astock-lens");
  assert(plistText.includes("<string>AStock Lens</string>"), "Info.plist must expose app display name");

  const executableText = readFileSync(executable, "utf8");
  assert(executableText.includes("scripts/launch-local-app.zsh"), "app executable must delegate to the launch script");
  assert(executableText.includes("osascript"), "app executable must open a visible Terminal session");

  const launcherText = readFileSync(launcher, "utf8");
  assert(launcherText.includes('http://127.0.0.1:${READ_PORT}'), "launcher must default to the local read service");
  assert(launcherText.includes('READ_SERVICE_HEALTH="$READ_SERVICE_URL/health"'), "launcher must derive the health endpoint");
  assert(launcherText.includes('curl -fsS "$READ_SERVICE_HEALTH"'), "launcher must check the local read service");
  assert(launcherText.includes("python3 -m uvicorn api.main:app"), "launcher must start the Python read service");
  assert(launcherText.includes("electron_runtime_is_available"), "launcher must verify Electron runtime before launching");
  assert(launcherText.includes("electron_app_path"), "launcher must resolve Electron.app for macOS checks");
  assert(launcherText.includes('require("electron")'), "launcher must verify Electron without starting the GUI binary");
  assert(launcherText.includes("run_with_timeout"), "launcher must not hang indefinitely while repairing Electron");
  assert(launcherText.includes("ASTOCK_ELECTRON_REBUILD_TIMEOUT_SECONDS"), "launcher must expose Electron repair timeout");
  assert(launcherText.includes("https://npmmirror.com/mirrors/electron/"), "launcher must default to a reliable Electron mirror");
  assert(launcherText.includes("npm_config_electron_mirror"), "launcher must pass the mirror through npm config");
  assert(launcherText.includes("--foreground-scripts"), "launcher must show Electron rebuild output");
  assert(launcherText.includes("codesign --verify --deep --strict"), "launcher must detect broken Electron code signatures");
  assert(launcherText.includes("codesign --force --deep --sign -"), "launcher must repair broken Electron code signatures");
  assert(launcherText.includes("xattr -dr com.apple.quarantine"), "launcher must clear quarantine before signing");
  assert(launcherText.includes("npm run dev"), "launcher must start the Electron + React client");
  assert(!launcherText.includes("git add -A"), "launcher must not run broad git commands");
  assert(!launcherText.includes("git reset"), "launcher must not run destructive git commands");
});

test("runtime files are kept out of git", () => {
  const ignoreText = readFileSync(join(root, ".gitignore"), "utf8");
  assert(ignoreText.includes(".runtime/"), "runtime pid/log files must be ignored");
});
