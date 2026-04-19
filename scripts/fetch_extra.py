"""
fetch_extra.py

Reads YouTube video URLs from extravideos.txt, grouped under headings:
- TOM SLOCUM (3 videos)
- MARK KOSOGLOW (4 videos)
- KYLE COLEMAN (4 videos)

For each video:
- Fetch the transcript using the Supadata API
- Fetch the video title (so we can name the file nicely)
- Save a Markdown file under: research/youtube-transcripts/<Folder>/

Folder names are:
- Tom-Slocum
- Mark-Kosoglow
- Kyle-Coleman

Before running:
1) pip install -r requirements.txt
2) Put SUPADATA_API_KEY in .env (same as fetch_youtube.py uses)
3) python fetch_extra.py
"""

# Import os so we can work with files and folders and read environment variables.
import os
# Import re for making safe filenames.
import re
# Import time so we can wait while polling Supadata async jobs.
import time
# Import urlparse so we can validate URLs a little.
from urllib.parse import urlparse

# Import requests so we can call Supadata and YouTube oEmbed.
import requests
# Import ReadTimeout/ConnectionError so we can retry network hiccups.
from requests.exceptions import ReadTimeout, ConnectionError
# Import load_dotenv so we can load SUPADATA_API_KEY from .env.
from dotenv import load_dotenv


# ----------------------------
# Configuration (easy to edit)
# ----------------------------

# Find the folder where this script lives.
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# The input file with headings + URLs.
EXTRA_VIDEOS_FILE = os.path.join(SCRIPT_DIR, "extravideos.txt")

# Base output directory where we store everything.
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "research", "youtube-transcripts")

# Supadata API base URL.
SUPADATA_API_BASE = "https://api.supadata.ai/v1"

# Retry a few times if Supadata is slow.
SUPADATA_NETWORK_RETRIES = 3

# If Supadata returns a jobId (202), poll up to this many seconds.
SUPADATA_MAX_POLL_SECONDS = 180

# How often to poll for async jobs.
SUPADATA_POLL_INTERVAL_SECONDS = 1


# ----------------------------
# Helper functions
# ----------------------------

def slugify_for_filename(text: str, max_length: int = 140) -> str:
    """Convert text into a filename-safe slug."""
    # Lowercase for consistency.
    text = (text or "").lower()
    # Replace anything not a-z or 0-9 with "-".
    text = re.sub(r"[^a-z0-9]+", "-", text)
    # Remove extra "-" at the ends.
    text = text.strip("-")
    # Fallback if empty.
    if not text:
        text = "untitled"
    # Truncate to avoid super-long paths on Windows.
    return text[:max_length]


def normalize_group_name(raw_heading: str) -> str | None:
    """
    Convert a heading line like "TOM SLOCUM" into a folder name like "Tom-Slocum".
    Returns None if the heading isn't one of the ones we recognize.
    """
    # Clean whitespace and uppercase for comparison.
    h = (raw_heading or "").strip().upper()
    # Map headings to the exact folder names you requested.
    mapping = {
        "TOM SLOCUM": "Tom-Slocum",
        "MARK KOSOGLOW": "Mark-Kosoglow",
        "KYLE COLEMAN": "Kyle-Coleman",
    }
    # Return the mapped folder name, or None if not found.
    return mapping.get(h)


def is_probably_url(text: str) -> bool:
    """Quick check if a string looks like a URL."""
    # Parse the text as a URL.
    parsed = urlparse(text)
    # If it has a scheme (http/https) and a network location, it's probably a URL.
    return bool(parsed.scheme and parsed.netloc)


def read_grouped_video_urls(path: str) -> dict[str, list[str]]:
    """
    Read extravideos.txt and return a dictionary like:
      { "Tom-Slocum": [url1, url2, ...], "Mark-Kosoglow": [...], ... }
    """
    # Start with empty groups.
    groups: dict[str, list[str]] = {"Tom-Slocum": [], "Mark-Kosoglow": [], "Kyle-Coleman": []}
    # Keep track of which group we're currently reading.
    current_group: str | None = None

    # Open the file in UTF-8.
    with open(path, "r", encoding="utf-8") as f:
        # Go line by line.
        for line in f:
            # Remove whitespace.
            s = line.strip()
            # Skip empty lines.
            if not s:
                continue
            # See if this line is a recognized heading.
            maybe_group = normalize_group_name(s)
            if maybe_group:
                current_group = maybe_group
                continue
            # If it's a URL and we have a current group, add it.
            if current_group and is_probably_url(s):
                groups[current_group].append(s)

    # Return the grouped URLs.
    return groups


def fetch_youtube_title(video_url: str) -> str:
    """
    Fetch the video title without needing a YouTube API key.
    Uses YouTube oEmbed endpoint which returns JSON including "title".
    """
    # Build the oEmbed endpoint URL.
    oembed_url = "https://www.youtube.com/oembed"
    # Call it with the video URL.
    resp = requests.get(oembed_url, params={"url": video_url, "format": "json"}, timeout=(15, 30))
    # Raise an error if it failed (e.g. invalid URL).
    resp.raise_for_status()
    # Parse JSON and return the title.
    data = resp.json()
    return str(data.get("title", "")).strip() or "Untitled"


def supadata_get_transcript(video_url: str, supadata_api_key: str) -> str:
    """
    Fetch a plain-text transcript from Supadata.
    - GET /v1/transcript?url=...&text=true&mode=auto
    - If we get 202 with jobId, poll /v1/transcript/{jobId}
    """
    # Build the endpoint URL for "transcript".
    endpoint_url = f"{SUPADATA_API_BASE}/transcript"
    # Set API key header.
    headers = {"x-api-key": supadata_api_key}
    # Request plain text transcript.
    params = {"url": video_url, "text": "true", "mode": "auto"}

    # Try the request a few times in case of timeouts.
    last_error: Exception | None = None
    resp = None
    for attempt in range(1, SUPADATA_NETWORK_RETRIES + 1):
        try:
            resp = requests.get(endpoint_url, headers=headers, params=params, timeout=(15, 180))
            last_error = None
            break
        except (ReadTimeout, ConnectionError) as e:
            last_error = e
            print(f"    Supadata timed out (attempt {attempt}/{SUPADATA_NETWORK_RETRIES}). Retrying...")
            time.sleep(2)

    # If we never got a response, raise the last error.
    if resp is None:
        raise last_error or RuntimeError("Supadata request failed for an unknown reason.")

    # Handle invalid API key.
    if resp.status_code == 401:
        raise ValueError("Supadata API key is invalid (401). Check SUPADATA_API_KEY in .env.")

    # If it's not 200 or 202, raise an HTTP error.
    if resp.status_code not in (200, 202):
        resp.raise_for_status()

    # Parse JSON.
    data = resp.json()

    # If transcript is returned immediately.
    if resp.status_code == 200:
        return str(data.get("content", "")).strip()

    # Otherwise it's async.
    job_id = data.get("jobId")
    if not job_id:
        raise ValueError("Supadata returned 202 but no jobId: " + str(data))

    # Build the job status URL.
    job_url = f"{SUPADATA_API_BASE}/transcript/{job_id}"

    # Start polling.
    start_time = time.time()
    while True:
        # Stop if we've waited too long.
        if time.time() - start_time > SUPADATA_MAX_POLL_SECONDS:
            raise TimeoutError(f"Supadata transcript job timed out after {SUPADATA_MAX_POLL_SECONDS}s: {job_id}")

        # Wait a bit between polls.
        time.sleep(SUPADATA_POLL_INTERVAL_SECONDS)

        # Ask for status.
        job_resp = requests.get(job_url, headers=headers, timeout=(15, 60))
        job_resp.raise_for_status()
        job_data = job_resp.json()

        # Read status.
        status = str(job_data.get("status", "")).lower()
        if status == "completed":
            return str(job_data.get("content", "")).strip()
        if status == "failed":
            raise ValueError("Supadata transcript job failed: " + str(job_data))
        # Otherwise: queued/active -> keep polling.


def write_markdown(folder_name: str, title: str, video_url: str, transcript: str) -> str:
    """Save one markdown file for one video and return the file path."""
    # Create the folder path.
    folder_path = os.path.join(OUTPUT_DIR, folder_name)
    # Create folders if needed.
    os.makedirs(folder_path, exist_ok=True)

    # Build a safe filename from the title.
    filename = slugify_for_filename(title) + ".md"
    file_path = os.path.join(folder_path, filename)

    # If a file with the same name exists, append a number to avoid overwriting.
    if os.path.exists(file_path):
        base = slugify_for_filename(title, max_length=120)
        i = 2
        while True:
            candidate = os.path.join(folder_path, f"{base}-{i}.md")
            if not os.path.exists(candidate):
                file_path = candidate
                break
            i += 1

    # Build markdown content.
    lines: list[str] = []
    lines.append(f"# {title}")
    lines.append("")
    lines.append(f"- **URL**: {video_url}")
    lines.append("")
    lines.append("## Transcript")
    lines.append("")
    lines.append(transcript.strip() or "(No transcript returned)")
    lines.append("")

    # Write the file as UTF-8.
    with open(file_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    # Return where we saved.
    return file_path


# ----------------------------
# Main program
# ----------------------------

def main() -> None:
    """Run the whole script."""
    # Load .env into environment variables.
    load_dotenv()

    # Read Supadata key.
    supadata_api_key = (os.getenv("SUPADATA_API_KEY") or "").strip()
    # Stop early if missing.
    if not supadata_api_key:
        raise ValueError("Missing SUPADATA_API_KEY. Put it in .env (same as fetch_youtube.py).")

    # Check the input file exists.
    if not os.path.exists(EXTRA_VIDEOS_FILE):
        raise FileNotFoundError(f"Could not find {EXTRA_VIDEOS_FILE}")

    # Print paths so it’s easy to see what’s happening.
    print(f"Reading extra videos from: {EXTRA_VIDEOS_FILE}")
    print(f"Saving into: {OUTPUT_DIR}")

    # Read groups and URLs.
    groups = read_grouped_video_urls(EXTRA_VIDEOS_FILE)

    # Count total URLs.
    total = sum(len(urls) for urls in groups.values())
    if total == 0:
        print("No video URLs found in extravideos.txt.")
        return

    # Print quick summary.
    print(f"Found {total} video(s).")
    for folder, urls in groups.items():
        print(f"  - {folder}: {len(urls)} video(s)")

    # Process each group.
    done = 0
    for folder_name, urls in groups.items():
        # Skip empty groups.
        if not urls:
            continue
        print(f"\nProcessing group: {folder_name}")
        # Process each URL.
        for url in urls:
            done += 1
            print(f"  [{done}/{total}] Video: {url}")
            try:
                # Fetch title for filename.
                print("    Fetching title...")
                title = fetch_youtube_title(url)
                print(f"    Title: {title}")
                # Fetch transcript.
                print("    Fetching transcript from Supadata...")
                transcript = supadata_get_transcript(url, supadata_api_key)
                # Save file.
                path = write_markdown(folder_name, title, url, transcript)
                print(f"    Saved: {path}")
            except Exception as e:
                # If one video fails, we continue with the rest.
                print(f"    Failed. Skipping. Error: {e}")

    # Finished.
    print("\nDone.")


# Run main() only if this file is executed directly.
if __name__ == "__main__":
    main()

