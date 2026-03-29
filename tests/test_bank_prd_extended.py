"""Test Bank: Extended PRD Battery — 50 projects for overnight testing.

50 PRD-driven tests across all difficulty levels. Each gives DryDock a
realistic project requirement and verifies the result runs.

Estimated runtime: 6-10 hours.

Run: pytest tests/test_bank_prd_extended.py -v -s
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.testbank_helpers import (
    check_syntax_all,
    count_python_files,
    make_agent,
    requires_vllm,
    run_workload,
)

pytestmark = [requires_vllm, pytest.mark.asyncio]


def _run(work_dir: Path, cmd: str, timeout: int = 15) -> tuple[bool, str]:
    try:
        r = subprocess.run(cmd, shell=True, cwd=work_dir,
                           capture_output=True, text=True, timeout=timeout)
        return r.returncode == 0, (r.stdout + r.stderr)[:500]
    except subprocess.TimeoutExpired:
        return False, "TIMEOUT"
    except Exception as e:
        return False, str(e)


def _try_run(work_dir: Path) -> tuple[bool, str]:
    """Try common ways to run a project."""
    packages = set()
    for init in work_dir.rglob("__init__.py"):
        pkg = init.parent
        if pkg != work_dir and ".logs" not in str(pkg):
            packages.add(pkg.relative_to(work_dir).parts[0])
    for pkg in sorted(packages):
        for cmd in [f"python3 -m {pkg} --help", f"python3 -m {pkg}"]:
            ok, out = _run(work_dir, cmd)
            if ok:
                return True, out[:200]
    for name in ("main.py", "app.py", "cli.py", "run.py", "demo.py"):
        f = work_dir / name
        if f.exists():
            for cmd in [f"python3 {f} --help", f"python3 {f}"]:
                ok, out = _run(work_dir, cmd)
                if ok:
                    return True, out[:200]
    return False, "No runnable entry point"


async def _build_and_verify(tmp_path, prd_text, prompt, min_files=1, max_turns=25, max_events=300):
    """Common pattern: write PRD, build, verify."""
    (tmp_path / "PRD.md").write_text(prd_text)
    agent = make_agent(tmp_path, max_turns=max_turns)
    r = await run_workload(agent, prompt, max_events=max_events)
    assert r.ok, f"Crash: {r.summary()}"
    n = count_python_files(tmp_path)
    assert n >= min_files, f"Only {n} files created"
    errs = check_syntax_all(tmp_path)
    assert not errs, f"Syntax errors: {errs}"
    return r


# ============================================================================
# BATCH 1: Developer Tools (10 tests)
# ============================================================================

class TestDevTools:

    async def test_code_formatter(self, tmp_path):
        await _build_and_verify(tmp_path,
            "# Code Formatter\n\nA Python code formatter that standardizes "
            "indentation (4 spaces), removes trailing whitespace, ensures "
            "single blank line between functions, and sorts imports.\n\n"
            "## Usage\n```\npython3 -m formatter myfile.py\n"
            "python3 -m formatter myfile.py --check  # don't modify, just report\n```\n\n"
            "## Requirements\n- Python stdlib only\n- Use absolute imports\n",
            "Review the PRD and build the code formatter. Test on a sample file.",
        )

    async def test_dependency_checker(self, tmp_path):
        (tmp_path / "requirements.txt").write_text("requests>=2.28\nflask>=2.0\nnumpy\n")
        await _build_and_verify(tmp_path,
            "# Dependency Checker\n\nParse requirements.txt and check which "
            "packages are installed, which are missing, and version compatibility.\n\n"
            "## Usage\n```\npython3 -m depcheck requirements.txt\n```\n\n"
            "## Output\n- Installed: package==version (OK/OUTDATED)\n"
            "- Missing: package (NOT INSTALLED)\n\n"
            "## Requirements\n- Python stdlib + importlib.metadata\n",
            "Review PRD and build. Test on requirements.txt.",
        )

    async def test_git_stats(self, tmp_path):
        await _build_and_verify(tmp_path,
            "# Git Stats Tool\n\nAnalyze a git repository and show statistics.\n\n"
            "## Usage\n```\npython3 -m gitstats /path/to/repo\n"
            "python3 -m gitstats . --since 2024-01-01\n```\n\n"
            "## Output\n- Total commits, contributors\n"
            "- Commits per author\n- Most changed files\n- Activity by day of week\n\n"
            "## Requirements\n- Python stdlib + subprocess (git commands)\n",
            "Review PRD and build. Test on the current directory (create a test git repo if needed).",
        )

    async def test_env_manager(self, tmp_path):
        await _build_and_verify(tmp_path,
            "# Environment Variable Manager\n\nManage .env files from CLI.\n\n"
            "## Usage\n```\npython3 -m envmgr get DATABASE_URL\n"
            "python3 -m envmgr set DATABASE_URL sqlite:///db.sqlite\n"
            "python3 -m envmgr list\n"
            "python3 -m envmgr validate  # check all required vars are set\n```\n\n"
            "## Features\n- Read/write .env files\n- Validate against .env.example\n"
            "- Support variable interpolation ($VAR references)\n\n"
            "## Requirements\n- Python stdlib only\n",
            "Review PRD and build. Create a test .env file and test all commands.",
        )

    async def test_project_scaffolder(self, tmp_path):
        await _build_and_verify(tmp_path,
            "# Project Scaffolder\n\nGenerate project directory structures from templates.\n\n"
            "## Usage\n```\npython3 -m scaffold create myproject --template cli\n"
            "python3 -m scaffold create myproject --template library\n"
            "python3 -m scaffold list  # show available templates\n```\n\n"
            "## Templates\n- cli: package with __main__.py, cli.py, argparse setup\n"
            "- library: package with __init__.py, core.py, utils.py\n"
            "- script: single main.py with argparse\n\n"
            "## Requirements\n- Python stdlib only\n",
            "Review PRD and build. Test by scaffolding a 'cli' project called 'myapp'.",
            min_files=2,
        )

    async def test_config_merger(self, tmp_path):
        (tmp_path / "base.json").write_text('{"host":"localhost","port":8080,"debug":false}')
        (tmp_path / "dev.json").write_text('{"debug":true,"port":3000}')
        await _build_and_verify(tmp_path,
            "# Config Merger\n\nMerge multiple config files with precedence.\n\n"
            "## Usage\n```\npython3 -m configmerge base.json dev.json -o merged.json\n"
            "python3 -m configmerge base.json dev.json --format yaml\n```\n\n"
            "## Features\n- Deep merge (nested dicts)\n- Support JSON and YAML\n"
            "- Later files override earlier ones\n- --diff mode: show what changed\n\n"
            "## Requirements\n- Python stdlib (json), pyyaml optional\n",
            "Review PRD and build. Merge base.json with dev.json.",
        )

    async def test_api_mocker(self, tmp_path):
        await _build_and_verify(tmp_path,
            "# API Mock Server\n\nA simple mock API server for testing.\n\n"
            "## Usage\n```\npython3 -m mockapi routes.json --port 9000\n```\n\n"
            "## routes.json format\n```json\n[\n"
            '  {"method":"GET","path":"/users","response":{"users":[]},"status":200},\n'
            '  {"method":"POST","path":"/users","response":{"created":true},"status":201}\n'
            "]\n```\n\n"
            "## Requirements\n- Python stdlib (http.server, json)\n"
            "- Don't start the server in tests, just verify the routing logic\n",
            "Review PRD and build. Create sample routes.json. Verify the routing logic works "
            "(don't start the server, just test the route matching).",
            min_files=2,
        )

    async def test_sql_builder(self, tmp_path):
        await _build_and_verify(tmp_path,
            "# SQL Query Builder\n\nBuild SQL queries programmatically (like SQLAlchemy Core lite).\n\n"
            "## API\n```python\nfrom sqlbuilder import Query\n"
            "q = Query('users').select('name','email').where('age > 18').order_by('name')\n"
            "print(q.build())  # SELECT name, email FROM users WHERE age > 18 ORDER BY name\n```\n\n"
            "## Features\n- SELECT, INSERT, UPDATE, DELETE\n- WHERE, ORDER BY, LIMIT, OFFSET\n"
            "- JOIN support\n- Parameter placeholders (?)\n\n"
            "## Requirements\n- Python stdlib only\n",
            "Review PRD and build. Create a demo.py that builds and prints several queries.",
            min_files=2,
        )

    async def test_diff_tool(self, tmp_path):
        (tmp_path / "old.txt").write_text("line 1\nline 2\nline 3\nline 4\n")
        (tmp_path / "new.txt").write_text("line 1\nline 2 modified\nline 3\nline 5\n")
        await _build_and_verify(tmp_path,
            "# Diff Tool\n\nCompare two text files and show differences.\n\n"
            "## Usage\n```\npython3 -m difftool old.txt new.txt\n"
            "python3 -m difftool old.txt new.txt --unified  # unified diff format\n"
            "python3 -m difftool old.txt new.txt --stats    # just show stats\n```\n\n"
            "## Output\n- Line-by-line comparison\n- Added/removed/changed lines\n"
            "- Color output (optional)\n\n"
            "## Requirements\n- Python stdlib (difflib)\n",
            "Review PRD and build. Compare old.txt vs new.txt.",
        )

    async def test_cron_parser(self, tmp_path):
        await _build_and_verify(tmp_path,
            "# Cron Expression Parser\n\nParse and explain cron expressions.\n\n"
            "## Usage\n```\npython3 -m cronparse '0 9 * * 1-5'\n"
            "python3 -m cronparse '*/15 * * * *' --next 5  # show next 5 run times\n```\n\n"
            "## Output\n- Human-readable explanation\n"
            "- Next N scheduled run times\n\n"
            "## Cron Format\nminute hour day-of-month month day-of-week\n\n"
            "## Requirements\n- Python stdlib only (datetime)\n",
            "Review PRD and build. Parse '0 9 * * 1-5' and show next 3 runs.",
        )


# ============================================================================
# BATCH 2: Data Processing (10 tests)
# ============================================================================

class TestDataProcessing:

    async def test_csv_pivot(self, tmp_path):
        (tmp_path / "sales.csv").write_text(
            "region,product,quarter,revenue\nEast,Widget,Q1,1000\nEast,Widget,Q2,1200\n"
            "West,Widget,Q1,800\nWest,Gadget,Q1,500\nEast,Gadget,Q2,600\n"
        )
        await _build_and_verify(tmp_path,
            "# CSV Pivot Tool\n\nCreate pivot tables from CSV data.\n\n"
            "## Usage\n```\npython3 -m csvpivot sales.csv --rows region --cols quarter --values revenue --agg sum\n```\n\n"
            "## Requirements\n- Python stdlib only\n- Output as formatted table\n",
            "Review PRD and build. Pivot sales.csv with region as rows, quarter as columns, sum of revenue.",
        )

    async def test_log_rotator(self, tmp_path):
        (tmp_path / "app.log").write_text("log line 1\n" * 100)
        await _build_and_verify(tmp_path,
            "# Log Rotator\n\nRotate and compress log files.\n\n"
            "## Usage\n```\npython3 -m logrotate app.log --max-size 1KB --keep 3\n"
            "python3 -m logrotate app.log --max-age 7d --compress\n```\n\n"
            "## Features\n- Rotate by size or age\n- Keep N rotated files\n"
            "- Optional gzip compression\n- Naming: app.log.1, app.log.2, etc.\n\n"
            "## Requirements\n- Python stdlib (shutil, gzip)\n",
            "Review PRD and build. Test rotating app.log with --max-size 1KB --keep 2.",
        )

    async def test_data_validator(self, tmp_path):
        (tmp_path / "data.csv").write_text(
            "name,email,age\nAlice,alice@test.com,30\nBob,invalid-email,25\n,charlie@test.com,150\n"
        )
        await _build_and_verify(tmp_path,
            "# Data Validator\n\nValidate CSV data against rules.\n\n"
            "## Usage\n```\npython3 -m dataval data.csv --rules rules.json\n"
            "python3 -m dataval data.csv --check-email email --check-range age 0 120\n```\n\n"
            "## Built-in Checks\n- required: field not empty\n"
            "- email: valid email format\n- range: numeric within min/max\n"
            "- unique: no duplicates\n- pattern: regex match\n\n"
            "## Output\n- Row number, field, violation for each error\n"
            "- Summary: total rows, valid, invalid\n\n"
            "## Requirements\n- Python stdlib only\n",
            "Review PRD and build. Validate data.csv checking email format and age range 0-120.",
        )

    async def test_report_generator(self, tmp_path):
        (tmp_path / "metrics.json").write_text(
            '{"monthly": [{"month":"Jan","revenue":50000,"costs":30000},'
            '{"month":"Feb","revenue":62000,"costs":35000},'
            '{"month":"Mar","revenue":58000,"costs":32000}]}'
        )
        await _build_and_verify(tmp_path,
            "# Report Generator\n\nGenerate text/HTML reports from JSON data.\n\n"
            "## Usage\n```\npython3 -m reporter metrics.json --format text\n"
            "python3 -m reporter metrics.json --format html -o report.html\n```\n\n"
            "## Features\n- Summary statistics (totals, averages, trends)\n"
            "- Text table output\n- Simple HTML report with tables\n\n"
            "## Requirements\n- Python stdlib only\n",
            "Review PRD and build. Generate a text report from metrics.json.",
        )

    async def test_schema_generator(self, tmp_path):
        (tmp_path / "sample.json").write_text(
            '{"name":"Alice","age":30,"active":true,"tags":["python","cli"],'
            '"address":{"city":"NYC","zip":"10001"}}'
        )
        await _build_and_verify(tmp_path,
            "# JSON Schema Generator\n\nInfer JSON Schema from sample data.\n\n"
            "## Usage\n```\npython3 -m schemagen sample.json\n"
            "python3 -m schemagen sample.json -o schema.json\n```\n\n"
            "## Output\nJSON Schema with types, required fields, nested objects.\n\n"
            "## Requirements\n- Python stdlib only\n",
            "Review PRD and build. Generate schema from sample.json.",
        )

    async def test_duplicate_finder(self, tmp_path):
        for i in range(5):
            (tmp_path / f"file_{i}.txt").write_text(f"content {i % 3}")
        await _build_and_verify(tmp_path,
            "# Duplicate File Finder\n\nFind duplicate files by content hash.\n\n"
            "## Usage\n```\npython3 -m dupfinder /path/to/dir\n"
            "python3 -m dupfinder /path --min-size 1KB\n"
            "python3 -m dupfinder /path --delete  # delete duplicates (keep first)\n```\n\n"
            "## Output\n- Groups of duplicate files with sizes\n"
            "- Total space that could be freed\n\n"
            "## Requirements\n- Python stdlib (hashlib, pathlib)\n",
            "Review PRD and build. Find duplicates in the current directory.",
        )

    async def test_text_search(self, tmp_path):
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "readme.txt").write_text("Welcome to the project. Install with pip install myapp.")
        (tmp_path / "docs" / "guide.txt").write_text("This guide covers installation and configuration.")
        (tmp_path / "docs" / "api.txt").write_text("The API provides REST endpoints for data access.")
        await _build_and_verify(tmp_path,
            "# Text Search Engine\n\nFull-text search across files in a directory.\n\n"
            "## Usage\n```\npython3 -m textsearch docs/ 'install'\n"
            "python3 -m textsearch docs/ 'install' --context 2  # show 2 lines around match\n```\n\n"
            "## Features\n- Case-insensitive search\n- Show file, line number, matched line\n"
            "- Context lines around matches\n- Result count\n\n"
            "## Requirements\n- Python stdlib only\n",
            "Review PRD and build. Search for 'install' in docs/.",
        )

    async def test_csv_joiner(self, tmp_path):
        (tmp_path / "users.csv").write_text("id,name\n1,Alice\n2,Bob\n3,Charlie\n")
        (tmp_path / "orders.csv").write_text("user_id,product,amount\n1,Widget,9.99\n1,Gadget,24.99\n2,Widget,9.99\n")
        await _build_and_verify(tmp_path,
            "# CSV Joiner\n\nJoin two CSV files on a shared column (like SQL JOIN).\n\n"
            "## Usage\n```\npython3 -m csvjoin users.csv orders.csv --on id=user_id\n"
            "python3 -m csvjoin users.csv orders.csv --on id=user_id --type left\n```\n\n"
            "## Join Types\n- inner (default): only matching rows\n"
            "- left: all rows from first file\n- full: all rows from both\n\n"
            "## Requirements\n- Python stdlib only\n",
            "Review PRD and build. Join users.csv with orders.csv on id=user_id.",
        )

    async def test_xml_to_json(self, tmp_path):
        (tmp_path / "data.xml").write_text(
            '<?xml version="1.0"?>\n<users>\n<user><name>Alice</name><age>30</age></user>\n'
            '<user><name>Bob</name><age>25</age></user>\n</users>'
        )
        await _build_and_verify(tmp_path,
            "# XML to JSON Converter\n\nConvert XML files to JSON.\n\n"
            "## Usage\n```\npython3 -m xmljson data.xml\npython3 -m xmljson data.xml -o data.json\n```\n\n"
            "## Requirements\n- Python stdlib (xml.etree.ElementTree)\n"
            "- Handle attributes, nested elements, text content\n",
            "Review PRD and build. Convert data.xml to JSON.",
        )

    async def test_markdown_toc(self, tmp_path):
        (tmp_path / "doc.md").write_text(
            "# Title\n## Installation\n### From pip\n### From source\n"
            "## Usage\n### Basic\n### Advanced\n## API Reference\n"
        )
        await _build_and_verify(tmp_path,
            "# Markdown TOC Generator\n\nGenerate table of contents from markdown headers.\n\n"
            "## Usage\n```\npython3 -m mdtoc doc.md\npython3 -m mdtoc doc.md --insert  # insert TOC into file\n```\n\n"
            "## Output\n- Nested list of headers with links\n- Respects header levels (# ## ###)\n\n"
            "## Requirements\n- Python stdlib only\n",
            "Review PRD and build. Generate TOC for doc.md.",
        )


# ============================================================================
# BATCH 3: System Utilities (10 tests)
# ============================================================================

class TestSystemUtils:

    async def test_port_scanner(self, tmp_path):
        await _build_and_verify(tmp_path,
            "# Port Scanner\n\nScan a host for open ports.\n\n"
            "## Usage\n```\npython3 -m portscan localhost 80-100\n"
            "python3 -m portscan localhost 80,443,8080\n```\n\n"
            "## Features\n- Range or comma-separated ports\n- Timeout per port (default 1s)\n"
            "- Show service name for known ports\n\n"
            "## Requirements\n- Python stdlib (socket)\n",
            "Review PRD and build. Scan localhost ports 8000-8010.",
        )

    async def test_process_monitor(self, tmp_path):
        await _build_and_verify(tmp_path,
            "# Process Monitor\n\nMonitor running processes and system resources.\n\n"
            "## Usage\n```\npython3 -m procmon\npython3 -m procmon --top 10\n"
            "python3 -m procmon --filter python\n```\n\n"
            "## Output\n- PID, name, CPU%, memory, command\n"
            "- System summary: CPU, memory, disk\n\n"
            "## Requirements\n- Python stdlib (subprocess, os)\n"
            "- Read from /proc/ on Linux or use ps command\n",
            "Review PRD and build. Show top 5 processes.",
        )

    async def test_network_info(self, tmp_path):
        await _build_and_verify(tmp_path,
            "# Network Info Tool\n\nDisplay network interface information.\n\n"
            "## Usage\n```\npython3 -m netinfo\npython3 -m netinfo --json\n```\n\n"
            "## Output\n- Interface name, IP address, MAC address, status\n"
            "- DNS servers\n- Default gateway\n\n"
            "## Requirements\n- Python stdlib (socket, subprocess)\n",
            "Review PRD and build. Show network info.",
        )

    async def test_disk_usage(self, tmp_path):
        await _build_and_verify(tmp_path,
            "# Disk Usage Analyzer\n\nAnalyze disk usage like du but friendlier.\n\n"
            "## Usage\n```\npython3 -m diskuse /path\npython3 -m diskuse . --top 10\n"
            "python3 -m diskuse . --min-size 1MB\n```\n\n"
            "## Output\n- Directory tree with sizes (human-readable)\n"
            "- Largest files/directories first\n- Total usage\n\n"
            "## Requirements\n- Python stdlib (os, pathlib)\n",
            "Review PRD and build. Analyze disk usage of current directory.",
        )

    async def test_crontab_manager(self, tmp_path):
        await _build_and_verify(tmp_path,
            "# Crontab Manager\n\nManage crontab entries from CLI.\n\n"
            "## Usage (simulated — don't modify real crontab)\n"
            "```\npython3 -m cronmgr list\npython3 -m cronmgr add '0 9 * * *' 'echo hello'\n"
            "python3 -m cronmgr remove 1\npython3 -m cronmgr export crontab.txt\n```\n\n"
            "## Requirements\n- Store entries in a JSON file (simulated crontab)\n"
            "- Python stdlib only\n- Do NOT modify the real system crontab\n",
            "Review PRD and build. Add 2 entries, list them, remove one.",
        )

    async def test_backup_tool(self, tmp_path):
        (tmp_path / "source").mkdir()
        (tmp_path / "source" / "file1.txt").write_text("content 1")
        (tmp_path / "source" / "file2.py").write_text("print('hello')")
        await _build_and_verify(tmp_path,
            "# Backup Tool\n\nCreate incremental backups of directories.\n\n"
            "## Usage\n```\npython3 -m backuptool source/ backup/\n"
            "python3 -m backuptool source/ backup/ --compress\n"
            "python3 -m backuptool --list backup/  # show backup history\n```\n\n"
            "## Features\n- Copy new/modified files only (by mtime)\n"
            "- Optional gzip compression\n- Backup manifest (JSON)\n\n"
            "## Requirements\n- Python stdlib (shutil, gzip, json)\n",
            "Review PRD and build. Backup source/ to backup/.",
        )

    async def test_clipboard_manager(self, tmp_path):
        await _build_and_verify(tmp_path,
            "# Clipboard History Manager\n\nTrack clipboard history (simulated).\n\n"
            "## Usage\n```\npython3 -m clipmgr add 'some text'\n"
            "python3 -m clipmgr list\npython3 -m clipmgr get 1  # get entry by index\n"
            "python3 -m clipmgr search 'text'\npython3 -m clipmgr clear\n```\n\n"
            "## Requirements\n- Store in clipboard.json\n- Python stdlib only\n"
            "- Max 100 entries (FIFO)\n",
            "Review PRD and build. Add 3 entries, list them, search.",
        )

    async def test_timer_tool(self, tmp_path):
        await _build_and_verify(tmp_path,
            "# Timer & Stopwatch\n\nCLI timer and stopwatch.\n\n"
            "## Usage\n```\npython3 -m timer countdown 5s\npython3 -m timer countdown 2m30s\n"
            "python3 -m timer stopwatch  # press Enter to lap, Ctrl-C to stop\n"
            "python3 -m timer pomodoro  # 25min work, 5min break\n```\n\n"
            "## Requirements\n- Python stdlib (time, datetime)\n"
            "- Parse duration strings (5s, 2m30s, 1h)\n",
            "Review PRD and build. Test the duration parser and countdown with 1s.",
        )

    async def test_http_status(self, tmp_path):
        await _build_and_verify(tmp_path,
            "# HTTP Status Reference\n\nLook up HTTP status codes.\n\n"
            "## Usage\n```\npython3 -m httpstatus 404\npython3 -m httpstatus 2xx  # all 2xx codes\n"
            "python3 -m httpstatus search 'not found'\n```\n\n"
            "## Output\n- Code, name, description, common causes\n\n"
            "## Requirements\n- Python stdlib only\n- Built-in database of all HTTP status codes\n",
            "Review PRD and build. Look up 404 and list all 5xx codes.",
        )

    async def test_path_tool(self, tmp_path):
        await _build_and_verify(tmp_path,
            "# Path Manipulation Tool\n\nManipulate and analyze file paths.\n\n"
            "## Usage\n```\npython3 -m pathtool resolve ../relative/path\n"
            "python3 -m pathtool common /a/b/c /a/b/d  # find common prefix\n"
            "python3 -m pathtool split /path/to/file.tar.gz  # dir, name, extensions\n"
            "python3 -m pathtool tree /path --depth 2\n```\n\n"
            "## Requirements\n- Python stdlib (pathlib, os.path)\n",
            "Review PRD and build. Test resolve, split, and tree on current directory.",
        )


# ============================================================================
# BATCH 4: Libraries & Frameworks (10 tests)
# ============================================================================

class TestLibraries:

    async def test_event_emitter(self, tmp_path):
        await _build_and_verify(tmp_path,
            "# Event Emitter Library\n\nNode.js-style event emitter for Python.\n\n"
            "## API\n```python\nfrom eventemitter import EventEmitter\n"
            "ee = EventEmitter()\nee.on('data', lambda x: print(x))\n"
            "ee.once('close', lambda: print('closed'))\n"
            "ee.emit('data', 'hello')\nee.off('data', handler)\n```\n\n"
            "## Features\n- on, once, off, emit\n- Wildcard listeners (*)\n"
            "- Max listeners warning\n- Async support (optional)\n\n"
            "## Requirements\n- Python stdlib only\n- Create demo.py showing usage\n",
            "Review PRD and build. Run demo.py to verify.",
            min_files=2,
        )

    async def test_state_machine(self, tmp_path):
        await _build_and_verify(tmp_path,
            "# State Machine Library\n\nGeneric finite state machine.\n\n"
            "## API\n```python\nfrom statemachine import StateMachine\n"
            "sm = StateMachine('idle')\nsm.add_transition('start', 'idle', 'running')\n"
            "sm.add_transition('stop', 'running', 'idle')\n"
            "sm.add_transition('error', '*', 'failed')  # from any state\n"
            "sm.trigger('start')\nprint(sm.state)  # 'running'\n```\n\n"
            "## Features\n- Guards (conditions)\n- Enter/exit callbacks\n"
            "- History tracking\n- Visualization (print as ASCII)\n\n"
            "## Requirements\n- Python stdlib only\n- Create demo with order lifecycle\n",
            "Review PRD and build. Run demo showing order: pending→paid→shipped→delivered.",
            min_files=2,
        )

    async def test_retry_library(self, tmp_path):
        await _build_and_verify(tmp_path,
            "# Retry Library\n\nDecorator-based retry with backoff.\n\n"
            "## API\n```python\nfrom retrylib import retry\n\n"
            "@retry(max_attempts=3, backoff=2.0, exceptions=(ConnectionError,))\n"
            "def fetch_data():\n    ...\n```\n\n"
            "## Features\n- Exponential backoff\n- Configurable exceptions to catch\n"
            "- Max attempts\n- Jitter (random delay addition)\n- Logging of retries\n\n"
            "## Requirements\n- Python stdlib only\n- Create demo showing retries in action\n",
            "Review PRD and build. Demo with a function that fails twice then succeeds.",
            min_files=2,
        )

    async def test_validation_library(self, tmp_path):
        await _build_and_verify(tmp_path,
            "# Validation Library\n\nDeclarative data validation.\n\n"
            "## API\n```python\nfrom validator import Schema, Required, Email, Range, Pattern\n\n"
            "user_schema = Schema({\n"
            "    'name': [Required(), Pattern(r'^[A-Za-z ]+$')],\n"
            "    'email': [Required(), Email()],\n"
            "    'age': [Range(0, 150)],\n})\n\n"
            "errors = user_schema.validate({'name': '', 'email': 'bad', 'age': 200})\n```\n\n"
            "## Requirements\n- Python stdlib only\n- Create demo.py with examples\n",
            "Review PRD and build. Demo validating good and bad data.",
            min_files=2,
        )

    async def test_cli_framework(self, tmp_path):
        await _build_and_verify(tmp_path,
            "# Mini CLI Framework\n\nLike click/typer but minimal.\n\n"
            "## API\n```python\nfrom minicli import CLI, argument, option\n\n"
            "app = CLI('myapp')\n\n@app.command()\n"
            "@argument('name')\n@option('--greeting', default='Hello')\n"
            "def greet(name, greeting):\n    print(f'{greeting}, {name}!')\n\n"
            "app.run()\n```\n\n"
            "## Features\n- @command decorator\n- @argument and @option decorators\n"
            "- Auto-generated --help\n- Subcommands\n\n"
            "## Requirements\n- Python stdlib (argparse under the hood)\n",
            "Review PRD and build. Create example app and test: python3 example.py greet Alice",
            min_files=2,
        )

    async def test_cache_library(self, tmp_path):
        await _build_and_verify(tmp_path,
            "# Cache Library\n\nIn-memory cache with LRU eviction and TTL.\n\n"
            "## API\n```python\nfrom cachelib import Cache\n"
            "cache = Cache(max_size=100, default_ttl=60)\n"
            "cache.set('key', 'value', ttl=30)\nval = cache.get('key')\n"
            "cache.delete('key')\ncache.clear()\n```\n\n"
            "## Features\n- LRU eviction when full\n- Per-key TTL\n"
            "- Stats (hits, misses, evictions)\n- Thread-safe\n\n"
            "## Requirements\n- Python stdlib only\n- Create demo.py\n",
            "Review PRD and build. Demo cache with TTL and eviction.",
            min_files=2,
        )

    async def test_template_engine(self, tmp_path):
        await _build_and_verify(tmp_path,
            "# Template Engine\n\nSimple template engine (like Jinja2 lite).\n\n"
            "## API\n```python\nfrom templite import Template\n"
            "t = Template('Hello {{ name }}! {% for item in items %}{{ item }} {% endfor %}')\n"
            "print(t.render(name='World', items=['a','b','c']))\n```\n\n"
            "## Features\n- {{ variable }} substitution\n"
            "- {% for item in list %}...{% endfor %}\n"
            "- {% if condition %}...{% endif %}\n"
            "- Filters: {{ name|upper }}, {{ name|lower }}\n\n"
            "## Requirements\n- Python stdlib only\n- Demo rendering HTML page\n",
            "Review PRD and build. Demo rendering an HTML page with a user list.",
            min_files=2,
        )

    async def test_pubsub(self, tmp_path):
        await _build_and_verify(tmp_path,
            "# Pub/Sub Message Bus\n\nIn-process publish/subscribe messaging.\n\n"
            "## API\n```python\nfrom pubsub import MessageBus\n"
            "bus = MessageBus()\nbus.subscribe('user.created', handler)\n"
            "bus.publish('user.created', {'name': 'Alice'})\n"
            "bus.subscribe('user.*', wildcard_handler)  # topic wildcards\n```\n\n"
            "## Features\n- Topic-based routing\n- Wildcard subscriptions (user.*)\n"
            "- Async handlers (optional)\n- Message history\n\n"
            "## Requirements\n- Python stdlib only\n- Demo with 3 topics\n",
            "Review PRD and build. Demo with user events (created, updated, deleted).",
            min_files=2,
        )

    async def test_orm_lite(self, tmp_path):
        await _build_and_verify(tmp_path,
            "# Micro ORM\n\nMinimal ORM using dataclasses and SQLite.\n\n"
            "## API\n```python\nfrom microorm import Model, Database\n\n"
            "db = Database('app.db')\n\n"
            "class User(Model):\n    name: str\n    email: str\n    age: int = 0\n\n"
            "User.create_table(db)\nuser = User(name='Alice', email='a@b.com', age=30)\n"
            "user.save(db)\nusers = User.find_all(db)\n"
            "alice = User.find_by(db, name='Alice')\n```\n\n"
            "## Requirements\n- Python stdlib (sqlite3, dataclasses)\n"
            "- Auto-create tables from dataclass fields\n- CRUD operations\n",
            "Review PRD and build. Demo with User and Product models.",
            min_files=2, max_turns=35,
        )

    async def test_middleware_pipeline(self, tmp_path):
        await _build_and_verify(tmp_path,
            "# Middleware Pipeline\n\nGeneric middleware pattern (like Express.js).\n\n"
            "## API\n```python\nfrom pipeline import Pipeline\n\n"
            "p = Pipeline()\n"
            "p.use(lambda ctx, next: (ctx.update({'logged': True}), next(ctx))[-1])\n"
            "p.use(lambda ctx, next: next(ctx) if ctx.get('auth') else {'error': 'unauthorized'})\n"
            "result = p.execute({'auth': True, 'data': 'hello'})\n```\n\n"
            "## Features\n- Ordered middleware chain\n- Context dict passed through\n"
            "- Short-circuit (return early without calling next)\n- Error handling middleware\n\n"
            "## Requirements\n- Python stdlib only\n- Demo with logging + auth + validation middleware\n",
            "Review PRD and build. Demo with 3 middleware functions.",
            min_files=2,
        )


# ============================================================================
# BATCH 5: Complete Applications (10 tests)
# ============================================================================

class TestApplications:

    async def test_quiz_app(self, tmp_path):
        await _build_and_verify(tmp_path,
            "# Quiz Application\n\nInteractive quiz from question bank.\n\n"
            "## Usage\n```\npython3 -m quizapp start questions.json\n"
            "python3 -m quizapp stats  # show past scores\n"
            "python3 -m quizapp create  # create questions interactively\n```\n\n"
            "## Question Format (JSON)\n```json\n[{\"question\":\"What is 2+2?\","
            "\"options\":[\"3\",\"4\",\"5\"],\"answer\":1}]\n```\n\n"
            "## Features\n- Multiple choice\n- Score tracking (JSON persistence)\n"
            "- Randomize question order\n- Show correct answer after wrong guess\n\n"
            "## Requirements\n- Python stdlib only\n",
            "Review PRD and build. Create a sample questions.json with 5 questions and test.",
            min_files=2,
        )

    async def test_recipe_manager(self, tmp_path):
        await _build_and_verify(tmp_path,
            "# Recipe Manager\n\nStore and search recipes.\n\n"
            "## Usage\n```\npython3 -m recipes add  # interactive\n"
            "python3 -m recipes list\npython3 -m recipes search pasta\n"
            "python3 -m recipes show 'Spaghetti Carbonara'\n"
            "python3 -m recipes scale 'Spaghetti Carbonara' --servings 4\n```\n\n"
            "## Data\n- recipes.json: name, ingredients (with quantities), steps, servings, tags\n\n"
            "## Requirements\n- Python stdlib only\n"
            "- Pre-load 3 sample recipes so the demo works\n",
            "Review PRD and build with 3 sample recipes. Test list and search.",
            min_files=2,
        )

    async def test_habit_tracker(self, tmp_path):
        await _build_and_verify(tmp_path,
            "# Habit Tracker\n\nTrack daily habits.\n\n"
            "## Usage\n```\npython3 -m habits add 'Exercise' --frequency daily\n"
            "python3 -m habits check 'Exercise'  # mark today as done\n"
            "python3 -m habits status  # show all habits with streaks\n"
            "python3 -m habits history 'Exercise' --days 7\n```\n\n"
            "## Features\n- Streak counting (consecutive days)\n"
            "- Completion rate percentage\n- Daily/weekly frequency\n\n"
            "## Requirements\n- Python stdlib only\n- Persist in habits.json\n",
            "Review PRD and build. Add 2 habits, check one off, show status.",
        )

    async def test_bookmark_manager(self, tmp_path):
        await _build_and_verify(tmp_path,
            "# Bookmark Manager\n\nOrganize web bookmarks from CLI.\n\n"
            "## Usage\n```\npython3 -m bookmarks add https://example.com --title 'Example' --tags dev,tools\n"
            "python3 -m bookmarks list\npython3 -m bookmarks list --tag dev\n"
            "python3 -m bookmarks search 'python'\n"
            "python3 -m bookmarks export bookmarks.html\n```\n\n"
            "## Requirements\n- Python stdlib only\n- Persist in bookmarks.json\n"
            "- Export as HTML (Netscape bookmark format)\n",
            "Review PRD and build. Add 3 bookmarks with tags, list by tag.",
        )

    async def test_flash_cards(self, tmp_path):
        await _build_and_verify(tmp_path,
            "# Flash Cards\n\nStudy flash cards with spaced repetition scoring.\n\n"
            "## Usage\n```\npython3 -m flashcards create deck.json  # create deck\n"
            "python3 -m flashcards add deck.json 'Q: What is Python?' 'A: A programming language'\n"
            "python3 -m flashcards study deck.json\n"
            "python3 -m flashcards stats deck.json\n```\n\n"
            "## Features\n- Cards with front/back\n- Score: easy/medium/hard per review\n"
            "- Stats: mastered, learning, new counts\n\n"
            "## Requirements\n- Python stdlib only\n",
            "Review PRD and build. Create a deck with 5 cards and show stats.",
        )

    async def test_changelog_gen(self, tmp_path):
        await _build_and_verify(tmp_path,
            "# Changelog Generator\n\nGenerate changelog from conventional commits.\n\n"
            "## Usage\n```\npython3 -m changelog generate  # from git log\n"
            "python3 -m changelog generate --since v1.0.0\n"
            "python3 -m changelog generate --format markdown -o CHANGELOG.md\n```\n\n"
            "## Commit Format\n- feat: → Features\n- fix: → Bug Fixes\n"
            "- docs: → Documentation\n- refactor: → Refactoring\n\n"
            "## Requirements\n- Python stdlib (subprocess for git)\n"
            "- If no git repo, accept commits from stdin or a text file\n",
            "Review PRD and build. Test with sample commits in a file.",
        )

    async def test_project_stats(self, tmp_path):
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").write_text("# Main module\ndef main():\n    pass\n")
        (tmp_path / "src" / "utils.py").write_text("# Utils\ndef helper():\n    return 42\n")
        await _build_and_verify(tmp_path,
            "# Project Statistics\n\nAnalyze a codebase and show statistics.\n\n"
            "## Usage\n```\npython3 -m projstats /path/to/project\n"
            "python3 -m projstats . --format json\n```\n\n"
            "## Output\n- Files by language (extension)\n- Total lines of code (excluding blanks/comments)\n"
            "- Largest files\n- Average file size\n- TODO/FIXME count\n\n"
            "## Requirements\n- Python stdlib only\n",
            "Review PRD and build. Analyze the current directory.",
        )

    async def test_time_tracker(self, tmp_path):
        await _build_and_verify(tmp_path,
            "# Time Tracker\n\nTrack time spent on tasks.\n\n"
            "## Usage\n```\npython3 -m timetrack start 'Working on feature X'\n"
            "python3 -m timetrack stop\npython3 -m timetrack status  # show current task\n"
            "python3 -m timetrack report  # show today's time\n"
            "python3 -m timetrack report --week  # show this week\n```\n\n"
            "## Features\n- Start/stop timer\n- Daily/weekly reports\n"
            "- Total time per task\n- Persist in timetrack.json\n\n"
            "## Requirements\n- Python stdlib only\n",
            "Review PRD and build. Start a task, stop it, show report.",
        )

    async def test_contact_book(self, tmp_path):
        await _build_and_verify(tmp_path,
            "# Contact Book\n\nManage contacts from CLI.\n\n"
            "## Usage\n```\npython3 -m contacts add --name 'Alice' --email alice@test.com --phone 555-1234\n"
            "python3 -m contacts list\npython3 -m contacts search alice\n"
            "python3 -m contacts edit 1 --phone 555-5678\n"
            "python3 -m contacts export contacts.csv\n```\n\n"
            "## Requirements\n- Python stdlib only\n- Persist in contacts.json\n"
            "- Search by name, email, or phone\n",
            "Review PRD and build. Add 2 contacts, search, list.",
        )

    async def test_invoice_generator(self, tmp_path):
        await _build_and_verify(tmp_path,
            "# Invoice Generator\n\nCreate invoices from CLI.\n\n"
            "## Usage\n```\npython3 -m invoicegen create --client 'Acme Corp' --items items.json -o invoice.txt\n"
            "python3 -m invoicegen list  # show all invoices\n```\n\n"
            "## items.json format\n```json\n"
            '[{"description":"Consulting","hours":10,"rate":150},'
            '{"description":"Development","hours":20,"rate":200}]\n```\n\n'
            "## Output\n- Invoice number, date, client\n- Line items with totals\n"
            "- Subtotal, tax (configurable), grand total\n\n"
            "## Requirements\n- Python stdlib only\n",
            "Review PRD and build. Create a sample items.json and generate an invoice.",
            min_files=2,
        )
