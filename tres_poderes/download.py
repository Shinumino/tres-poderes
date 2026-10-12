"""Download the Camara's yearly quota files, keeping what each new download replaces.

Under the raw folder:

    Ano-2026.csv.zip                                   the file in use: what load-cota reads
    versions/Ano-2026/<csv sha256>/Ano-2026.csv.zip    each distinct content, the newest few kept
    versions/manifest.json                             what was kept, when, with which hashes

When someone says a number was different last week, the file it came from is still here to be
opened. A download replaces the file in use only after the loader itself has accepted it; an
error page, another year's file or a damaged zip leaves the file in use exactly as it was.
"""
import collections
import dataclasses
import datetime
import hashlib
import json
import os
import pathlib
import shutil
import time
import urllib.error
import urllib.request

from tres_poderes import cota
from tres_poderes.money import format_brl

URL = "https://www.camara.leg.br/cotas/Ano-{year}.csv.zip"
# 2007 and every year before it answered 404 on 2026-10-12; 2008 exists with 445 receipts.
FIRST_YEAR = 2008
# Versions kept per year (the owner's choice, 2026-10-12). The current year changes almost daily,
# so this is about ten days of it; a closed year changes rarely, so ten of its versions go far back.
KEEP = 10

Answer = collections.namedtuple("Answer", "status body last_modified")


@dataclasses.dataclass(frozen=True)
class Outcome:
    year: int
    ok: bool
    message: str


@dataclasses.dataclass(frozen=True)
class Version:
    path: pathlib.Path
    sha256: str               # of the CSV inside: the hash the database records for a loaded year
    zip_sha256: str
    receipts: int
    centavos: int
    last_modified: str | None
    downloaded_at: str


def fetch(url):
    """Ask the Camara for one file. An HTTP error is an answer to act on, not an exception."""
    request = urllib.request.Request(url, headers={"User-Agent": "tres-poderes (github.com/Shinumino/tres-poderes)"})
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return Answer(response.status, response.read(), response.headers.get("Last-Modified"))
    except urllib.error.HTTPError as error:
        return Answer(error.code, b"", None)


def _version_path(raw, year, sha256):
    return raw / "versions" / f"Ano-{year}" / sha256 / f"Ano-{year}.csv.zip"


def _read_manifest(raw):
    path = raw / "versions" / "manifest.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _write_manifest(raw, manifest):
    path = raw / "versions" / "manifest.json"
    # Written beside and swapped in, after every year: a run cut short never leaves half a manifest.
    partial = path.with_suffix(".part")
    partial.write_text(json.dumps(manifest, indent=1, sort_keys=True), encoding="utf-8")
    os.replace(partial, path)


def versions(raw, year):
    """The kept versions of a year, oldest first."""
    raw = pathlib.Path(raw)
    return [Version(path=_version_path(raw, year, entry["sha256"]), **entry) for entry in _read_manifest(raw).get(str(year), [])]


def _accepted(incoming, year):
    """(csv sha256, receipts, centavos) once the loader has taken the whole file; BadFile otherwise.

    The real loader, into a database that is thrown away: "accepted here" and "will load tonight"
    cannot then mean two different things.
    """
    scratch = cota.connect(":memory:")
    try:
        cota.load_cota(scratch, incoming)
        return cota.loaded_sha256(scratch, year), cota.receipt_count(scratch, year), cota.total_camara(scratch, year)
    finally:
        scratch.close()


def _one_year(raw, year, fetch, now, keep):
    current = raw / f"Ano-{year}.csv.zip"
    try:
        answer = fetch(URL.format(year=year))
    except OSError as error:  # refused connections, timeouts, DNS: urllib raises them all as OSError
        return Outcome(year, False, f"could not be downloaded ({error})")
    if answer.status == 404:
        # A year never seen is next year's file in early January. A year we hold going missing is
        # the Camara taking data away, and that must not pass as "nothing to do".
        if current.exists():
            return Outcome(year, False, "the Camara answered 404 for a year we hold")
        return Outcome(year, True, "not published")
    if answer.status != 200:
        return Outcome(year, False, f"the Camara answered {answer.status}")

    # Under the yearly name, in a folder of its own: the loader checks that name against the content.
    incoming = raw / "versions" / "incoming" / current.name
    incoming.parent.mkdir(parents=True, exist_ok=True)
    incoming.write_bytes(answer.body)
    try:
        sha256, receipts, centavos = _accepted(incoming, year)
    except cota.BadFile as error:
        incoming.unlink()
        return Outcome(year, False, f"refused, {error}")

    manifest = _read_manifest(raw)
    kept = manifest.setdefault(str(year), [])
    if kept and kept[-1]["sha256"] == sha256 and current.exists():
        # The Camara rebuilds every zip each night: other bytes, the same receipts. Not a new version.
        incoming.unlink()
        return Outcome(year, True, f"unchanged, {receipts} receipts, {format_brl(centavos)}")

    stored = _version_path(raw, year, sha256)
    stored.parent.mkdir(parents=True, exist_ok=True)
    os.replace(incoming, stored)
    partial = current.with_suffix(".part")
    shutil.copyfile(stored, partial)
    os.replace(partial, current)

    kept[:] = [entry for entry in kept if entry["sha256"] != sha256]  # content that came back is moved to newest
    kept.append({
        "sha256": sha256,
        "zip_sha256": hashlib.sha256(answer.body).hexdigest(),
        "receipts": receipts,
        "centavos": centavos,
        "last_modified": answer.last_modified,
        "downloaded_at": now().isoformat(),
    })
    for old in kept[:-keep]:
        shutil.rmtree(_version_path(raw, year, old["sha256"]).parent, ignore_errors=True)
    del kept[:-keep]
    _write_manifest(raw, manifest)
    return Outcome(year, True, f"new version, {receipts} receipts, {format_brl(centavos)}")


def download_cota(raw, years, fetch=fetch, now=lambda: datetime.datetime.now(datetime.timezone.utc), keep=KEEP, pause=1.0):
    """Download the given years into raw. Returns one Outcome per year; no year stops another."""
    raw = pathlib.Path(raw)
    (raw / "versions").mkdir(parents=True, exist_ok=True)
    outcomes = []
    for index, year in enumerate(years):
        if index and pause:
            # One request at a time with a pause: this is a public server and we are a guest on it.
            time.sleep(pause)
        outcomes.append(_one_year(raw, year, fetch, now, keep))
    return outcomes
