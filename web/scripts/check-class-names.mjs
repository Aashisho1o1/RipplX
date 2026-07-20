// Check static className strings and literal template chunks against shipped CSS.
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("../src/", import.meta.url));
function walk(directory) { return readdirSync(directory).flatMap(entry => { const full = join(directory, entry); return statSync(full).isDirectory() ? walk(full) : [full]; }); }
const files = walk(root);
const css = files.filter(file => file.endsWith(".css")).map(file => readFileSync(file, "utf8")).join("\n");
const defined = new Set([...css.matchAll(/\.([A-Za-z][\w-]*)/g)].map(match => match[1]));
const attribute = /className=(?:"([^"]*)"|\{`([^`]*)`\})/g;
const problems = [];
for (const file of files.filter(name => name.endsWith(".tsx"))) {
  const source = readFileSync(file, "utf8");
  for (const match of source.matchAll(attribute)) {
    const literal = (match[1] ?? match[2]).replace(/\$\{[^}]*\}/g, " ");
    const line = source.slice(0, match.index).split("\n").length;
    for (const name of literal.split(/\s+/).filter(Boolean)) if (!defined.has(name)) problems.push(`${file.slice(root.length)}:${line} .${name}`);
  }
}
if (problems.length) {
  console.error(`Undefined CSS class names (${problems.length}):`);
  for (const problem of problems) console.error(`  ${problem}`);
  process.exit(1);
}
console.log(`All className literals resolve to a selector (${defined.size} defined).`);
