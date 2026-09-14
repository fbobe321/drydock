# Drydock — Go-To-Market / Growth PRD

Status: DRAFT (2026-09-13) — plan approved, not yet executed.
Owner: Frank Bobe III. License context: Apache-2.0, solo-maintained.
Companion to the product PRD (`docs/PRD.md`); this doc is about **adoption**, not features.

---

## 0. Why this PRD exists

Drydock is technically shipping (PyPI `drydock-cli`, full agentic toolset, missions, ratchet,
RMF/STIG + NIST governance) but has **2 GitHub stars / 1 fork** and no user base. This PRD is the plan
to get it off the ground — deliberately, in the one lane where a solo project can actually win.

> **Scope note (2026-09-13):** FIAR (financial audit readiness) is intentionally OUT of the public
> positioning — too narrow and too close to owner's professional domain (IP/conflict separation). NIST
> is the bigger, more defensible audience. See the open decision in §6 on pulling FIAR from the shipped
> codebase, not just the marketing.

**Reality check that shapes everything below:** the *general* terminal-coding-agent category is
saturated — OpenCode (~207k★), Claude Code, Cursor, Copilot. Drydock will **not** win there and will
not try to. The tbench baseline (Drydock+gemma4 = 21% pass@1, filed as internal-only, see
`docs/PRD.md` / tbench notes) confirmed the general-agent score is model-bound and not a competitive
story. **We compete where nobody with stars is playing.**

## 1. Positioning thesis (the pivot)

STOP marketing Drydock as "another local coding agent." LEAD with the moat:

> **The air-gapped coding agent that automates your NIST compliance — CSF 2.0, 800-53, AI RMF, STIG.**

- **Wedge audience:** federal agencies, defense/GovCon, critical-infrastructure, and any org adopting
  NIST CSF 2.0 (which in 2024 explicitly broadened scope from critical-infra to *all* organizations —
  a much larger audience than a single sector). These orgs often *cannot* use cloud agents (policy/ATO)
  and have **compliance mandates that drive budget**.
- **Why it's defensible:** no major agent does offline + governed NIST compliance automation. Sticky
  users who don't churn to the shiny thing, and a category we can credibly own.
- **The general-agent story becomes the proof point, not the pitch:** "and it's a full coding agent
  that runs offline on a single workstation" — said *second*, after the compliance hook lands.

Non-goal: chasing raw star count or HN front page as a general coding tool. Vanity stars from
drive-by general-dev traffic churn and generate support noise that trades against research time.

## 1b. The durable thesis: NIST for the LLM/agent era ("NIST 2.0")

Compliance automation is the *entry* wedge; the durable, forward-looking position is bigger and
almost nobody is occupying it. **Cybersecurity compliance was built for deterministic systems and
human operators. LLMs + agentic harnesses break those assumptions — so the frameworks must pivot.**
Drydock sits directly on that fault line, in two reinforcing ways:

1. **Drydock automates the NIST work** — CSF 2.0, 800-53, AI RMF (AI 100-1), the generative-AI
   profile: control mapping, assessment, remediation, POA&M, evidence packaging. Broad and growing.

2. **Drydock is itself a reference for a *governable* agent** — the thing cyber will be forced to
   reckon with as agents proliferate inside environments. What do "least privilege / separation of
   duties / audit trail / continuous authorization" mean for a *non-deterministic autonomous actor
   with shell access*? Drydock already embodies answers: air-gapped (no data-plane exfiltration — a
   control, not a nicety), a deterministic control loop + verification gates (bounded, non-self-
   certifying behavior), a durable tamper-evident event trace (`/events` / `/trace` — the audit
   record), advisory-not-blocking safety, and a credential-exfil release gate.

**The pivot cyber needs** — and no mature framework covers yet — is a control model for *"an
autonomous agent operating in your environment"*: model/prompt/tool supply chain (SBOM for weights,
prompt-injection, tool-poisoning), assurance under non-determinism, continuous ATO for systems that
update/learn, and provable data-flow containment. That gap is the thought-leadership lane, and it
doubles as a distribution engine (§W7): writing *"what CSF 2.0 / AI RMF mean when your operators are
agents"* attracts exactly the audience we want and positions Drydock as the tool that already answers
it.

## 2. Goals & success metrics

"Off the ground" = **a small base of committed niche users who depend on it**, not a vanity number.

| Metric | Now | 90-day target | Signal it measures |
|---|---|---|---|
| Real trial installs (quickstart completed) | ~0 | 100 | Activation / friction removed |
| Committed users (return, file issues, in a channel) | 0 | 10–20 | Genuine traction |
| GitHub stars | 2 | 150+ | Social proof / cold-start credibility (secondary) |
| Flagship proof-of-work artifacts published | 0 | 1 (offline STIG→POA&M) | Undeniable, shareable value |
| Niche-channel posts landed | 0 | 5 | Reaching the right audience |

Stars are tracked as a **credibility signal** (2★ pattern-matches "abandoned"), not the objective.

## 3. Workstreams

### W1 — Repositioning (highest leverage, do first)
Rewrite the front door around the air-gapped-compliance wedge.
- **Deliverables:** new README headline + first screen; `web/index.html` hero rewrite; one-line
  descriptor reused everywhere (repo "About", PyPI summary, social).
- **Copy spine:** hook (air-gapped compliance) → 60-sec demo → who it's for → "also a full offline
  coding agent" → install. Features list moves *below* the fold.
- **Done when:** a compliance/GovCon reader sees the headline and thinks "this is for me," not "why
  not OpenCode?"

### W2 — Frictionless quickstart (the real blocker)
A new user today needs a GPU + a served model before Drydock does anything; every step loses people.
- **Deliverables:** one-command path — `pipx install drydock-cli` + a `drydock bootstrap` (or script)
  that pulls a known-good GGUF and starts llama.cpp with the right flags; an **easy strong-model
  trial path** (`drydock --provider openai --model … --api-key …` or Copilot/OpenAI key) so
  evaluators can try it with a capable model on minute one; a hosted asciinema cast / GIF so people
  see it work *before* installing.
- **Done when:** zero-to-first-successful-task is one paste + one command, and a keyed frontier model
  works without local GPU.

### W3 — Flagship proof-of-work (the shareable moment)
One undeniable artifact a cloud agent cannot produce.
- **Deliverable:** recorded, reproducible demo — "Drydock takes a fresh RHEL/Ubuntu box from zero to
  a passing STIG baseline, fully offline, and emits the POA&M," with the `.ckl`/POA&M outputs
  committed to an `examples/` showcase.
- **Done when:** it's a 60–90s screen recording + a repo dir anyone can re-run; screenshot-able.

### W4 — Distribution (go where the audience lives — NOT HN)
- **Channels:** r/NISTControls, r/govcon, GRC/compliance Slack & Discord communities, LinkedIn
  (defense-tech + GRC crowd), ATO/cybersecurity practitioner forums, Ollama / LM Studio / local-LLM
  showcases.
- **Deliverable:** 5 genuinely-useful posts (e.g. "how I automated a STIG assessment offline"), each
  leading with the problem, not the tool.
- **Done when:** posts are live and driving trial installs (tag/track via quickstart referrer note).

### W5 — Social proof / cold-start
- **Deliverables:** seed 10–20 real trials → honest stars; one strong "why I built this" writeup;
  listings in aggregators that get scraped (awesome-ai-agents / awesome-local-llm / awesome-RMF-type
  lists), local-LLM community showcases.
- **Done when:** star count and listings no longer read as "abandoned."

### W6 — First-impression model default (protect the trial)
Ties to the model-ceiling finding: a first run on gemma4 that flubs a task loses the user forever.
- **Deliverables:** quickstart *recommends* a strong local coder model (e.g. Qwen2.5-Coder-32B) or
  the keyed-frontier trial path as the default first experience; gemma4/offline-31B positioned as
  the **proof point** ("and it runs fully offline on a 31B"), not the first thing tried.
- **Done when:** the default trial experience uses a model that succeeds on common tasks.

### W7 — Thought leadership: "NIST 2.0 for the agent era" (positioning + distribution engine)
The §1b thesis, turned into a small content series that IS the marketing (not ads about the tool).
- **Deliverables:** 3–4 short pieces — e.g. *"CSF 2.0 when your operators are agents,"* *"What
  least-privilege / SoD / cATO mean for an autonomous agent with shell access,"* *"Air-gapped as a
  control: keeping the data plane on-box"* — each ending with a concrete Drydock demonstration.
- **Feeds:** W4 (channels) and W5 (credibility). Establishes the category before competitors name it.
- **Done when:** the series is published and cited/linked by the niche audience, not just posted.

## 4. Sequencing

1. **W1 + W2 first** (repositioning + frictionless quickstart) — nothing else pays off until the
   front door is right and trials don't die on setup.
2. **W3** (flagship proof-of-work) — the asset every channel post points to.
3. **W6** folded into W2 (default model choice is part of the quickstart).
4. **W4 + W5** (distribution + social proof) — only after 1–3, so arrivals convert.

## 5. Risks & the trade-off (stated plainly)

- **Maintenance cost of users.** Growth trades against research time (issues, support, breaking
  changes). Decision: optimize for a handful of committed niche users over drive-by stars.
- **Model-quality first impression.** Mitigated by W6.
- **Solo-maintainer sustainability / bus factor.** Real; keep scope to the wedge, don't sprawl.
- **Wedge may be too narrow.** Acceptable — a small owned niche beats losing the general race.

## 6. Open decisions (need owner input)
- **FIAR in the shipped codebase — DONE (2026-09-13).** Owner chose full removal. Excised
  `drydock/fiar.py`, the four `fiar-*` builtin skills, all six `Fiar*` tools (schemas + funcs +
  registration in `drydock/tools/__init__.py`), `tests/test_fiar.py`, the README section, and doc
  refs; `tests/test_skills.py` expectation updated. Recoverable via git history if ever needed.
  (Remaining: historical FIAR mentions in `RESUME.md`'s dated dev-log — decide whether to scrub those
  too, since RESUME is in the repo.)
- Which model to feature in the W2 quickstart default (Qwen2.5-Coder-32B is the leading candidate).
- Is public GTM even the goal, or is Drydock a research platform + personal tool where stars are
  vanity? (This PRD assumes: pursue the niche. Revisit if the answer is "research-only.")
- Whether to keep the repo private→public flip timing aligned with W1 (README must be repositioned
  *before* it gets traffic).
