#!/usr/bin/env python3
"""
Sync planning/epics/*.md to GitHub Issues.

For each epic file:
  1. Create or find the parent epic issue (idempotent: searches existing issues by title)
  2. Create each sub-issue, capturing the issue number
  3. Update the epic body with a task list of sub-issues

Idempotent: re-running won't duplicate issues already created.
Issues are matched by title.

Requires: gh CLI authenticated, repo set via REPO env var or default.
"""

import io
import json
import re
import subprocess
import sys
from pathlib import Path

# Force UTF-8 stdout on Windows so Unicode in issue titles doesn't crash printing.
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "buffer"):
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

REPO = "DarinShapiro/SentiHome"
EPICS_DIR = Path(__file__).parent.parent.parent / "planning" / "epics"


def run_gh(args, input_text=None):
    """Run gh CLI, return stdout. Raise on error."""
    cmd = ["gh"] + args
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        input=input_text,
        encoding="utf-8",
    )
    if result.returncode != 0:
        print(f"FAILED: {' '.join(cmd)}", file=sys.stderr)
        print(result.stderr, file=sys.stderr)
        sys.exit(1)
    return result.stdout.strip()


def list_existing_issues():
    """Return dict mapping title -> issue number for all issues (open + closed)."""
    out = run_gh([
        "issue", "list",
        "--repo", REPO,
        "--state", "all",
        "--limit", "1000",
        "--json", "number,title",
    ])
    issues = json.loads(out)
    return {issue["title"]: issue["number"] for issue in issues}


def create_issue(title, body, labels):
    """Create an issue; return its number."""
    args = [
        "issue", "create",
        "--repo", REPO,
        "--title", title,
        "--body", body,
    ]
    for label in labels:
        args += ["--label", label]
    url = run_gh(args)
    # URL ends with /issues/N
    return int(url.rsplit("/", 1)[-1])


def edit_issue_body(number, body):
    """Replace an issue's body."""
    run_gh([
        "issue", "edit", str(number),
        "--repo", REPO,
        "--body", body,
    ])


def parse_epic_file(path):
    """Parse an epic markdown file. Return dict with title, body, sub_issues."""
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()

    # First line: # Epic NN: Title
    epic_title_line = lines[0].lstrip("# ").strip()
    epic_title = f"Epic: {epic_title_line.split(':', 1)[1].strip()}"

    # Find sections
    arch_refs = ""
    components = ""
    priority = ""
    blocked_by = ""
    blocks = ""
    description = ""
    definition_of_done = ""
    issues_raw = []

    section = None
    section_lines = []

    def flush():
        nonlocal description, definition_of_done
        if section == "description":
            description = "\n".join(section_lines).strip()
        elif section == "definition of done":
            definition_of_done = "\n".join(section_lines).strip()
        elif section == "issues":
            issues_raw.extend(section_lines)

    for line in lines[1:]:
        stripped = line.strip()
        # Frontmatter-style metadata
        if stripped.startswith("**Architecture refs:**"):
            arch_refs = stripped.split("**Architecture refs:**", 1)[1].strip()
        elif stripped.startswith("**Components:**"):
            components = stripped.split("**Components:**", 1)[1].strip()
        elif stripped.startswith("**Priority:**"):
            priority = stripped.split("**Priority:**", 1)[1].strip()
        elif stripped.startswith("**Blocked by:**"):
            blocked_by = stripped.split("**Blocked by:**", 1)[1].strip()
        elif stripped.startswith("**Blocks:**"):
            blocks = stripped.split("**Blocks:**", 1)[1].strip()
        # Section headers
        elif stripped.startswith("## "):
            flush()
            section = stripped[3:].strip().lower()
            section_lines = []
        else:
            section_lines.append(line)
    flush()

    # Parse sub-issues from issues_raw
    # Each issue is a numbered list item: `N. **title** — description (labels: ...)`
    sub_issues = []
    pat = re.compile(r"^\s*\d+\.\s+\*\*(.+?)\*\*\s+—\s+(.+?)\s+\(labels:\s+(.+?)\)\s*$")
    for line in issues_raw:
        m = pat.match(line)
        if m:
            title = m.group(1).strip()
            desc = m.group(2).strip()
            label_text = m.group(3).strip()
            # Labels are like `epic:foundation`, `component:infrastructure`, `priority:p0` separated by commas
            labels = [l.strip().strip("`") for l in label_text.split(",")]
            sub_issues.append({
                "title": title,
                "description": desc,
                "labels": labels,
            })

    return {
        "title": epic_title,
        "arch_refs": arch_refs,
        "components": components,
        "priority": priority,
        "blocked_by": blocked_by,
        "blocks": blocks,
        "description": description,
        "definition_of_done": definition_of_done,
        "sub_issues": sub_issues,
        "file": path.name,
    }


def epic_body(epic, sub_issue_numbers):
    """Render the epic body markdown including task list of sub-issues."""
    lines = []
    lines.append("## Description")
    lines.append("")
    lines.append(epic["description"])
    lines.append("")
    if epic["arch_refs"]:
        lines.append(f"**Architecture references:** {epic['arch_refs']}")
        lines.append("")
    if epic["components"]:
        lines.append(f"**Components:** {epic['components']}")
        lines.append("")
    if epic["priority"]:
        lines.append(f"**Priority:** {epic['priority']}")
        lines.append("")
    if epic["blocked_by"]:
        lines.append(f"**Blocked by:** {epic['blocked_by']}")
        lines.append("")
    if epic["blocks"]:
        lines.append(f"**Blocks:** {epic['blocks']}")
        lines.append("")
    lines.append("## Sub-issues")
    lines.append("")
    for sub, num in zip(epic["sub_issues"], sub_issue_numbers):
        lines.append(f"- [ ] #{num} — {sub['title']}")
    lines.append("")
    lines.append("## Definition of done")
    lines.append("")
    lines.append(epic["definition_of_done"] or "_See sub-issues._")
    lines.append("")
    lines.append(f"_Source: [`planning/epics/{epic['file']}`](../blob/main/planning/epics/{epic['file']})_")
    return "\n".join(lines)


def sub_issue_body(sub, epic_number, arch_refs):
    """Render a sub-issue body."""
    lines = []
    lines.append("## Description")
    lines.append("")
    lines.append(sub["description"])
    lines.append("")
    lines.append(f"## Epic")
    lines.append("")
    lines.append(f"Part of #{epic_number}")
    lines.append("")
    if arch_refs:
        lines.append(f"## Architecture reference")
        lines.append("")
        lines.append(arch_refs)
        lines.append("")
    lines.append("## Acceptance criteria")
    lines.append("")
    lines.append("- [ ] Implementation complete")
    lines.append("- [ ] Tests added (where applicable)")
    lines.append("- [ ] Documentation updated (where applicable)")
    return "\n".join(lines)


def main():
    epic_files = sorted(EPICS_DIR.glob("*.md"))
    if not epic_files:
        print(f"No epic files found in {EPICS_DIR}")
        sys.exit(1)

    print(f"Found {len(epic_files)} epic files")
    existing = list_existing_issues()
    print(f"Found {len(existing)} existing issues")

    for epic_file in epic_files:
        epic = parse_epic_file(epic_file)
        print(f"\n=== {epic['title']} ({len(epic['sub_issues'])} sub-issues) ===")

        # Create or find the parent epic
        if epic["title"] in existing:
            epic_number = existing[epic["title"]]
            print(f"  Epic exists: #{epic_number}")
            new_epic = False
        else:
            # Create with placeholder body (we'll update after sub-issues created)
            epic_number = create_issue(
                title=epic["title"],
                body="_Creating sub-issues..._",
                labels=["type:epic"],
            )
            print(f"  Created epic: #{epic_number}")
            new_epic = True
            existing[epic["title"]] = epic_number

        # Create sub-issues
        sub_numbers = []
        for sub in epic["sub_issues"]:
            if sub["title"] in existing:
                num = existing[sub["title"]]
                print(f"  Sub exists: #{num} {sub['title']}")
            else:
                body = sub_issue_body(sub, epic_number, epic["arch_refs"])
                num = create_issue(
                    title=sub["title"],
                    body=body,
                    labels=sub["labels"],
                )
                print(f"  Created: #{num} {sub['title']}")
                existing[sub["title"]] = num
            sub_numbers.append(num)

        # Update epic body with task list
        body = epic_body(epic, sub_numbers)
        edit_issue_body(epic_number, body)
        print(f"  Updated epic #{epic_number} body with {len(sub_numbers)} sub-issues")

    print("\nDone.")


if __name__ == "__main__":
    main()
