#!/usr/bin/env bash
# scripts/dev/regenerate-schemas.sh
# Regenerate Python + TypeScript bindings from JSON schemas in shared/schemas/
#
# Outputs:
#   shared/lib-python/src/sentihome_shared/generated/    (pydantic models)
#   shared/lib-typescript/src/generated/                  (TS interfaces)
#
# CI runs this and fails if generated content differs from committed.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

PY_OUT="shared/lib-python/src/sentihome_shared/generated"
TS_OUT="shared/lib-typescript/src/generated"

blue()   { printf '\033[34m%s\033[0m\n' "$*"; }
green()  { printf '\033[32m%s\033[0m\n' "$*"; }
yellow() { printf '\033[33m%s\033[0m\n' "$*"; }

# Clean and recreate output dirs
rm -rf "$PY_OUT" "$TS_OUT"
mkdir -p "$PY_OUT" "$TS_OUT"

# ──────────────────────────────────────────────────────────────
# Python (datamodel-code-generator → pydantic v2)
# ──────────────────────────────────────────────────────────────
blue "▶ Generating Python pydantic models from schemas..."

# Process each .schema.json individually so non-schema files (README, etc.)
# in shared/schemas/ don't trip up datamodel-codegen's directory walk.
while IFS= read -r schema_file; do
  rel_path="${schema_file#shared/schemas/}"
  out_dir="$PY_OUT/$(dirname "$rel_path")"
  # Module names use underscores (Python convention); dashes break imports
  base_name="$(basename "${rel_path%.schema.json}" | tr '-' '_')"
  out_file="$out_dir/${base_name}.py"
  mkdir -p "$out_dir"

  # Use the input directory so $ref paths resolve. Single-file mode with the
  # parent directory as input root gives us a working compromise.
  uv run datamodel-codegen \
    --input "$schema_file" \
    --input-file-type jsonschema \
    --output "$out_file" \
    --output-model-type pydantic_v2.BaseModel \
    --target-python-version 3.12 \
    --use-double-quotes \
    --use-schema-description \
    --use-field-description \
    --field-constraints \
    --use-standard-collections \
    --use-union-operator \
    --disable-timestamp \
    --reuse-model 2>/dev/null || yellow "  ::warning:: Failed: $rel_path"
done < <(find shared/schemas -name '*.schema.json' -type f | sort)

# Ensure the generated tree is a proper Python package
find "$PY_OUT" -type d -exec sh -c '
  for dir; do
    [[ -f "$dir/__init__.py" ]] || cat > "$dir/__init__.py" <<EOF
"""Generated from JSON schemas. DO NOT EDIT BY HAND.

Regenerate with: scripts/dev/regenerate-schemas.sh
"""
EOF
  done
' sh {} +

py_count=$(find "$PY_OUT" -name '*.py' -not -name '__init__.py' | wc -l)
green "  Python: $py_count model file(s) generated"

# ──────────────────────────────────────────────────────────────
# TypeScript (json-schema-to-typescript)
# ──────────────────────────────────────────────────────────────
blue "▶ Generating TypeScript interfaces from schemas..."

if ! command -v npx >/dev/null 2>&1; then
  yellow "::warning::npx not found; skipping TypeScript generation"
else
  cat > "$TS_OUT/index.ts" <<'EOF'
/**
 * Generated TypeScript interfaces from shared/schemas/. DO NOT EDIT BY HAND.
 *
 * Regenerate with: scripts/dev/regenerate-schemas.sh
 */
EOF

  # We need to use --cwd so json2ts can resolve $ref relative paths
  exports=""
  while IFS= read -r schema_file; do
    rel_path="${schema_file#shared/schemas/}"
    out_name="$(echo "${rel_path%.schema.json}" | tr '/' '-' | tr '[:upper:]' '[:lower:]').ts"
    out_path="$TS_OUT/$out_name"
    module_name="${out_name%.ts}"

    if npx --yes -p json-schema-to-typescript@^15.0.4 json2ts \
        --input "$schema_file" \
        --output "$out_path" \
        --cwd "$(dirname "$schema_file")" \
        --no-additionalProperties 2>/dev/null; then
      exports="${exports}export * from './${module_name}.js';\n"
    else
      yellow "  ::warning:: Failed to generate $out_path"
    fi
  done < <(find shared/schemas -name '*.schema.json' -type f | sort)

  printf "$exports" >> "$TS_OUT/index.ts"

  ts_count=$(find "$TS_OUT" -name '*.ts' -not -name 'index.ts' | wc -l)
  green "  TypeScript: $ts_count interface file(s) generated"
fi

green "✓ Schema regeneration complete."
echo
echo "Generated files:"
echo "  Python:     $PY_OUT/"
echo "  TypeScript: $TS_OUT/"
