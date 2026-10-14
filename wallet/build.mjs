// Bundle src/wallet.js (React + Privy + Solana connectors) for the static site.
// Run: npm run build   (output: ../web/wallet/privy-wallet.js plus its c-*.js chunks)
// Code splitting keeps Privy's lazily used parts (EVM, WalletConnect, ...) out of the first load.
import { build } from 'esbuild';
import { mkdirSync, readdirSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { gzipSync } from 'node:zlib';
import { readFileSync } from 'node:fs';

const outdir = '../web/wallet';
rmSync(outdir, { recursive: true, force: true }); // generated files only
mkdirSync(outdir, { recursive: true });

const result = await build({
  entryPoints: { 'privy-wallet': 'src/wallet.js' },
  outdir,
  bundle: true,
  splitting: true,
  format: 'esm',
  platform: 'browser',
  target: ['chrome100', 'safari15', 'firefox100'],
  minify: true,
  legalComments: 'none',
  chunkNames: 'c-[hash]',
  metafile: true,
  define: {
    'process.env.NODE_ENV': '"production"',
    global: 'globalThis',
  },
  logLevel: 'warning',
});

// Report what a visitor downloads on first use: the entry plus everything it imports statically.
const outputs = result.metafile.outputs;
const entry = Object.keys(outputs).find((f) => outputs[f].entryPoint);
const eager = new Set([entry]);
const queue = [entry];
while (queue.length) {
  for (const im of outputs[queue.pop()].imports) {
    if (im.kind === 'import-statement' && !eager.has(im.path)) { eager.add(im.path); queue.push(im.path); }
  }
}
let gz = 0;
for (const f of eager) gz += gzipSync(readFileSync(f), { level: 9 }).length;

// preload.json: every file needed on first use, so the page can fetch them in parallel (modulepreload)
// instead of discovering them one import level at a time.
const names = [...eager].map((f) => f.split('/').pop()).filter((n) => n !== 'privy-wallet.js');
writeFileSync(`${outdir}/preload.json`, JSON.stringify(names));
// Every request into wallet/ is versioned (chunks by content hash, entry and preload.json by ?v=), so cache for a year.
writeFileSync(`${outdir}/.htaccess`, '<IfModule mod_headers.c>\n  Header set Cache-Control "public, max-age=31536000, immutable"\n</IfModule>\n');
const files = readdirSync(outdir);
const total = files.reduce((n, f) => n + statSync(`${outdir}/${f}`).size, 0);
console.log(`built ${outdir}: ${files.length} files, ${(total / 1024).toFixed(0)} KB total; first use ${eager.size} files, ${(gz / 1024).toFixed(0)} KB gzip`);
