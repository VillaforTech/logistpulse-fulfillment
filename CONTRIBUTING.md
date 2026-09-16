# Contributing to the LogistPulse reference

This repository maintains an executable integration baseline for the team-owned LogistPulse product. Contributions should preserve the complete fulfillment story: valid order transitions, durable facts, recoverable analytics, live Grafana rendering and a release gate based on business correctness.

The shared product work belongs in [LOGISTPULSE-GOLDEN_2](https://github.com/VillaforTech/logistpulse). Use this repository to improve the reference, reproducibility or engineering documentation. Do not use a reference commit to claim another contributor completed a shared issue.

## Workflow

1. Create a focused branch from the current `main` or the reviewed reference branch.
2. Describe the product behavior, failure mode or documentation gap being changed.
3. Add or update the smallest meaningful verification.
4. Record commands and observed results in the pull request.
5. Request review only after all required checks pass.

Use conventional prefixes such as `feat/`, `fix/`, `docs/` and `chore/`. Keep generated artifacts limited to intentional evidence and never commit local secrets.

## Definition of done

- Order transitions and outbox records remain transactional.
- Event identity and aggregate revision survive retries and replay.
- Kitchen commands and analytics facts retain their separate semantics.
- Analytics commits only after its state is durable.
- Timers detect overdue work without requiring a new event.
- Browser tests measure a correlated Grafana render, including losses and errors.
- Recovery tests retain state across broker interruption and service restart.
- Failed, skipped or cancelled required checks cannot produce a green release gate.
- Documentation describes observed behavior and distinguishes the reference from team contributions.

## Validation

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest tests/unit -q
node --test tests/browser/*.test.mjs
bash scripts/compose.sh run --rm --build database-tests
.venv/bin/python scripts/business_test.py
npm ci
npx playwright install --with-deps chromium
npm run browser-test
.venv/bin/python scripts/resilience_test.py
.venv/bin/python scripts/verify_evidence.py
```

Run the full suite for runtime changes. For documentation-only changes, verify links, commands, formatting and the rendered GitHub structure, then state that runtime behavior was unchanged.

## Security and attribution

Use only synthetic stores, orders and amounts. Never commit `.env`, tokens, institutional credentials or real customer, employee or fleet data. Do not publish a Codespace or local database port to make a demo easier.

Preserve authorship accurately. The reference was implemented by Roberto Villafuerte with Codex assistance; adaptations in the shared repository receive credit through their own commits, reviews and merged pull requests.

Course-specific evidence remains in `docs/deber-01.md`, but the repository should read first as an engineering reference that another developer can run, inspect and challenge.
