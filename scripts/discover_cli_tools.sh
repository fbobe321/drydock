#!/bin/bash
# Discover installed CLI tools and create DryDock skill wrappers
# Run this to auto-detect useful CLIs on the system

SKILLS_DIR="$HOME/.drydock/skills"
mkdir -p "$SKILLS_DIR"

# List of CLI tools to check for and create skill wrappers
TOOLS=(
    "jq|JSON processor — query and transform JSON data"
    "yq|YAML/JSON/XML processor — like jq but for YAML"
    "rg|ripgrep — fast regex search across files"
    "fd|fd-find — fast file finder (better find)"
    "bat|cat with syntax highlighting and line numbers"
    "scc|Source code line counter by language"
    "httpie|HTTPie — user-friendly HTTP client (http command)"
    "tree|Directory tree viewer"
    "pandoc|Universal document converter"
    "ffmpeg|Audio/video processing tool"
    "convert|ImageMagick — image conversion and manipulation"
    "sqlite3|SQLite database CLI"
)

created=0
for entry in "${TOOLS[@]}"; do
    IFS='|' read -r cmd desc <<< "$entry"

    # Check if command exists
    if ! command -v "$cmd" &>/dev/null; then
        continue
    fi

    skill_name="system-$cmd"
    skill_dir="$SKILLS_DIR/$skill_name"

    # Skip if already exists
    [ -f "$skill_dir/SKILL.md" ] && continue

    # Get help text
    help_text=$($cmd --help 2>&1 | head -30 || echo "No help available")

    mkdir -p "$skill_dir"
    cat > "$skill_dir/SKILL.md" << EOF
---
name: $skill_name
description: "$desc"
user-invocable: false
---

# $cmd

$desc

## Usage
Run via bash tool: \`$cmd [args]\`

## Help Output
\`\`\`
$help_text
\`\`\`
EOF

    echo "Created skill: $skill_name ($cmd)"
    created=$((created + 1))
done

echo "---"
echo "Discovered $created new CLI tools as skills"
echo "Total user skills: $(ls -d "$SKILLS_DIR"/*/SKILL.md 2>/dev/null | wc -l)"
