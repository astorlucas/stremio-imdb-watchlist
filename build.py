#!/usr/bin/env python3
"""Build a static Stremio catalog addon from a list of IMDb title ids.

Reads ids.txt (one tt id per line, IMDb watchlist order), asks Cinemeta (Stremio's own metadata
service) for each title's type, name and poster, and writes the files Stremio fetches:

    docs/manifest.json
    docs/catalog/movie/imdb-watchlist.json
    docs/catalog/series/imdb-watchlist.json

docs/ is what GitHub Pages serves (https, which Stremio's install link needs); any static web server
works. Install in Stremio with <base url>/manifest.json. Titles Cinemeta does not know are listed in
docs/unknown.txt and left out. Re-run after editing ids.txt, then commit and push.
"""
import json
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

CINEMETA = "https://v3-cinemeta.strem.io/meta/{type}/{id}.json"
CATALOG_ID = "imdb-watchlist"
ADDON_ID = "org.lucas.imdb-watchlist"
TYPES = ("movie", "series")
WORKERS = 8
TIMEOUT_S = 20

HERE = Path(__file__).resolve().parent
SITE = HERE / "docs"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Cinemeta answers a 307 for a title asked under the wrong type; following it returns the title typed as
    asked and without a poster. Left unfollowed, the 3xx is the signal to try the other type."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        return None


OPENER = urllib.request.build_opener(NoRedirect)
WRONG_TYPE_CODES = {301, 302, 303, 307, 308, 404}


def read_ids(path: Path) -> list[str]:
    ids = [line.strip() for line in path.read_text().splitlines()]
    seen: set[str] = set()
    ordered = []
    for imdb_id in ids:
        if imdb_id.startswith("tt") and imdb_id not in seen:
            seen.add(imdb_id)
            ordered.append(imdb_id)
    return ordered


def fetch_meta(imdb_id: str) -> dict | None:
    """Cinemeta answers for the right type only; asked under the wrong one it redirects (or 404s)."""
    for media_type in TYPES:
        req = urllib.request.Request(CINEMETA.format(type=media_type, id=imdb_id),
                                     headers={"User-Agent": "stremio-imdb-watchlist/1.0"})
        try:
            with OPENER.open(req, timeout=TIMEOUT_S) as resp:
                meta = json.load(resp).get("meta") or {}
        except urllib.error.HTTPError as err:
            if err.code in WRONG_TYPE_CODES:
                continue
            print(f"  {imdb_id}: HTTP {err.code} from Cinemeta", file=sys.stderr)
            continue
        except (urllib.error.URLError, json.JSONDecodeError, TimeoutError) as err:
            print(f"  {imdb_id}: {err}", file=sys.stderr)
            continue
        if meta.get("id") == imdb_id and meta.get("name"):
            return {
                "id": imdb_id,
                "type": meta.get("type", media_type),
                "name": meta["name"],
                "poster": meta.get("poster"),
                "posterShape": "poster",
                "description": meta.get("description"),
                "releaseInfo": meta.get("releaseInfo") or meta.get("year"),
                "imdbRating": meta.get("imdbRating"),
                "genres": meta.get("genres"),
            }
    return None


def manifest(movies: int, series: int) -> dict:
    return {
        "id": ADDON_ID,
        "version": "1.0.0",
        "name": "IMDb Watchlist (Lucas)",
        "description": f"Static copy of an IMDb watchlist: {movies} movies, {series} series.",
        "resources": ["catalog"],
        "types": list(TYPES),
        "idPrefixes": ["tt"],
        "catalogs": [{"type": media_type, "id": CATALOG_ID, "name": "IMDb Watchlist"} for media_type in TYPES],
        "behaviorHints": {"configurable": False, "configurationRequired": False},
    }


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1))


def main() -> int:
    ids = read_ids(HERE / "ids.txt")
    print(f"{len(ids)} ids; asking Cinemeta...")
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        metas = list(pool.map(fetch_meta, ids))

    found = [m for m in metas if m]
    unknown = [imdb_id for imdb_id, m in zip(ids, metas) if not m]
    by_type = {media_type: [m for m in found if m["type"] == media_type] for media_type in TYPES}

    for media_type, items in by_type.items():
        write_json(SITE / "catalog" / media_type / f"{CATALOG_ID}.json", {"metas": items})
    write_json(SITE / "manifest.json", manifest(len(by_type["movie"]), len(by_type["series"])))
    (SITE / "unknown.txt").write_text("\n".join(unknown) + ("\n" if unknown else ""))
    (SITE / "index.html").write_text("<!doctype html><title>IMDb Watchlist addon</title>"
                                     "<p>Stremio addon. Install: <code>manifest.json</code> in this folder.</p>\n")
    print(f"movies: {len(by_type['movie'])}  series: {len(by_type['series'])}  unknown: {len(unknown)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
