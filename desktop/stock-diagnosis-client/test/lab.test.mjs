/**
 * Lab-rail Seatbelt write-sandbox + param-builder adversarial tests (ADR-037).
 * Real sandbox-exec spawns — no mocks.
 */
import test from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import {
  existsSync,
  mkdtempSync,
  readFileSync,
  realpathSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import os from "node:os";
import path from "node:path";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const {
  SANDBOX_EXEC,
  LAB_PROFILE_PATH,
  buildLabSpawnArgs,
  buildLabPiArgs,
  ensureLabDir,
} = require("../src/main/lab/labBridge.cjs");

const SANDBOX_BIN = SANDBOX_EXEC || "/usr/bin/sandbox-exec";

/**
 * LAB_DIR and "outside" must NOT sit under TMP_DIR (realpath of os.tmpdir()),
 * otherwise TMP_DIR write-allow would falsely pass penetration tests.
 * Use /tmp (→ /private/tmp on macOS), which is distinct from /var/folders TMPDIR.
 */
function makeTempDirs() {
  // realpath so Seatbelt subpath params match kernel-visible paths on macOS
  const labDir = realpathSync(mkdtempSync(path.join("/tmp", "lab-sb-in-")));
  const outsideDir = realpathSync(mkdtempSync(path.join("/tmp", "lab-sb-out-")));
  const tmpDir = realpathSync(os.tmpdir());
  // Ensure outside is not nested under lab or tmp allowance
  assert.equal(
    outsideDir === labDir || outsideDir.startsWith(`${labDir}${path.sep}`),
    false
  );
  assert.equal(outsideDir.startsWith(tmpDir + path.sep) || outsideDir === tmpDir, false);
  return { labDir, outsideDir, tmpDir };
}

function cleanup(...dirs) {
  for (const dir of dirs) {
    try {
      rmSync(dir, { recursive: true, force: true });
    } catch (_error) {
      // best-effort
    }
  }
}

function runSandboxed(labDir, tmpDir, shellCommand) {
  return spawnSync(
    SANDBOX_BIN,
    [
      "-D",
      `LAB_DIR=${labDir}`,
      "-D",
      `TMP_DIR=${tmpDir}`,
      "-f",
      LAB_PROFILE_PATH,
      "/bin/sh",
      "-c",
      shellCommand,
    ],
    { encoding: "utf8" }
  );
}

test("penetration write outside LAB_DIR must fail and leave no file", () => {
  const { labDir, outsideDir, tmpDir } = makeTempDirs();
  try {
    const target = path.join(outsideDir, "f");
    const result = runSandboxed(labDir, tmpDir, `echo x > "${target}"`);
    assert.notEqual(result.status, 0, `expected non-zero exit, got ${result.status}; stderr=${result.stderr}`);
    assert.equal(existsSync(target), false, "outside target must not exist after denied write");
  } finally {
    cleanup(labDir, outsideDir);
  }
});

test("nested child process write outside LAB_DIR must fail and leave no file", () => {
  const { labDir, outsideDir, tmpDir } = makeTempDirs();
  try {
    const target = path.join(outsideDir, "f2");
    const result = runSandboxed(
      labDir,
      tmpDir,
      `/bin/sh -c "echo x > \\"${target}\\""`
    );
    assert.notEqual(result.status, 0, `expected non-zero exit, got ${result.status}; stderr=${result.stderr}`);
    assert.equal(existsSync(target), false, "nested outside write must not create file");
  } finally {
    cleanup(labDir, outsideDir);
  }
});

test("write inside LAB_DIR must succeed with correct content", () => {
  const { labDir, outsideDir, tmpDir } = makeTempDirs();
  try {
    const target = path.join(labDir, "ok.txt");
    const result = runSandboxed(labDir, tmpDir, `echo hello-lab > "${target}"`);
    assert.equal(result.status, 0, `expected exit 0, got ${result.status}; stderr=${result.stderr}`);
    assert.equal(existsSync(target), true, "lab-internal file must exist");
    const body = readFileSync(target, "utf8").trim();
    assert.equal(body, "hello-lab");
  } finally {
    cleanup(labDir, outsideDir);
  }
});

test("buildLabSpawnArgs includes -f profile and both -D params", () => {
  const labDir = "/tmp/fake-lab";
  const tmpDir = "/tmp/fake-tmp";
  const piArgs = ["--offline", "-p", "hi"];
  const args = buildLabSpawnArgs({ labDir, tmpDir, piArgs });
  assert.ok(Array.isArray(args));
  assert.ok(args.includes("-f"), "must include -f");
  const fIdx = args.indexOf("-f");
  assert.equal(args[fIdx + 1], LAB_PROFILE_PATH);
  assert.ok(existsSync(LAB_PROFILE_PATH), "lab.sb profile must exist");

  const dFlags = [];
  for (let i = 0; i < args.length; i += 1) {
    if (args[i] === "-D") dFlags.push(args[i + 1]);
  }
  assert.ok(dFlags.includes(`LAB_DIR=${labDir}`), "must pass LAB_DIR");
  assert.ok(dFlags.includes(`TMP_DIR=${tmpDir}`), "must pass TMP_DIR");
  assert.equal(args[args.indexOf("pi")], "pi");
  assert.ok(args.includes("--offline"));
  assert.ok(args.includes("hi"));
});

test("buildLabPiArgs omits --no-extensions and --extension", () => {
  const args = buildLabPiArgs("explore", { model: "x", thinking: "low" });
  assert.ok(args.includes("--offline"));
  assert.ok(args.includes("--no-session"));
  assert.ok(args.includes("--no-prompt-templates"));
  assert.ok(args.includes("--no-themes"));
  assert.ok(args.includes("--mode"));
  assert.ok(args.includes("json"));
  assert.equal(args.includes("--no-extensions"), false, "Lab must not pass --no-extensions");
  assert.equal(args.includes("--extension"), false, "Lab must not pass --extension");
  assert.ok(args.includes("-p"));
  assert.ok(args.includes("explore"));
});

test("ensureLabDir rejects path-escape sessionId", () => {
  assert.throws(() => ensureLabDir("../evil"), /invalid lab sessionId|sessionId/);
  assert.throws(() => ensureLabDir("a/b"), /invalid lab sessionId|sessionId/);
  assert.throws(() => ensureLabDir("a b"), /invalid lab sessionId|sessionId/);
  assert.throws(() => ensureLabDir(""), /invalid lab sessionId|sessionId/);
});

test("ensureLabDir creates session directory under scratch/lab", () => {
  const sessionId = `test-sess-${Date.now()}`;
  const labDir = ensureLabDir(sessionId);
  try {
    assert.ok(path.isAbsolute(labDir));
    assert.ok(labDir.includes(path.join("scratch", "lab", sessionId)));
    assert.ok(existsSync(labDir));
    // write probe — without sandbox, just ensure dir is usable
    const probe = path.join(labDir, "probe.txt");
    writeFileSync(probe, "ok");
    assert.equal(readFileSync(probe, "utf8"), "ok");
  } finally {
    // remove only this session dir contents parent session folder
    try {
      rmSync(labDir, { recursive: true, force: true });
    } catch (_e) {
      /* ignore */
    }
  }
});

test("lab.sb profile content is pinned (deny-all-write + allow LAB/TMP/dev)", () => {
  const text = readFileSync(LAB_PROFILE_PATH, "utf8");
  assert.ok(text.includes("(version 1)"));
  assert.ok(text.includes("(deny file-write* (subpath \"/\"))"));
  assert.ok(text.includes('(param "LAB_DIR")'));
  assert.ok(text.includes('(param "TMP_DIR")'));
  assert.ok(text.includes('"/dev"'));
});
