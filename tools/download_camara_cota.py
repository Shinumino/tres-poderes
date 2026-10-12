"""Download the Camara's yearly quota-expense files (Cota Parlamentar) into data/raw.

These are the official bulk export: one zip per year, refreshed daily by the Camara. Loading
history from them replaces weeks of crawling the API deputy by deputy.

Every download is recorded in manifest.json with its sha256, so a number we publish can be
traced back to the exact file it came from.

    python tools/download_camara_cota.py            # every year the Camara publishes
    python tools/download_camara_cota.py 2025 2026  # only these years
"""
import datetime
import hashlib
import json
import os
import pathlib
import sys
import time
import urllib.error
import urllib.request

URL = "https://www.camara.leg.br/cotas/Ano-{year}.csv.zip"
# 2007 and every year before it answered 404 on 2026-10-12; 2008 exists but is nearly empty.
FIRST_YEAR = 2008
DEST = pathlib.Path(__file__).resolve().parent.parent / "data" / "raw" / "camara" / "cota"
# One request at a time with a pause: this is a public server and we are a guest on it.
PAUSE_SECONDS = 1.0


def download(year, fetch=urllib.request.urlopen):
    """Fetch one year. Returns the manifest entry, or None when the Camara has no file."""
    url = URL.format(year=year)
    request = urllib.request.Request(url, headers={"User-Agent": "tres-poderes (open data, personal project)"})
    try:
        with fetch(request, timeout=120) as response:
            body = response.read()
            last_modified = response.headers.get("Last-Modified")
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return None
        raise

    target = DEST / f"Ano-{year}.csv.zip"
    # Write beside the target and swap: a download cut half way never replaces a good file.
    partial = target.with_suffix(".part")
    partial.write_bytes(body)
    os.replace(partial, target)
    return {
        "url": url,
        "bytes": len(body),
        "sha256": hashlib.sha256(body).hexdigest(),
        "last_modified": last_modified,
        "downloaded_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
    }


def main(argv):
    years = [int(a) for a in argv] or list(range(FIRST_YEAR, datetime.date.today().year + 1))
    DEST.mkdir(parents=True, exist_ok=True)
    manifest_path = DEST / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}

    for year in years:
        entry = download(year)
        if entry is None:
            print(f"{year}: no file (404)")
        else:
            manifest[f"Ano-{year}.csv.zip"] = entry
            print(f"{year}: {entry['bytes'] / 1e6:6.1f} MB  {entry['sha256'][:12]}  modified {entry['last_modified']}")
        time.sleep(PAUSE_SECONDS)

    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")


if __name__ == "__main__":
    main(sys.argv[1:])
