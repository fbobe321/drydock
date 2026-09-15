# Offline STIG → POA&M (proof of work)

**What a cloud coding agent can't do: assess a CUI-bearing system for compliance
without sending anything off the box.** This example runs the full DISA STIG
pipeline — benchmark → assessed checklist → eMASS POA&M mapped to NIST 800-53 —
entirely offline, deterministically, with no LLM in the scoring path.

## Run it

```bash
pip install drydock-cli        # or: PYTHONPATH=. from a clone
python examples/stig-poam/run.py
```

No network, no API key, no GPU. It reads the sample benchmark and writes two files
under `out/` (both committed here so you can eyeball them without running anything).

## Inputs → outputs

| File | What it is |
|---|---|
| `benchmark-xccdf.xml` | A sample DISA STIG excerpt (XCCDF), 4 rules across CAT I/II/III |
| `out/assessed.ckl` | A completed DISA `.ckl` — statuses + finding details, re-imports into STIG Viewer / eMASS |
| `out/poam.csv` | eMASS POA&M for the **open** findings, each row mapped to its NIST 800-53 control |

The generated POA&M:

| Control | Raw | Rule | Finding |
|---|---|---|---|
| **AC-10** | CAT II | SV-222387r879511 | No concurrent-session limit configured |
| **IA-5**  | CAT I  | SV-222388r879589 | Credentials posted over plain HTTP |
| **SC-13** | CAT II | SV-222390r879887 | Non-FIPS cipher suite in use |

(The 4th rule — the DoD logon banner — was assessed *NotAFinding*, so it is correctly
absent from the POA&M.)

## Why this is the proof point, not a demo toy

- **Deterministic + ungameable.** The POA&M is a faithful transform of facts already
  in the checklist plus the DISA CCI→800-53 map — `drydock/poam.py` has no model call.
  The agent's job is the *assessment* (examining evidence to set each status); the
  export is pure, reproducible machinery.
- **Air-gapped by design.** Checklists carry hostnames, IPs, and findings that are
  almost always CUI. Nothing here touches the network — the whole reason to run a
  local agent instead of a cloud one.
- **Interactively, this is the `/stig-*` skills.** In the TUI you'd run `/stig new`
  (parse the XCCDF), let the agent assess each rule against real system evidence
  (`/stig-assess`), then `/stig poam` to export. This script exercises the same
  library functions (`stig.xccdf_to_checklist`, `Checklist.update`, `poam.export`)
  so the artifact is reviewable in CI and in a diff.

> A recorded ~60s terminal cast of the interactive `/stig-*` flow belongs alongside
> this directory (see `docs/gtm_prd.md` W3); this runnable example is the reproducible
> substrate behind it.
