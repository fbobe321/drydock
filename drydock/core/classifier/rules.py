"""Classification rules — pattern → bucket mapping.

Each rule is a regex + a target Bucket + a one-line suggested action.
Patterns are derived from MODEL_SHORTCOMINGS.md and TRIAGE_v1.md, plus
the admiral fire codes I've watched land in production over the last
several sessions.

Adding a new rule:
1. Pick a stable `pattern_id` ("loop:bash:heredoc", not "rule_47")
2. Pick the highest-confidence Bucket — secondaries go in `also`
3. Make `suggested_action` directly actionable for the dispatcher
   (e.g. "consider read-before-write enforcement on file_path X")

Rule order matters: classify_lines() returns the FIRST match. Put
specific patterns above generic catch-alls.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from drydock.core.classifier.signal import Bucket


@dataclass(frozen=True)
class ClassificationRule:
    pattern_id: str
    regex: re.Pattern[str]
    bucket: Bucket
    suggested_action: str
    confidence: float = 0.85
    also: tuple[Bucket, ...] = ()    # secondary buckets


def _r(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.IGNORECASE)


# Order: most specific first.
RULES: tuple[ClassificationRule, ...] = (
    # ----- HARNESS rules (tool plumbing, control flow, write/edit safety) -----
    ClassificationRule(
        pattern_id="harness:search_replace:not_found_loop",
        regex=_r(r"(retry_after_error[:\s]*search_replace|search_replace.*not\s+found.*\b(retry|3|4|5)x)"),
        bucket=Bucket.HARNESS,
        suggested_action="Tighten search_replace error message; embed file head on first failure.",
    ),
    ClassificationRule(
        pattern_id="harness:search_replace:identical_blocks",
        regex=_r(r"search.*replace.*(byte[-\s]*identical|same\s+block|already\s+correct)"),
        bucket=Bucket.HARNESS,
        suggested_action="Short-circuit identical SEARCH/REPLACE before _apply_blocks.",
    ),
    ClassificationRule(
        pattern_id="harness:bash:heredoc_loop",
        regex=_r(r"loop[:\s]*bash.*(cat\s*<<|here[-\s]*doc|EOF)"),
        bucket=Bucket.HARNESS,
        suggested_action="Detect cat-heredoc write pattern in bash loop-breaker; redirect to write_file.",
    ),
    ClassificationRule(
        pattern_id="harness:bash:escape_loop",
        regex=_r(r"loop[:\s]*bash.*(echo\s+-e|printf|\\\\n|\\\\t)"),
        bucket=Bucket.HARNESS,
        suggested_action="Add escape-sequence-loop detection in bash hint generator.",
    ),
    # ORDER MATTERS: the repeated-call loop has to match BEFORE the
    # invalid-regex rule below, because admiral's loop intervention
    # text contains the substring "retry_after_error:grep" too —
    # without this split, valid greps that just looped were getting
    # mis-classified as unescaped_pattern (4k+ false positives in
    # the dispatch queue, observed 2026-05-10).
    ClassificationRule(
        pattern_id="harness:grep:repeated_call_loop",
        regex=_r(
            r"retry_after_error[:\s]*grep[:\s]*"
            r".*\b(this\s+exact\s+call\s+to\s+`?grep`?\s+has\s+been\s+made"
            r"|matches[:\s]|same\s+arguments\s+that\s+errored)"
        ),
        bucket=Bucket.HARNESS,
        suggested_action="Add per-call dedup hint to grep tool result on Nth identical call; reuse search_replace dedup pattern.",
        confidence=0.85,
    ),
    ClassificationRule(
        pattern_id="harness:grep:unescaped_pattern",
        # Only true invalid-regex signals — the engine error text or an
        # explicit "invalid regex" mention. Bare `retry_after_error:grep`
        # without one of those signals goes to the loop rule above.
        regex=_r(r"(grep:\s+Unmatched|grep.*invalid\s+regex|grep:.*Unbalanced|grep:.*Trailing\s+backslash)"),
        bucket=Bucket.HARNESS,
        suggested_action="Validate regex in grep tool early; suggest re.escape() literal.",
    ),
    ClassificationRule(
        pattern_id="harness:tool:hallucinated_name",
        # Require the line to either name a specific hallucinated tool or
        # quote the format.py suppression-redirect message. The old regex
        # `(unknown\s+tool|hallucinated.*tool)` matched any narrative
        # mention of "hallucinated tools" — autonomous_review.log summary
        # entries like "addressed thinking_stall, bash loops, hallucinated
        # tools" fired this 48× during the 2026-05-11→14 window with no
        # real hallucinated call behind them.
        regex=_r(
            # 'unknown tool: <name>' — require colon then an identifier
            r"(unknown\s+tool:\s*[\w_.-]{3,}"
            # 'hallucinated tool '<name>'' — require QUOTED name. Excludes
            # narrative phrases like "hallucinated tool name to ..." that
            # leaked through the prior regex.
            r"|hallucinated\s+tool\s+['\"][\w_.-]+['\"]"
            # format.py redirect-message signature (verbatim) — em-dash + tail
            r"|'[^']{2,40}'\s+does\s+not\s+exist\s+—\s+do\s+not\s+call"
            # admiral / detectors narrative phrasing
            r"|model\s+invented\s+(?:a\s+)?tool\s+['\"][\w_.-]+['\"])"
        ),
        bucket=Bucket.HARNESS,
        suggested_action="Add the hallucinated tool name to _IGNORE_TOOLS suppression list.",
    ),
    ClassificationRule(
        pattern_id="harness:write_file:dedup_attempted",
        regex=_r(r"(dedup.*write|identical[-\s]*content.*\b\d+x|3rd\s+identical\s+write)"),
        bucket=Bucket.HARNESS,
        suggested_action="Escalate write_file dedup message with current dir listing + next-action suggestion.",
        also=(Bucket.MODEL_PRIOR,),
    ),
    ClassificationRule(
        pattern_id="harness:thinking_stall",
        regex=_r(r"(empty[-\s]*response|thinking[-\s]*stall|empty\s+assistant\s+message|empty_after_tool[:\s]*\w+)"),
        bucket=Bucket.HARNESS,
        suggested_action="Pop empty message and inject 'Continue working' nudge.",
    ),
    ClassificationRule(
        pattern_id="harness:tool_error_raised",
        regex=_r(r"ToolError.*(file.*(not.*found|didn'?t\s+exist|missing)|missing.*path|not\s+in\s+read[-\s]*state)"),
        bucket=Bucket.HARNESS,
        suggested_action="Convert raise into advisory result with actionable hint.",
    ),
    ClassificationRule(
        pattern_id="harness:loop:bash_generic",
        regex=_r(r"loop[:\s]*bash\b"),
        bucket=Bucket.HARNESS,
        suggested_action="Investigate the bash subcommand pattern; admiral nudge already firing.",
        confidence=0.6,
    ),
    ClassificationRule(
        pattern_id="harness:install:api_key_demanded",
        regex=_r(r"MissingAPIKeyError|MISTRAL_API_KEY.*not\s+set"),
        bucket=Bucket.HARNESS,
        suggested_action="Auto-detect local LLM in bootstrap_config_files (already in v2.7.34).",
    ),

    # ----- RETRIEVAL rules (model can't know without lookup) -----
    ClassificationRule(
        pattern_id="retrieval:cross_package_inheritance",
        regex=_r(r"(read_file.*\b\d{2,}\s+times|read\s+\S+\s+\d{2,}\s+times|inherits?\s+from\s+\w+\.\w+\s+but\s+not\s+found|looking\s+for\s+\w+\s+to\s+pattern[-\s]*match)"),
        bucket=Bucket.RETRIEVAL,
        suggested_action="Suggest retrieve(query=<symbol>) before next read_file; index parent-class chain.",
        also=(Bucket.MODEL_PRIOR,),
    ),
    ClassificationRule(
        pattern_id="retrieval:multi_module_design_loss",
        regex=_r(r"(rewriting\s+\w+\.py.*and\s+breaking|module\s+\w+\s+regressed|forgot.*PRD\s+goal|lost.*design\s+context)"),
        bucket=Bucket.RETRIEVAL,
        suggested_action="Persist project design memory in GraphRAG; surface relevant chunks in system prompt.",
    ),
    ClassificationRule(
        pattern_id="retrieval:missing_corpus_evidence",
        regex=_r(r"(I\s+don'?t\s+know.*\bX\b|cannot\s+find.*in\s+codebase|no\s+definition\s+for)"),
        bucket=Bucket.RETRIEVAL,
        suggested_action="Index more of the project corpus / extend GraphRAG ingestion.",
    ),

    # ----- STEERING rules (stable behavioral priors) -----
    ClassificationRule(
        pattern_id="steering:no_web_search_when_stuck",
        regex=_r(r"(\d+\s+search_replace\s+fail|local-only\s+failure\s+loop|never\s+used\s+web_search)"),
        bucket=Bucket.STEERING,
        suggested_action="Boost 'external-lookup-when-stuck' direction; pair with web_search availability hint.",
        also=(Bucket.MODEL_PRIOR,),
    ),
    ClassificationRule(
        pattern_id="steering:rewrite_instead_of_patch",
        regex=_r(r"(full[-\s]*file\s+rewrite|broke\s+\d+\s+passing\s+tests|score\s+regression\s+after\s+fix)"),
        bucket=Bucket.STEERING,
        suggested_action="Boost 'minimal-patch' direction; tighten search_replace usage in prompt.",
    ),
    ClassificationRule(
        pattern_id="steering:interactive_fallback_with_args",
        regex=_r(r"(getpass.*EOFError|input\(\).*non[-\s]*interactive|ignored\s+--\w+\s+flag)"),
        bucket=Bucket.STEERING,
        suggested_action="Boost 'prefer-explicit-args' direction; add an AST hint at write_file time.",
    ),

    # ----- MODEL_PRIOR (reasoning gaps — LoRA territory) -----
    ClassificationRule(
        pattern_id="model:subtle_logic_bug_unfound",
        regex=_r(r"(\d+\s+iterations.*same\s+failing\s+test|never\s+traced.*data\s+flow|kept\s+rewriting\s+but\s+missed)"),
        bucket=Bucket.MODEL_PRIOR,
        suggested_action="LoRA candidate: traces of correct failing-test → data-flow → minimal-fix patches.",
        also=(Bucket.STEERING,),
    ),
    ClassificationRule(
        pattern_id="model:abstract_reasoning_failure",
        regex=_r(r"(optimization.*idle|optimize\s+phase\s+failed|treats\s+optimize\s+as\s+too\s+vague)"),
        bucket=Bucket.MODEL_PRIOR,
        suggested_action="Worked-example prompts for performance reasoning + per-vertical Deep Noir vector.",
    ),
    ClassificationRule(
        pattern_id="model:scaffolding_without_wiring",
        regex=_r(r"(added.*signature.*never\s+pass|tests\s+pass.*signature\s+only|method.*defined.*not\s+wired)"),
        bucket=Bucket.MODEL_PRIOR,
        suggested_action="Worked-example: end-to-end-tested feature traces; LoRA candidate.",
    ),

    # ----- AMBIGUOUS_INPUT (not a model bug) -----
    ClassificationRule(
        pattern_id="input:underspecified_prompt",
        regex=_r(r"(asked\s+for\s+clarification|prompt\s+is\s+ambiguous|don'?t\s+understand\s+the\s+request)"),
        bucket=Bucket.AMBIGUOUS_INPUT,
        suggested_action="Surface to user as 'PRD/prompt under-specified'; not a drydock fix.",
        confidence=0.9,
    ),
)
