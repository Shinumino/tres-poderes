# Tres Poderes

What the people in Brazil's three federal powers cost, taken from official open data and shown so an
ordinary Brazilian can read it. Site language: pt-BR. Built one power at a time, the Camara first.

## Commands

| | |
|---|---|
| Run | `python tools/download_camara_cota.py`, then `python -m tres_poderes load-cota` |
| Test (narrow) | `python -m unittest tests.test_cota`, or `check.cmd -k <part of a test name>` |
| Check (what CI will run) | `check.cmd` |
| Prove the data | `python -m tres_poderes verify-cota` (database against the files, to the centavo) |

## Rules the owner set (2026-10-12)

1. **Compare, never grade.** Reference points yes (minimum wage, pay ceiling, others in the same office).
   Scores, labels or colours that say good or bad, no. A ranking is by ONE real number and is named
   after that number.
2. **Favor no one.** The same metric for every person, party and power.
3. **Money is whole centavos, always.** No floating point anywhere near an amount. `parse_centavos`
   refuses what it would have to round; do not loosen it, store the original text instead.
4. **Quota totals use `vlrLiquido`** (what the quota paid). `vlrDocumento` is the invoice value: it is
   kept as written and has 5,487 values that are not exact centavos.
5. **Every number can be traced** to an official file and line, and every value shown to a reader gets
   a short plain-language description of what it is.
6. **Test first, red shown.** Tests call the public functions (`load_cota`, `total_liquido`,
   `receipts`, the commands); they never query the tables.
7. **No login; the only user input is filtering.** JavaScript is fine for the interface, served from
   our own site.

## What the data taught us (measured 2026-10-12)

- The yearly bulk files are the base, but they are NOT complete: air tickets
  (`PASSAGEM AÉREA - SIGEPA`) stop appearing in them in August 2025. The API still has them.
  **No 2025 or 2026 total is complete until the API cross-check exists.**
- The API is not complete either (it lacks rows the bulk has), its `ano` filter returned nothing for
  anyone on 2026-10-12 (ask by `idLegislatura`), and it can repeat rows across pages. Accept a crawl
  only when the number of distinct rows equals the `x-total-count` header.
- The owner's earlier project (Super-trunfo, kept outside this repository) is a read-only reference.
  Its database misses about half of 2023-2024 and is not a source of truth for totals.

## Not built yet (so nobody assumes it is)

CI, kept copies of each day's raw files, the API cross-check, precomputed totals, backups and a
restore drill, the security pass, a changelog, any web page.
