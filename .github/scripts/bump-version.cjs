#!/usr/bin/env node
/**
 * Bump the add-on + integration version strings in lockstep.
 *
 * Called by semantic-release's @semantic-release/exec plugin from the
 * `prepareCmd` step (see .releaserc.json) — semantic-release decided the new
 * version, this writes it into the two files Supervisor / HA Core look at.
 *
 *   node .github/scripts/bump-version.cjs <new-version>
 *
 * Touches only those two files; @semantic-release/git lists them as `assets`
 * and commits all three together (the CHANGELOG written by
 * @semantic-release/changelog being the third).
 */
'use strict';

const fs = require('fs');

const version = process.argv[2];
if (!version) {
  console.error('usage: bump-version.cjs <version>');
  process.exit(1);
}

// ── config.yaml ───────────────────────────────────────────────────
// Line shape: `version: 'X.Y.Z'`. Single-quoted so YAML treats it as a string
// (avoids 0.10 → float-parsing surprises). Match the existing form exactly so
// the surrounding comments / blank lines stay untouched.
const yamlPath = 'ha-integration/addon/kukiihome/config.yaml';
const yaml = fs.readFileSync(yamlPath, 'utf8');
const yamlNew = yaml.replace(/^version: .*$/m, `version: '${version}'`);
if (yaml === yamlNew) {
  console.error(`bump-version: no 'version:' line found in ${yamlPath}`);
  process.exit(1);
}
fs.writeFileSync(yamlPath, yamlNew);

// ── manifest.json ─────────────────────────────────────────────────
// JSON.parse + serialize keeps formatting consistent; we re-emit with a
// trailing newline to match what Python / git diff users expect.
const manifestPath = 'ha-integration/custom_components/kukiihome/manifest.json';
const manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8'));
manifest.version = version;
fs.writeFileSync(manifestPath, JSON.stringify(manifest, null, 2) + '\n');

console.log(`bump-version: wrote ${version} -> ${yamlPath}, ${manifestPath}`);
