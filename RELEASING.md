# Releasing Kukii-Home

Releases are **fully automatic** from `main`. You don't bump versions, don't
edit `CHANGELOG.md`, don't tag — semantic-release does all of it based on the
commit messages you push.

## The rule in one paragraph

Push a commit to `main`. CI runs. If the commit's Conventional-Commit prefix
is **release-worthy** (`feat:`, `fix:`, `perf:`, or any commit with a
`BREAKING CHANGE:` footer), semantic-release computes the next version,
writes it into `ha-integration/addon/kukiihome/config.yaml` and
`ha-integration/custom_components/kukiihome/manifest.json`, prepends a
CHANGELOG entry, commits the bump with `[skip ci]`, tags `v<version>`,
creates a GitHub Release, and the addon-build job pushes the new image to
GHCR. Supervisor sees the new version next time you Update in HA. If the
commit's prefix is **not** release-worthy (`chore:`, `docs:`, `refactor:`,
`test:`, `style:`, `build:`, `ci:`), CI runs validation but nothing ships.

## Commit prefix → bump

| Prefix | Bump | Example |
|---|---|---|
| `fix:` | patch (0.7.0 → 0.7.1) | `fix(ha-agent): stale keep-alive POST race` |
| `perf:` | patch | `perf(preprocessor): cache encoded crops` |
| `feat:` | minor (0.7.0 → 0.8.0) | `feat(identity): add gait Stage-2 worker` |
| `feat!:` or any commit with `BREAKING CHANGE:` footer | major (0.7.0 → 1.0.0) | `feat(api)!: rename /identity to /people` |
| `docs:` | none | `docs(ui): ratify Part VI` |
| `chore:` | none | `chore(deps): bump pytest` |
| `refactor:` | none | `refactor(identity): extract corpus loader` |
| `test:` | none | `test(home): cover empty-state copy` |
| `style:` | none | `style: reformat config.yaml` |
| `build:` | none | `build: drop amd64 from matrix` |
| `ci:` | none | `ci: switch to release-on-merge` |

A scope (`feat(identity): …`, `fix(ha-agent): …`) is optional and informational
— the bump is decided by the prefix only.

## Multi-line commits — `BREAKING CHANGE:` footer

Add a footer to force a major bump:

```
feat(api): rename /identity/tracks → /people/observations

BREAKING CHANGE: clients that called /identity/tracks now hit 404.
Update to /people/observations or pin to the previous version.
```

## Forcing a release on a non-release-worthy commit

Rare, but: if you've made a series of `chore:`/`refactor:` commits that *do*
deserve a release (e.g., a code cleanup that meaningfully improves things),
include a `Release-As:` footer in the latest commit:

```
chore: tidy up the identity module

Release-As: 0.8.0
```

## Skipping a release on a release-worthy-looking commit

Add `[skip release]` to the commit subject, or use a non-release-worthy
prefix to begin with.

## What CI does, step by step

1. **validate** — YAML + integration compile-check + Dockerfile lint. Runs
   on every push and every PR.
2. **s6-scripts** — verify the s6 service launchers point at the venv python.
   Runs on every push and every PR.
3. **release** — semantic-release. Runs only on push to `main`. Outputs
   `new_release_published` (true / false), and on true, also
   `new_release_version` and the SHA of the bump commit it just pushed.
4. **build** — Docker build (aarch64). Runs on PRs (load only, no push — a
   sanity check) and on push-to-`main` *only when* a release was published
   (push to GHCR using the bumped version). Skipped on push-to-`main`
   commits that didn't produce a release.
5. **verify-published** — re-pull the manifest at the published version for
   every arch listed in `config.yaml`'s `arch:` field. Red here means
   Supervisor would 404 on Update.

If steps 1-5 are green, the new version is safe to install in HA.

## Local checks before pushing

There's no required local test pass for releases, but you can preview what
semantic-release *would* do without actually releasing:

```bash
# Show planned bump + changelog entry for the commits on your branch.
# Run from repo root; requires Node 20+.
npx --yes semantic-release --dry-run --no-ci --branches "$(git branch --show-current)"
```

## When something goes wrong

- **Release published but image push failed** — fix the push problem, then
  push a `chore: retry release` commit; on success, the existing version
  doesn't change (no `feat`/`fix`). You may need to manually delete the
  GitHub Release and tag to re-attempt; rare.
- **Wrong version published** — never rewrite history. Push a `fix:` /
  `feat:` commit that adjusts behaviour going forward; let the next version
  supersede it.
- **CI fails mid-release** — semantic-release commits the bump *before* the
  image is built, so a build failure leaves `config.yaml` pointing at a
  version Supervisor can't pull. The next commit's CI run will retry the
  build. If that's not enough, manually re-trigger the workflow on the bump
  commit via the Actions tab.

## Historical context

This workflow replaced manual version bumps on 2026-06-04. Prior to that
each release required: edit `config.yaml` version, edit `manifest.json`
version, write a CHANGELOG entry, commit, push, watch CI, hope. Failure
modes included: forgotten changelog entries, version drift between the
add-on and integration files, manual updates clicked in HA before CI had
finished publishing (404s). All gone now.
