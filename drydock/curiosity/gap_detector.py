"""Gap detector — find unfamiliar terms in user input.

A "gap" is a token in a user message that looks like a named entity or
identifier the agent probably has no context for: paper titles, library
names, API identifiers, file paths, multi-word Title Case phrases,
ALL-CAPS acronyms, version-like tokens.

The detector is HEURISTIC, not exhaustive. False positives are cheap
(an extra retrieve call) and false negatives are the failure mode the
PRD §5.7 calls out (Gemma 4 answers from prior on a general-knowledge
HLE question because it never noticed there was something to look up).
Bias the heuristics toward firing.

Public surface:

    gaps: list[str] = detect_gaps(user_text)
"""
from __future__ import annotations

import re
from typing import Iterable

# Common English words that look like Title Case but aren't entities.
# Keep small — the goal is "minimize false positives on conversational
# openers", not exhaustive linguistic filtering.
_STOPWORDS: frozenset[str] = frozenset({
    # Sentence-start common Title-Case words.
    "The", "A", "An", "I", "We", "You", "It", "He", "She", "They",
    "This", "That", "These", "Those", "What", "When", "Where", "Why",
    "How", "Who", "Which", "If", "But", "And", "Or", "So", "Yes", "No",
    "Please", "Can", "Could", "Would", "Should", "Will", "Let", "Do",
    "Does", "Did", "Is", "Are", "Was", "Were", "Be", "Been", "Being",
    "Have", "Has", "Had", "Get", "Got", "Make", "Made", "Take", "Took",
    # Common imperative openers for drydock tasks.
    "Build", "Fix", "Add", "Remove", "Update", "Refactor", "Test",
    "Run", "Check", "Review", "Show", "List", "Explain", "Find", "Look",
    # HLE/exam prose openers — flagged in 2026-05-14 queue audit
    # because they always lead a Title-Case phrase ("Consider the X").
    "Consider", "Suppose", "Given", "Let", "Define", "Compute",
    "Determine", "Evaluate", "Prove", "Recall", "Note", "Assume",
})

# HLE / harness prompt-template tokens that the detector kept flagging
# as "unknown terms" — 2026-05-14 queue audit found FINAL (45×),
# ANSWER (44×), QUESTION (44×), FINAL ANSWER: (47×) all in the curiosity
# queue as false positives. Compared case-insensitively. Any candidate
# whose stripped form (case-folded, trailing colon stripped) is in this
# set is dropped before enqueue.
_TEMPLATE_NOISE: frozenset[str] = frozenset(
    s.lower() for s in {
        "FINAL", "ANSWER", "QUESTION", "FINAL ANSWER",
        "GROUND TRUTH", "PREDICTED ANSWER", "VERDICT",
        # autonomous_review / admiral output tokens
        "CONSIDER", "RESPONSE", "RESULT", "VERIFIED",
        # Common prose openers that pass acronym + title-case regexes
        "CHAPTER", "SECTION", "PART", "INTRODUCTION", "CONCLUSION",
        # HLE multiple-choice format boilerplate — 2026-05-16 queue audit
        # found "Answer Choices" (95×), "None of the" (13×), etc. leaking
        # through because they match the title-case phrase regex.
        "ANSWER CHOICES", "ANSWER CHOICE",
        "NONE OF THE", "NONE OF THE ABOVE", "NONE OF THESE",
        "ALL OF THE", "ALL OF THE ABOVE", "ALL OF THESE",
        "ALL OF ABOVE", "NONE OF ABOVE",
        "CHOOSE ONE", "SELECT ONE", "WHICH OF THE",
        "WHICH OF THE FOLLOWING",
    }
)

# Words that are only ever connectors — a Title-Case phrase composed
# entirely of these (after stripping the leading word) is prose filler.
_CONNECTOR_WORDS: frozenset[str] = frozenset({
    "of", "the", "and", "for", "in", "de", "von", "a", "an",
})


def _is_template_noise(candidate: str) -> bool:
    """True if the candidate is HLE/admiral boilerplate, not a real term."""
    norm = candidate.strip(" :.,;").lower()
    if not norm:
        return True
    if norm in _TEMPLATE_NOISE:
        return True
    # Drop bare English stopword tokens too (the user prompt sometimes
    # gets fragmented and "the" / "is" leak through the quoted-string
    # path with 3-char minimum length).
    if norm in {sw.lower() for sw in _STOPWORDS}:
        return True
    # A multi-word phrase whose non-first words are all connectors is
    # prose filler, not an entity ("None of the", "All of the", etc.).
    words = norm.split()
    if len(words) >= 2 and all(w in _CONNECTOR_WORDS for w in words[1:]):
        return True
    return False

# Acronyms shorter than this are too noisy to chase ("ID", "OK", "OS").
_MIN_ACRONYM_LEN = 3

# Maximum gaps to return per call. The retrieve consumer can only act
# on so many before context bloats; truncate at the source.
_MAX_GAPS = 8

_RE_ACRONYM = re.compile(r"\b[A-Z]{%d,}(?:-?[A-Z0-9]+)?\b" % _MIN_ACRONYM_LEN)
_RE_TITLE_CASE_PHRASE = re.compile(
    r"\b(?:[A-Z][a-z]+(?:[-'][A-Z][a-z]+)?)(?:\s+(?:[A-Z][a-z]+(?:[-'][A-Z][a-z]+)?|of|the|and|for|in|de|von)){1,4}"
)
_RE_DOTTED_IDENT = re.compile(r"\b[a-zA-Z_][a-zA-Z0-9_]*(?:\.[a-zA-Z_][a-zA-Z0-9_]*){1,}\b")
_RE_SNAKE_IDENT = re.compile(r"\b[a-z][a-z0-9_]*_[a-z0-9_]+\b")
_RE_VERSIONED = re.compile(r"\b[a-zA-Z][a-zA-Z0-9_-]*-\d+(?:\.\d+)+\b")
_RE_QUOTED = re.compile(r'"([^"\n]{3,80})"|\'([^\'\n]{3,80})\'')
_RE_PATH = re.compile(r"\b(?:/[A-Za-z0-9_.-]+){2,}\b")


def _strip_punct(s: str) -> str:
    return s.strip(" \t\n.,;:!?\"'()[]{}<>")


def _dedup_preserve_order(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for it in items:
        k = it.lower()
        if k in seen:
            continue
        seen.add(k)
        out.append(it)
    return out


def detect_gaps(text: str, max_gaps: int = _MAX_GAPS) -> list[str]:
    """Extract candidate unfamiliar terms from `text`.

    The agent_loop's curiosity hook calls this on every new user
    message. Anything returned becomes a retrieve target before the
    first LLM turn.
    """
    if not text or not text.strip():
        return []

    candidates: list[str] = []

    # Quoted strings of meaningful length — strongest signal (user
    # explicitly delimited a name).
    for m in _RE_QUOTED.finditer(text):
        val = m.group(1) or m.group(2) or ""
        val = val.strip()
        if val:
            candidates.append(val)

    # Filesystem paths — almost always worth knowing about.
    for m in _RE_PATH.finditer(text):
        candidates.append(m.group(0))

    # Versioned package-like tokens ("django-4.2", "torch-2.0.1").
    for m in _RE_VERSIONED.finditer(text):
        candidates.append(m.group(0))

    # Dotted identifiers (module.path or Type.method).
    for m in _RE_DOTTED_IDENT.finditer(text):
        candidates.append(m.group(0))

    # Snake-case identifiers (likely function or symbol names).
    for m in _RE_SNAKE_IDENT.finditer(text):
        candidates.append(m.group(0))

    # ALL-CAPS acronyms (RAG, MCP, GraphRAG-style).
    for m in _RE_ACRONYM.finditer(text):
        tok = m.group(0)
        if tok not in _STOPWORDS:
            candidates.append(tok)

    # Title-Case multi-word phrases (paper titles, product names).
    for m in _RE_TITLE_CASE_PHRASE.finditer(text):
        phrase = _strip_punct(m.group(0))
        if not phrase:
            continue
        first = phrase.split()[0]
        if first in _STOPWORDS:
            # Drop the leading stopword — "The Curiosity Layer" → "Curiosity Layer"
            phrase = " ".join(phrase.split()[1:])
            if not phrase:
                continue
        candidates.append(phrase)

    return _dedup_preserve_order(
        c for c in candidates if c and not _is_template_noise(c)
    )[:max_gaps]
