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
| `fix:` | patch (0.26.0 → 0.26.1) | `fix(ha-agent): stale keep-alive POST race` |
| `perf:` | patch | `perf(preprocessor): cache encoded crops` |
| `feat:` | **patch** (0.26.0 → 0.26.1) | `feat(identity): add gait Stage-2 worker` |
| `feat!:` or any commit with `BREAKING CHANGE:` footer | **minor** (0.26.0 → 0.27.0) | `feat(api)!: rename /identity to /people` |
| `docs:` | none | `docs(ui): ratify Part VI` |
| `chore:` | none | `chore(deps): bump pytest` |
| `refactor:` | none | `refactor(identity): extract corpus loader` |
| `test:` | none | `test(home): cover empty-state copy` |
| `style:` | none | `style: reformat config.yaml` |
| `build:` | none | `build: drop amd64 from matrix` |
| `ci:` | none | `ci: switch to release-on-merge` |

A scope (`feat(identity): …`, `fix(ha-agent): …`) is optional and informational
— the bump is decided by the prefix only.

### Why feat → patch (not minor)

Before 1.0, semver's minor/patch distinction is mostly aesthetic — there's
no "API-stable" contract to honor yet. The default `feat → minor` rule from
Conventional Commits would chew through minor numbers fast and push us to
1.0 long before the design is settled. We deliberately:

- Map **`feat:`** to **patch** so day-to-day shipping doesn't burn minor
  version numbers
- Map **`BREAKING CHANGE:`** (and `feat!:` / `fix!:` etc.) to **minor**,
  reserving major for an explicit, deliberate 1.0 cut
- Use **`Release-As:`** when we *want* a minor or major bump for a
  milestone release (see below)

This keeps 0.x.y meaningful: patches accumulate normally, minor bumps mark
genuine inflection points we chose to call out.

## Multi-line commits — `BREAKING CHANGE:` footer

Add a footer to force a **minor** bump (pre-1.0; becomes major post-1.0):

```
feat(api): rename /identity/tracks → /people/observations

BREAKING CHANGE: clients that called /identity/tracks now hit 404.
Update to /people/observations or pin to the previous version.
```

## Explicit milestone bumps — `Release-As:` footer

When you want a deliberate minor or major release, add a `Release-As:`
footer with the target version:

```
feat(memory): drawer ships; iteration 3 closes

Release-As: 0.27.0
```

This is the only way to bump minor (or major) under the default rules.
Use it sparingly — minor bumps should mark moments worth marking.

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
3. **release** (push to `main` only) — semantic-release runs the **full release
   sequence in one job**, with a critical ordering invariant: the version on
   `main` only advances *after* the image at that version is already live in
   GHCR.

   In order, inside the release job:

   1. Analyze commits since the last `v*` tag → decide bump (or skip entirely).
   2. Generate release notes.
   3. Prepend a CHANGELOG entry (in workspace).
   4. Write the new version into `config.yaml` + `manifest.json` (in workspace).
   5. **Build the aarch64 image with the bumped files → push to GHCR.**
   6. **Verify the image is live in GHCR** (`docker manifest inspect`).
   7. If 5 + 6 succeeded: commit the bump to `main` with `[skip ci]` + push
      a `vX.Y.Z` git tag.
   8. Create the GitHub Release with the generated notes.

   **If steps 5 or 6 fail, steps 7 + 8 never run.** `main` stays at the old
   version. Supervisor (which polls `main`) therefore never sees a newer
   version than what's actually in GHCR — the auto-update 404 is structurally
   impossible.

4. **build** (PR only) — Docker build with `--load`, no push. Catches
   Dockerfile regressions before merge. Doesn't run on `main` pushes
   because the release job already builds (and pushes) authoritative images.

If the release job is green, the new version is safe to install in HA — the
verify step is *inside* the release job, so green = published + verified.

## What happens on each commit type

| Push to main | Validate | s6-scripts | Release | Effect |
|---|---|---|---|---|
| `feat:` / `fix:` / `perf:` / breaking | ✓ | ✓ | runs full sequence | New version published; HA can Update safely |
| `chore:` / `docs:` / `refactor:` / `test:` / `style:` / `build:` / `ci:` | ✓ | ✓ | runs but exits early (`new_release_published=false`) | Nothing changes; CI confirms code still passes lint + structural checks |
| Anything failing validate or s6-scripts | ✓ red | ✓ red | not reached | Nothing changes; fix and re-push |
| Anything failing docker build or GHCR verify | ✓ | ✓ | red at the build step | Nothing on `main` changes; image at the failed version may be an orphan in GHCR (harmless). Fix and re-push. |

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
