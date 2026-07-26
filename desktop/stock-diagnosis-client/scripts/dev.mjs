import { spawn } from "node:child_process";

const vite = spawn("npx", ["vite", "--host", "127.0.0.1", "--port", "5174"], {
  stdio: "inherit",
  shell: false,
});

let electron;
const timer = setTimeout(() => {
  electron = spawn("npx", ["electron", "."], {
    stdio: "inherit",
    shell: false,
    env: {
      ...process.env,
      VITE_DEV_SERVER_URL: "http://127.0.0.1:5174",
    },
  });
  electron.on("exit", (code) => {
    vite.kill();
    process.exit(code ?? 0);
  });
}, 1200);

vite.on("exit", (code) => {
  clearTimeout(timer);
  if (electron) electron.kill();
  process.exit(code ?? 0);
});

process.on("SIGINT", () => {
  clearTimeout(timer);
  vite.kill();
  if (electron) electron.kill();
});
