# Tres Poderes

What the people in Brazil's three federal powers cost, taken from official open data and shown so an
ordinary Brazilian can read it. Site language: pt-BR. Built one power at a time, the Camara first.

## Commands

| | |
|---|---|
| Run | `python -m tres_poderes download-cota`, then `python -m tres_poderes load-cota` |
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
- The API is not complete either: it lacks rows the bulk has. Its `ano` filter only works together
  with `idLegislatura` (alone it returned nothing for anyone on 2026-10-12).
- The API shows fewer fields than the bulk, so different receipts can look identical in it (two
  segments of a flight, for example: 37 such rows in 5,852 for one deputy). Never de-duplicate API rows
  by content, match the two sources as counted sets, and accept a crawl only when the number of rows
  received equals the `x-total-count` header. The bulk files have no exact-copy rows.
- The owner's earlier project (Super-trunfo, kept outside this repository) is a read-only reference.
  Its database misses about half of 2023-2024 and is not a source of truth for totals.

- The real files are less tidy than they look: values contain quote characters (doubled, as CSV
  requires) and, from 2021 on, a few fields contain line breaks. So "one line is one receipt" is
  false, and a gate against line breaks in fields would refuse real files.
- Every one of the 19 real files can be rebuilt byte for byte from its parsed values. `verify-cota`
  relies on this: the database must reproduce each official file exactly. If the Camara ever changes
  how it writes the CSV (line ends, quoting, the byte order mark), that check goes red by design.
- A yearly file says its year three times: the zip's name, the CSV's name, every row. The loader
  refuses a file where they disagree; each of those gaps was a way to replace the wrong year.

- The database is derived data: nothing in it exists that the files do not hold. After changing how
  the loader reads, delete `data/tres_poderes.db` and load again (about 80 s). `load-cota` alone
  will not redo a year whose file content it has already loaded. `verify-cota` does notice: it
  flagged the 27 multi-line receipts the day `linha` changed from "where it ends" to "where it
  starts".
- `total_liquido` and `receipts` need a deputy; `total_camara` is the whole Camara and
  `receipts_without_deputy` the leaderships. `None` used to mean "everyone" in one and "nobody" in
  the other.

## Known and not fixed

- A load while another process holds the write lock ends in a traceback ("database is locked")
  after 5 s instead of a plain message. The exit code is non-zero and nothing is half-written.

## Not built yet (so nobody assumes it is)

The API cross-check, a continuity alarm, the deputies table, backups and a restore drill, the
security pass, a changelog, any web page. CI runs `check.cmd` on every push
(`.github/workflows/check.yml`).

## Downloads

`download-cota` keeps the 10 most recent distinct versions of each year (the owner's limit,
2026-10-12; `--keep`) under `data/raw/camara/cota/versions/`, with a manifest of hashes and dates.
A download becomes the file in use only after the real loader has accepted it into a throwaway
database. Tests reach it through a fake `fetch`; nothing in the suite touches the network.
