"""Download Josh Braun resources using direct PDF URLs."""

import time
from pathlib import Path
from urllib.parse import unquote
import requests

# Direct PDF URLs
URLS = [
    "https://joshbraun.com/wp-content/uploads/2022/07/hi-res.pdf",
    "https://joshbraun.com/wp-content/uploads/2022/06/9-Cold-Email-Copywriting-Formulas-That-Boost-Response-Rates.pdf",
    "https://joshbraun.com/wp-content/uploads/2022/04/Cold-Email-First-Stentence-Cheat-Sheet.pdf",
    "https://joshbraun.com/wp-content/uploads/2021/08/saleshelpdesk.pdf",
    "https://joshbraun.com/wp-content/uploads/2021/08/pokebearcoldcall.pdf",
    "https://joshbraun.com/wp-content/uploads/2021/08/20tactics.pdf",
    "https://joshbraun.com/wp-content/uploads/2021/08/3questions.pdf",
    "https://joshbraun.com/wp-content/uploads/2021/08/15coldemail-copywriting.pdf",
]

DELAY_SECONDS = 2


def get_filename(url):
    """Get filename from the URL."""
    return unquote(url.split("/")[-1])


def main():
    # Find project root
    script_dir = Path(__file__).resolve().parent
    if (script_dir / "research").is_dir():
        project_root = script_dir
    else:
        project_root = script_dir.parent

    # Output folder
    out_dir = project_root / "research" / "other" / "josh-braun"
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"Saving files to: {out_dir}")

    # Session with browser-like headers
    session = requests.Session()
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Referer": "https://joshbraun.com/learn/resources/",
    })

    # Download each file
    for index, url in enumerate(URLS, start=1):
        fname = get_filename(url)
        print(f"[{index}/{len(URLS)}] Downloading {fname} ...")

        try:
            response = session.get(url, stream=True, timeout=60)
            response.raise_for_status()

            dest = out_dir / fname
            with dest.open("wb") as f:
                for chunk in response.iter_content(chunk_size=64 * 1024):
                    if chunk:
                        f.write(chunk)

            size_kb = dest.stat().st_size / 1024.0
            print(f"    Saved: {fname} ({size_kb:.1f} KB)")

        except Exception as e:
            print(f"    ERROR: {e}")
            continue

        if index < len(URLS):
            time.sleep(DELAY_SECONDS)

    print("Done!")


if __name__ == "__main__":
    main()