// Сборка DeepWave: main/preload (Node) и интерфейс (браузер) через esbuild.
import { build } from "esbuild";
import { cpSync, existsSync, mkdirSync, writeFileSync, rmSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const prod = process.argv.includes("--prod");
const out = join(here, "dist");
rmSync(out, { recursive: true, force: true });
mkdirSync(join(out, "renderer"), { recursive: true });
mkdirSync(join(here, "build"), { recursive: true });

// Корень проекта (папка с crawler.py, backend/ и catalog/) — упакованное приложение найдёт его по root.json
writeFileSync(join(here, "build", "root.json"), JSON.stringify({ root: resolve(here, "..") }));

await build({
  entryPoints: [join(here, "src/main/main.ts"), join(here, "src/main/preload.ts")],
  outdir: join(out, "main"), bundle: true, platform: "node", format: "cjs", target: "node20",
  external: ["electron"], sourcemap: !prod, minify: prod, logLevel: "warning",
});

// Шрифт Inter подключается, если пакет установлен; иначе используется системный Segoe UI.
const fontPkg = join(here, "node_modules/@fontsource-variable/inter/index.css");
const fontShim = {
  name: "font-shim",
  setup(b) {
    b.onResolve({ filter: /^@fontsource-variable\/inter$/ }, () =>
      existsSync(fontPkg) ? { path: fontPkg } : { path: "font-empty", namespace: "shim" });
    b.onLoad({ filter: /.*/, namespace: "shim" }, () => ({ contents: "", loader: "css" }));
  },
};

await build({
  entryPoints: [join(here, "src/renderer/main.tsx")],
  outfile: join(out, "renderer/app.js"), bundle: true, platform: "browser", format: "iife",
  target: "chrome120", jsx: "automatic", sourcemap: !prod, minify: prod, logLevel: "warning",
  loader: { ".woff2": "file", ".woff": "file", ".svg": "dataurl" }, assetNames: "assets/[name]-[hash]",
  plugins: [fontShim], define: { "process.env.NODE_ENV": JSON.stringify(prod ? "production" : "development") },
});
cpSync(join(here, "src/renderer/index.html"), join(out, "renderer/index.html"));
console.log(`Сборка готова (${prod ? "production" : "development"})`);
