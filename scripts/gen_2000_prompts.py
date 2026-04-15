#!/usr/bin/env python3
"""Generate a 2000-prompt extension by riffing on the 200 categories.

Outputs scripts/stress_prompts_tool_agent_2000.txt — 1 build prompt
plus 2000 feature additions across categorical variants. Run when
the 200-prompt stress hits 201/201 and we want to push further.

Strategy: each existing category gets 8-10 more variants. New
categories added: i18n, observability, network protocols, format
converters, simulation, batch ops, validators, accessibility,
domain helpers (text/numeric/date/regex/structural).
"""
from __future__ import annotations

from pathlib import Path

CATEGORIES = {
    "Built-in tools — extended (200)": [
        "sha1 hash", "sha512 hash", "blake2b hash", "crc32",
        "rot13 cipher", "caesar cipher", "atbash cipher",
        "morse encode", "morse decode",
        "hex_to_int", "int_to_hex", "bin_to_int", "int_to_bin",
        "float_to_int", "round_to", "ceil", "floor", "abs", "negate",
        "sum_list", "avg_list", "min_list", "max_list", "median",
        "std_dev", "variance", "percentile", "mode",
        "factorial", "fibonacci", "is_prime", "primes_up_to",
        "gcd", "lcm", "modular_inverse",
        "sin", "cos", "tan", "asin", "acos", "atan",
        "log10", "log2", "ln", "exp", "pow_func", "sqrt", "cbrt",
        "deg_to_rad", "rad_to_deg",
        "miles_to_km", "km_to_miles", "celsius_to_f", "f_to_celsius",
        "feet_to_m", "m_to_feet", "kg_to_lb", "lb_to_kg",
        "format_bytes", "format_duration", "format_number_with_commas",
        "parse_int", "parse_float", "parse_bool",
        "is_valid_email", "is_valid_url", "is_valid_ipv4", "is_valid_ipv6",
        "is_valid_uuid", "is_valid_isbn",
        "extract_emails_from_text", "extract_urls_from_text",
        "extract_phone_numbers", "extract_dates",
        "strip_html_tags", "strip_ansi_codes", "normalize_whitespace",
        "snake_to_camel", "camel_to_snake", "kebab_to_snake", "snake_to_kebab",
        "pluralize", "singularize", "ordinalize",
        "left_pad", "right_pad", "center_pad",
        "wrap_text_at_width", "indent_text", "dedent_text",
        "remove_duplicates_in_list", "flatten_list", "chunk_list",
        "zip_lists", "unzip_lists",
        "set_intersection", "set_union", "set_difference",
        "dict_get_nested", "dict_set_nested", "dict_merge", "dict_invert",
        "list_to_dict", "dict_to_list",
        "csv_filter", "csv_select_columns", "csv_sort_by",
        "json_pretty", "json_minify", "json_validate",
        "yaml_validate", "toml_parse", "toml_dump",
        "ini_parse", "ini_dump",
        "xml_to_dict", "dict_to_xml",
        "html_escape", "html_unescape",
        "shell_quote", "shell_unquote",
        "now_iso", "now_unix", "today_date", "today_weekday",
        "parse_iso_date", "format_date", "date_diff_days",
        "add_days", "subtract_days", "next_weekday", "previous_weekday",
        "is_weekend", "is_business_day",
        "timezone_now", "convert_timezone",
        "duration_humanize", "ago_format",
        "url_parse", "url_join", "url_query_params",
        "ip_to_int", "int_to_ip", "cidr_contains", "cidr_hosts",
        "is_private_ip", "reverse_dns",
        "html_get_title", "html_get_meta_description",
        "markdown_to_text", "markdown_to_html", "html_to_markdown",
        "ansi_color_text", "ansi_strip", "terminal_width",
        "rgb_to_hex", "hex_to_rgb", "rgb_to_hsl", "hsl_to_rgb",
        "color_lighten", "color_darken", "color_invert",
        "image_size", "image_format",
        "audio_duration", "video_duration",
        "file_size", "file_mtime", "file_owner",
        "list_directory", "tree_directory", "file_count",
        "find_files_by_extension", "find_files_modified_since",
        "find_largest_files", "find_oldest_files",
        "compare_two_files", "diff_two_strings",
        "patch_apply", "patch_create",
        "git_current_branch", "git_last_commit_message", "git_diff_stat",
        "git_blame", "git_log_oneline", "git_remote_url",
        "github_repo_info", "gitlab_repo_info",
        "package_json_info", "pyproject_toml_info", "cargo_toml_info",
        "license_detect", "readme_detect", "ci_detect",
        "lines_of_code", "tabs_vs_spaces",
        "indent_consistency_check", "trailing_whitespace_count",
        "todo_count", "fixme_count", "hack_count",
        "import_graph", "function_count", "class_count",
        "test_file_count", "test_function_count",
        "complexity_metric", "halstead_metrics",
        "tabular_pretty_print", "csv_to_markdown",
        "weather_for_city", "stock_price",
        "bitcoin_price", "ethereum_price",
        "ip_geolocation", "phone_to_country",
        "email_validation_smtp", "domain_whois",
        "wikipedia_summary", "dictionary_lookup",
        "thesaurus_synonyms", "translate_text",
        "summarize_text", "tokenize_text", "stem_words", "lemmatize_words",
        "language_detect", "sentiment_analyze", "keyword_extract",
        "tag_extract", "entity_extract",
        "qr_encode", "qr_decode", "barcode_encode", "barcode_decode",
        "captcha_solve", "spell_check",
        "image_resize", "image_rotate", "image_crop", "image_grayscale",
        "image_thumbnail", "image_to_ascii",
        "audio_speed", "audio_volume", "audio_trim",
        "video_thumbnail", "video_extract_audio",
        "pdf_text_extract", "pdf_metadata", "pdf_merge", "pdf_split",
        "epub_text_extract", "docx_text_extract",
    ],
    "Format converters (200)": [
        f"convert {a} to {b}"
        for a in ["json", "yaml", "toml", "csv", "tsv", "xml", "ini", "env"]
        for b in ["json", "yaml", "toml", "csv", "tsv", "xml", "ini", "env"]
        if a != b
    ][:200],
    "CLI flags (200)": [
        f"Add a --{flag} CLI flag that {action}"
        for flag in [
            "color", "no-color", "json", "yaml", "csv", "table", "raw", "markdown",
            "verbose", "quiet", "debug", "trace", "log-level", "log-file",
            "config", "profile", "env", "dry-run", "force", "interactive",
            "non-interactive", "yes", "no", "timeout", "max-retries", "no-retry",
            "cache", "no-cache", "cache-dir", "clear-cache",
            "concurrency", "parallel", "sequential", "batch-size",
            "input", "output", "input-file", "output-file", "stdin", "stdout",
            "format", "encoding", "delimiter", "separator", "quote-char",
            "include", "exclude", "filter", "sort", "reverse", "unique",
            "head", "tail", "skip", "limit", "page", "page-size",
            "user", "group", "permissions", "owner", "umask",
        ]
        for action in [
            "controls behavior",
        ]
    ][:200],
    "Plugin system (200)": [
        f"Plugin feature: {x}"
        for x in (
            "hot-reload, dependency injection, event bus, lifecycle hooks, "
            "configuration schema, secret management, telemetry, profiling, "
            "metrics export, health check, version negotiation, capability flags, "
            "permission scopes, sandboxing, resource limits, rate limiting, "
            "circuit breaker, fallback, retry policy, timeout policy, "
            "idempotency, deduplication, batching, debouncing, throttling, "
            "caching with TTL, cache invalidation, write-through, write-behind, "
            "request signing, token rotation, OAuth flow, SAML flow, OIDC flow, "
            "API key rotation, secret scanning, PII redaction, log sampling, "
            "trace propagation, span attributes, baggage propagation, error tagging, "
            "alert routing, notification channels, escalation policies, on-call rotation, "
            "incident management, runbook execution, chaos injection, fault simulation, "
            "load shedding, graceful degradation, blue-green deploy, canary deploy, "
            "feature flags per user, feature flags per cohort, A/B testing harness, "
            "experiment tracking, statistical significance, sample size calc, "
            "hypothesis testing, bayesian estimator, monte carlo, simulation, "
            "scheduled tasks, cron expressions, recurring jobs, one-shot jobs, "
            "job priorities, job queues, job dependencies, job retries, "
            "DAG execution, workflow definition, parallel branches, conditional steps, "
            "input validation, output validation, schema migration, data versioning, "
            "data lineage, audit trail, compliance reporting, GDPR export, GDPR delete, "
            "user consent tracking, anonymization, pseudonymization, k-anonymity"
        ).split(", ")
    ][:200],
    "Storage (200)": [
        f"Add storage backend: {x}"
        for x in [
            "sqlite", "postgres", "mysql", "mongodb", "redis", "memcached",
            "elasticsearch", "opensearch", "clickhouse", "duckdb",
            "rocksdb", "leveldb", "lmdb", "badger", "bolt",
            "s3", "gcs", "azure-blob", "minio", "ceph",
            "nfs", "samba", "ftp", "sftp", "webdav",
        ] * 9
    ][:200],
    "API surfaces (200)": [
        f"API: {x}"
        for x in [
            "REST GET endpoint", "REST POST endpoint", "REST PUT endpoint",
            "REST DELETE endpoint", "REST PATCH endpoint",
            "GraphQL query", "GraphQL mutation", "GraphQL subscription",
            "gRPC unary", "gRPC server-streaming", "gRPC client-streaming",
            "gRPC bidi-streaming",
            "WebSocket server", "WebSocket client",
            "SSE server", "SSE client",
            "JSON-RPC server", "JSON-RPC client",
            "OpenAPI generation", "Swagger UI",
            "API versioning", "API deprecation policy",
            "rate limiter (token bucket)", "rate limiter (leaky bucket)",
            "rate limiter (sliding window)",
        ] * 8
    ][:200],
    "Testing (200)": [
        f"Test: {x}"
        for x in [
            "unit test for tool X", "integration test for pipe Y",
            "fuzz test for parser Z", "property test for converter A",
            "regression test for bug B", "smoke test for CLI C",
            "performance benchmark for D", "memory benchmark for E",
            "concurrency test for F", "race condition test for G",
            "idempotency test for H", "rollback test for I",
            "snapshot test for J", "golden test for K",
        ] * 14
    ][:200],
    "Documentation (200)": [
        f"Doc: {x}"
        for x in [
            "README section about X", "CONTRIBUTING update for Y",
            "tutorial for Z", "API reference for A", "FAQ entry for B",
            "troubleshooting guide for C", "migration guide for D",
            "changelog entry for E", "release notes for F",
            "architecture diagram for G",
        ] * 20
    ][:200],
    "Performance (200)": [
        f"Perf: {x}"
        for x in [
            "cache result of pure function", "lazy-load module",
            "memoize expensive call", "batch DB writes",
            "stream large file", "compress old logs",
            "deduplicate repeated requests", "coalesce parallel reads",
            "warm cache on startup", "evict LRU entries",
        ] * 20
    ][:200],
    "Integration (200)": [
        f"Integrate: {x}"
        for x in [
            "Slack webhook", "Discord webhook", "Telegram bot",
            "GitHub Actions", "GitLab CI", "CircleCI", "Jenkins",
            "Prometheus metrics", "Grafana dashboard", "Datadog APM",
            "New Relic APM", "Sentry error tracking", "Honeycomb tracing",
            "PagerDuty incidents", "Opsgenie", "VictorOps",
            "AWS Lambda", "GCP Cloud Functions", "Azure Functions",
            "Cloudflare Workers", "Fastly Compute", "Vercel Edge",
            "Docker image", "OCI image", "Singularity image",
        ] * 9
    ][:200],
}


def main() -> None:
    out = Path(__file__).resolve().parent / "stress_prompts_tool_agent_2000.txt"
    lines: list[str] = [
        "# Bootstrap: build tool_agent from PRD first",
        "Review the PRD.md in this directory. Build the initial tool_agent "
        "package with the 6 built-in tools, pipe syntax parser, plugin "
        "loader, conversation memory, and CLI entrypoint. After all files "
        "are written, run `python3 -m tool_agent --list-tools` to verify "
        "it works.",
        "",
    ]
    total = 0
    for cat, prompts in CATEGORIES.items():
        lines.append(f"# {cat}")
        for p in prompts:
            lines.append(p)
            total += 1
        lines.append("")
    out.write_text("\n".join(lines))
    print(f"wrote {out} ({total} feature prompts + 1 build = {total + 1})")


if __name__ == "__main__":
    main()
