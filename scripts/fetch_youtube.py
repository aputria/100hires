"""
fetch_youtube.py

A beginner-friendly script that:
- Reads YouTube channel URLs from channels.txt
- Uses YouTube Data API v3 to fetch the 5 most recent videos for each channel
- Uses Supadata API to fetch each video's transcript
- Saves each video to its own Markdown file under: research/youtube-transcripts/

Before running:
1) Install requirements: pip install -r requirements.txt
2) Create a .env file (see .env.example) with your API keys
3) Ensure channels.txt contains one channel URL per line (handles like https://www.youtube.com/@SomeHandle work great)
"""

# Import the built-in "os" module so we can read environment variables and build file paths.
import os
# Import the built-in "re" module for regular expressions (we use it to create safe filenames).
import re
# Import the built-in "time" module so we can wait between polling attempts for Supadata async jobs.
import time
# Import the built-in "urllib.parse" helpers so we can safely parse URLs and encode query parameters.
from urllib.parse import urlparse, quote

# Import "requests" (a popular HTTP library) so we can call web APIs (YouTube + Supadata).
import requests
# Import a specific error type so we can retry on timeouts nicely.
from requests.exceptions import ReadTimeout, ConnectionError
# Import "load_dotenv" so we can read API keys from a local .env file.
from dotenv import load_dotenv


# ----------------------------
# Configuration (easy to edit)
# ----------------------------

# Find the folder where this script file lives, so we can reliably load files next to it.
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# The file that contains one YouTube channel URL per line.
CHANNELS_FILE = os.path.join(SCRIPT_DIR, "channels.txt")

# Where we will save the output Markdown files (the script will create this folder if needed).
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "research", "youtube-transcripts")

# How many recent videos to fetch per channel.
VIDEOS_PER_CHANNEL = 5

# YouTube Data API v3 base URL (all YouTube API endpoints start with this).
YOUTUBE_API_BASE = "https://www.googleapis.com/youtube/v3"

# Supadata API base URL (all Supadata endpoints start with this).
SUPADATA_API_BASE = "https://api.supadata.ai/v1"

# How many times to retry Supadata network requests when they time out.
SUPADATA_NETWORK_RETRIES = 3

# If Supadata returns an async jobId, we'll poll until this many seconds have passed.
SUPADATA_MAX_POLL_SECONDS = 180

# How long to wait (in seconds) between polls when Supadata returns a jobId.
SUPADATA_POLL_INTERVAL_SECONDS = 1


# ----------------------------
# Helper functions
# ----------------------------

def slugify_for_filename(text: str, max_length: int = 120) -> str:
    """
    Convert text into a filename-safe slug.
    Example: "John Barrows / Sales Tips!" -> "john-barrows-sales-tips"
    """
    # Convert to lowercase so filenames are consistent.
    text = text.lower()
    # Replace any character that is NOT a letter or number with a hyphen.
    text = re.sub(r"[^a-z0-9]+", "-", text)
    # Remove leading and trailing hyphens.
    text = text.strip("-")
    # If the result is empty (e.g. title was only symbols), fall back to a safe default.
    if not text:
        text = "untitled"
    # Truncate so Windows paths don't get too long.
    return text[:max_length]


def channel_folder_name_from_url_and_title(channel_url: str, channel_title: str) -> str:
    """
    Decide which subfolder to use for a channel.

    The user asked: "Put each @ into one folder".
    So if the channel URL is a handle URL (https://youtube.com/@handle), we use that handle as the folder name.

    Special case requested by the user:
    - @30MPC should be stored under a folder named "Eric-Finch"
    """
    # Try to get the handle from the channel URL.
    handle = extract_handle_from_channel_url(channel_url)
    # If this is the @30MPC handle, rename the folder to Eric-Finch as requested.
    if handle and handle.lower() == "30mpc":
        return "Eric-Finch"
    # If we have a handle, use it (without the @) as the folder name.
    if handle:
        return handle
    # Fallback: if we don't have a handle, use a safe slug from the channel title.
    return slugify_for_filename(channel_title, max_length=60)


def get_existing_video_ids(output_dir: str) -> set[str]:
    """
    Look at the existing .md files and extract YouTube video IDs from filenames.

    Our files are named like:
      author-title-VIDEO_ID.md

    YouTube video IDs are typically 11 characters (letters/numbers/_/-), so we search for that.
    This lets the script be restarted safely: it will skip videos already saved.
    """
    # Create an empty set for fast "already done?" checks.
    existing: set[str] = set()
    # If the folder doesn't exist yet, there is nothing to skip.
    if not os.path.isdir(output_dir):
        return existing
    # Walk through the folder AND all subfolders (because we store each channel in its own folder).
    for root, _, files in os.walk(output_dir):
        for name in files:
            # Only consider markdown files.
            if not name.lower().endswith(".md"):
                continue
            # Try to find an 11-character video id before ".md", after a "-" separator.
            match = re.search(r"-([A-Za-z0-9_-]{11})\.md$", name)
            # If we found a match, add it to the set.
            if match:
                existing.add(match.group(1))
    # Return all video IDs we've already saved.
    return existing


def auto_organize_existing_files(output_dir: str) -> None:
    """
    Move existing markdown files from the root output folder into channel subfolders.
    """
    # If the output folder does not exist, there is nothing to organize.
    if not os.path.isdir(output_dir):
        return

    # A small mapping from old filename prefixes to the desired folder name.
    prefix_to_folder = {
        "30-minutes-to-president-s-club": "Eric-Finch",             # @30MPC -> Eric-Finch (requested)
        "connor-murray": "Connor-Murray",                           # @Connor-Murray
        "tech-sales-with-higher-levels": "techsales-higherlevels",  # @techsales-higherlevels
        "josh-braun": "joshbraunsales",                             # @joshbraunsales
        "morgan-j-ingram": "MorganJIngram",                         # @MorganJIngram (just in case)
    }

    # List the files in the root of output_dir only (not subfolders).
    for name in os.listdir(output_dir):
        # We only want to move markdown files.
        if not name.lower().endswith(".md"):
            continue

        # Build the current file path.
        src_path = os.path.join(output_dir, name)
        # If it's not a file (e.g. it's a folder), skip.
        if not os.path.isfile(src_path):
            continue

        # Decide which folder to move it into.
        target_folder: str | None = None
        for prefix, folder in prefix_to_folder.items():
            if name.startswith(prefix + "-"):
                target_folder = folder
                break

        # If we don't recognize the prefix, leave the file where it is.
        if not target_folder:
            continue

        # Create the destination folder.
        dest_dir = os.path.join(output_dir, target_folder)
        os.makedirs(dest_dir, exist_ok=True)

        # Move the file (rename works as a move within the same drive).
        dest_path = os.path.join(dest_dir, name)
        # If a file already exists at the destination, do not overwrite it.
        if os.path.exists(dest_path):
            continue

        os.rename(src_path, dest_path)


def read_channels_from_file(path: str) -> list[str]:
    """Read channel URLs from a text file (one URL per line)."""
    # Create an empty list that will hold the cleaned URLs.
    channels: list[str] = []
    # Create a set to remember which URLs we've already added (so duplicates are skipped).
    seen: set[str] = set()
    # Open the file in text mode with UTF-8 encoding.
    with open(path, "r", encoding="utf-8") as f:
        # Loop over each line in the file.
        for line in f:
            # Remove whitespace at the start/end of the line (including the newline).
            url = line.strip()
            # Ignore blank lines.
            if not url:
                continue
            # Ignore comment lines that start with "#".
            if url.startswith("#"):
                continue
            # If we've already seen this exact URL, skip it (this avoids duplicate work).
            if url in seen:
                continue
            # Remember it and add it to the final list.
            seen.add(url)
            channels.append(url)
    # Return the list of channel URLs.
    return channels


def extract_handle_from_channel_url(channel_url: str) -> str | None:
    """
    Try to extract a YouTube handle from a channel URL like:
    - https://www.youtube.com/@SomeHandle
    - https://youtube.com/@SomeHandle
    Returns "SomeHandle" (without the @) or None if we can't find it.
    """
    # Parse the URL into parts (scheme, host, path, etc.).
    parsed = urlparse(channel_url)
    # Get the path part (example: "/@30MPC").
    path = parsed.path or ""
    # Split path by "/" and remove empty pieces.
    parts = [p for p in path.split("/") if p]
    # If the first path segment starts with "@", that's the handle.
    if parts and parts[0].startswith("@"):
        # Remove the "@" and return the handle.
        return parts[0][1:]
    # If we didn't find a handle, return None.
    return None


def extract_channel_id_from_url(channel_url: str) -> str | None:
    """
    Try to extract a channelId from a URL like:
    - https://www.youtube.com/channel/UCxxxx...
    Returns the UC... string or None.
    """
    # Parse the URL into parts (scheme, host, path, etc.).
    parsed = urlparse(channel_url)
    # Get the path part (example: "/channel/UCabc123").
    path = parsed.path or ""
    # Split path by "/" and remove empty pieces.
    parts = [p for p in path.split("/") if p]
    # If the URL path looks like /channel/<id>, return that id.
    if len(parts) >= 2 and parts[0].lower() == "channel":
        return parts[1]
    # Otherwise return None.
    return None


def youtube_get(endpoint: str, params: dict, youtube_api_key: str) -> dict:
    """
    Call a YouTube Data API endpoint and return JSON as a Python dict.
    This raises an exception if the HTTP request fails.
    """
    # Copy params so we don't accidentally modify the caller's dict.
    params = dict(params)
    # Add the YouTube API key to every request as the "key" parameter.
    params["key"] = youtube_api_key
    # Build the full URL (example: https://www.googleapis.com/youtube/v3/search).
    url = f"{YOUTUBE_API_BASE}/{endpoint}"
    # Make a GET request with a short timeout so it doesn't hang forever.
    resp = requests.get(url, params=params, timeout=30)
    # If the response status is not 200-299, raise an HTTP error.
    resp.raise_for_status()
    # Convert JSON text into a Python dict and return it.
    return resp.json()


def resolve_channel_id(channel_url: str, youtube_api_key: str) -> str:
    """
    Convert a channel URL to a channelId (UC...).
    We support:
    - /channel/UC... URLs (direct extraction)
    - /@handle URLs (we use YouTube "channels" API forHandle, which is precise)
    """
    # First, see if the URL directly contains /channel/<id>.
    direct_id = extract_channel_id_from_url(channel_url)
    # If we found a direct channelId, return it immediately.
    if direct_id:
        return direct_id

    # Next, try to extract a handle like "@30MPC".
    handle = extract_handle_from_channel_url(channel_url)
    # If there's no handle, we can't reliably resolve this URL in a beginner script.
    if not handle:
        raise ValueError(f"Unsupported channel URL format: {channel_url}")

    # Prefer the official "forHandle" parameter (this is much more reliable than search).
    # Docs: https://developers.google.com/youtube/v3/docs/channels/list
    channel_data = youtube_get(
        endpoint="channels",
        params={
            "part": "id",
            "forHandle": handle,
            "maxResults": 1,
        },
        youtube_api_key=youtube_api_key,
    )

    # YouTube returns channel data under "items".
    items = channel_data.get("items", [])
    # If we found a channel, return its id.
    if items:
        return items[0]["id"]

    # Fallback (rare): if forHandle fails, try a search as a last resort.
    # This can still be wrong sometimes, but it's better than giving up immediately.
    search_data = youtube_get(
        endpoint="search",
        params={
            "part": "snippet",
            "type": "channel",
            "maxResults": 1,
            "q": f"@{handle}",
        },
        youtube_api_key=youtube_api_key,
    )

    # Pull out the first item from the search results.
    items = search_data.get("items", [])
    # If there are no items, we couldn't find the channel.
    if not items:
        raise ValueError(f"Could not resolve handle '@{handle}' to a channelId.")

    # The channelId is stored inside item["id"]["channelId"] for channel search results.
    return items[0]["id"]["channelId"]


def get_uploads_playlist_id(channel_id: str, youtube_api_key: str) -> tuple[str, str]:
    """
    Given a channelId, fetch:
    - uploads playlist ID (where the channel's uploads live)
    - channel title (author name)
    Returns (uploads_playlist_id, channel_title).
    """
    # Call the YouTube "channels" endpoint to fetch contentDetails + snippet for the channel.
    channel_data = youtube_get(
        endpoint="channels",
        params={
            "part": "contentDetails,snippet",
            "id": channel_id,
            "maxResults": 1,
        },
        youtube_api_key=youtube_api_key,
    )

    # YouTube returns channel data under "items".
    items = channel_data.get("items", [])
    # If no channel was returned, the channelId might be invalid.
    if not items:
        raise ValueError(f"Channel not found for channelId: {channel_id}")

    # Grab the first (and only) channel object.
    channel_obj = items[0]
    # Read the channel title (we use this as the author name).
    channel_title = channel_obj["snippet"]["title"]
    # Find the uploads playlist ID from contentDetails.
    uploads_playlist_id = channel_obj["contentDetails"]["relatedPlaylists"]["uploads"]
    # Return both values.
    return uploads_playlist_id, channel_title


def get_recent_video_ids_from_uploads_playlist(uploads_playlist_id: str, youtube_api_key: str, limit: int) -> list[str]:
    """
    Fetch the most recent video IDs from the channel uploads playlist.
    """
    # Call the YouTube "playlistItems" endpoint to fetch items from the uploads playlist.
    playlist_data = youtube_get(
        endpoint="playlistItems",
        params={
            "part": "contentDetails",
            "playlistId": uploads_playlist_id,
            "maxResults": limit,
        },
        youtube_api_key=youtube_api_key,
    )

    # The playlist items live under "items".
    items = playlist_data.get("items", [])
    # Create a list of video IDs from each playlist item.
    video_ids = [item["contentDetails"]["videoId"] for item in items]
    # Return the list of IDs.
    return video_ids


def get_video_details(video_id: str, youtube_api_key: str) -> dict:
    """
    Fetch title, publish date, description, and channelTitle (author) for a video.
    Returns a dict with keys: title, published_at, url, description, author.
    """
    # Call the YouTube "videos" endpoint to fetch snippet fields for this video ID.
    video_data = youtube_get(
        endpoint="videos",
        params={
            "part": "snippet",
            "id": video_id,
            "maxResults": 1,
        },
        youtube_api_key=youtube_api_key,
    )

    # The video data lives under "items".
    items = video_data.get("items", [])
    # If there are no items, the video might be private/deleted.
    if not items:
        raise ValueError(f"Video not found or inaccessible: {video_id}")

    # Grab the first (and only) video object.
    vid = items[0]
    # The snippet object contains human-friendly fields like title and description.
    snippet = vid["snippet"]

    # Extract the fields we need.
    title = snippet.get("title", "").strip()
    published_at = snippet.get("publishedAt", "").strip()
    description = snippet.get("description", "").strip()
    author = snippet.get("channelTitle", "").strip()
    url = f"https://www.youtube.com/watch?v={video_id}"

    # Return them as a dict.
    return {
        "video_id": video_id,
        "title": title,
        "published_at": published_at,
        "url": url,
        "description": description,
        "author": author,
    }


def supadata_get_transcript(video_url: str, supadata_api_key: str) -> str:
    """
    Fetch a plain-text transcript from Supadata.
    - Uses GET /v1/transcript?url=...&text=true&mode=auto
    - If Supadata returns 202 with a jobId, poll /v1/transcript/{jobId} until completed.
    Returns the transcript content as a string.
    """
    # Build the endpoint URL for "transcript".
    endpoint_url = f"{SUPADATA_API_BASE}/transcript"
    # Set the required API key header.
    headers = {"x-api-key": supadata_api_key}
    # Build query parameters (we want plain text).
    params = {
        "url": video_url,
        "text": "true",
        "mode": "auto",
    }

    # Make the initial request to Supadata.
    # We use a (connect_timeout, read_timeout) tuple so slow responses don't kill the script too quickly.
    # We also retry a few times on network timeouts, because these APIs can be occasionally slow.
    last_error: Exception | None = None
    resp = None
    for attempt in range(1, SUPADATA_NETWORK_RETRIES + 1):
        try:
            resp = requests.get(endpoint_url, headers=headers, params=params, timeout=(15, 180))
            last_error = None
            break
        except (ReadTimeout, ConnectionError) as e:
            last_error = e
            print(f"    Supadata request timed out (attempt {attempt}/{SUPADATA_NETWORK_RETRIES}). Retrying...")
            time.sleep(2)
    if resp is None:
        raise last_error or RuntimeError("Supadata request failed for an unknown reason.")

    # If the request is unauthorized, raise a clearer error.
    if resp.status_code == 401:
        raise ValueError("Supadata API key is invalid (401 Unauthorized). Check SUPADATA_API_KEY in .env.")

    # If Supadata returned a standard error (4xx/5xx), raise an exception.
    # Note: 202 is not an error; it means "accepted, processing".
    if resp.status_code not in (200, 202):
        resp.raise_for_status()

    # Parse JSON response into a dict.
    data = resp.json()

    # If we got transcript content immediately (HTTP 200), return it.
    if resp.status_code == 200:
        # The plain transcript is in data["content"] when text=true.
        return str(data.get("content", "")).strip()

    # Otherwise, we got an async jobId (HTTP 202), so we must poll for results.
    job_id = data.get("jobId")
    # If there's no jobId, something is wrong.
    if not job_id:
        raise ValueError("Supadata returned 202 but no jobId. Response was: " + str(data))

    # Build the job status endpoint: /v1/transcript/{jobId}
    job_url = f"{SUPADATA_API_BASE}/transcript/{job_id}"

    # Remember when we started so we can stop after a time limit.
    start_time = time.time()

    # Poll until we either complete, fail, or time out.
    while True:
        # If we've been polling too long, stop and raise an error.
        if time.time() - start_time > SUPADATA_MAX_POLL_SECONDS:
            raise TimeoutError(f"Supadata transcript job timed out after {SUPADATA_MAX_POLL_SECONDS} seconds: {job_id}")

        # Wait a short time between polls (recommended by Supadata docs).
        time.sleep(SUPADATA_POLL_INTERVAL_SECONDS)

        # Request the job status.
        job_resp = requests.get(job_url, headers=headers, timeout=(15, 60))
        # If this fails, raise an error.
        job_resp.raise_for_status()
        # Parse the job JSON.
        job_data = job_resp.json()

        # Read the job status (queued, active, completed, failed).
        status = job_data.get("status", "").lower()

        # If it's completed, return the transcript content.
        if status == "completed":
            return str(job_data.get("content", "")).strip()

        # If it failed, raise an error with details.
        if status == "failed":
            raise ValueError("Supadata transcript job failed: " + str(job_data))

        # Otherwise it's still queued/active; continue polling.


def write_markdown_file(video: dict, transcript: str) -> str:
    """
    Write one Markdown file per video, and return the file path.
    Filename format: author-name-video-title.md
    """
    # Make sure the output directory exists (create it if it doesn't).
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # Create safe filename parts.
    author_slug = slugify_for_filename(video.get("author", "unknown-author"))
    title_slug = slugify_for_filename(video.get("title", "untitled"))

    # Build the base filename.
    base_name = f"{author_slug}-{title_slug}"

    # Create a full path (we also include video_id to avoid collisions).
    file_name = f"{base_name}-{video['video_id']}.md"
    file_path = os.path.join(OUTPUT_DIR, file_name)

    # Build the Markdown content.
    md_lines: list[str] = []
    md_lines.append(f"# {video.get('title', '').strip()}")
    md_lines.append("")
    md_lines.append(f"- **Publish date**: {video.get('published_at', '').strip()}")
    md_lines.append(f"- **URL**: {video.get('url', '').strip()}")
    md_lines.append("")
    md_lines.append("## Description")
    md_lines.append("")
    md_lines.append(video.get("description", "").strip() or "(No description)")
    md_lines.append("")
    md_lines.append("## Transcript")
    md_lines.append("")
    md_lines.append(transcript.strip() or "(No transcript returned)")
    md_lines.append("")

    # Write the file using UTF-8 encoding (so emojis and non-English text work).
    with open(file_path, "w", encoding="utf-8") as f:
        # Join the lines with newlines and write them to disk.
        f.write("\n".join(md_lines))

    # Return the file path so we can print it.
    return file_path


def write_markdown_file_to_channel_folder(video: dict, transcript: str, channel_folder_name: str) -> str:
    """
    Write one Markdown file per video inside a per-channel folder:
      research/youtube-transcripts/<channel_folder_name>/
    """
    # Build the channel-specific output directory.
    channel_output_dir = os.path.join(OUTPUT_DIR, channel_folder_name)
    # Make sure the channel folder exists.
    os.makedirs(channel_output_dir, exist_ok=True)

    # Create safe filename parts.
    author_slug = slugify_for_filename(video.get("author", "unknown-author"))
    title_slug = slugify_for_filename(video.get("title", "untitled"))

    # Build the base filename.
    base_name = f"{author_slug}-{title_slug}"

    # Create a full path (we also include video_id to avoid collisions).
    file_name = f"{base_name}-{video['video_id']}.md"
    file_path = os.path.join(channel_output_dir, file_name)

    # Build the Markdown content.
    md_lines: list[str] = []
    md_lines.append(f"# {video.get('title', '').strip()}")
    md_lines.append("")
    md_lines.append(f"- **Publish date**: {video.get('published_at', '').strip()}")
    md_lines.append(f"- **URL**: {video.get('url', '').strip()}")
    md_lines.append("")
    md_lines.append("## Description")
    md_lines.append("")
    md_lines.append(video.get("description", "").strip() or "(No description)")
    md_lines.append("")
    md_lines.append("## Transcript")
    md_lines.append("")
    md_lines.append(transcript.strip() or "(No transcript returned)")
    md_lines.append("")

    # Write the file using UTF-8 encoding (so emojis and non-English text work).
    with open(file_path, "w", encoding="utf-8") as f:
        # Join the lines with newlines and write them to disk.
        f.write("\n".join(md_lines))

    # Return the file path so we can print it.
    return file_path


# ----------------------------
# Main program
# ----------------------------

def main() -> None:
    """Main entry point for the script."""
    # Load environment variables from a local .env file (if present).
    load_dotenv()

    # Read the YouTube API key from environment variables.
    youtube_api_key = os.getenv("YOUTUBE_API_KEY", "").strip()
    # Read the Supadata API key from environment variables.
    supadata_api_key = os.getenv("SUPADATA_API_KEY", "").strip()

    # If the YouTube key is missing, stop with a helpful message.
    if not youtube_api_key:
        raise ValueError("Missing YOUTUBE_API_KEY. Create a .env file (see .env.example).")

    # If the Supadata key is missing, stop with a helpful message.
    if not supadata_api_key:
        raise ValueError("Missing SUPADATA_API_KEY. Create a .env file (see .env.example).")

    # Print where we are reading/writing so it's easy to debug path issues.
    print(f"Script folder: {SCRIPT_DIR}")
    print(f"Reading channels from: {CHANNELS_FILE}")
    print(f"Saving markdown files to: {OUTPUT_DIR}")

    # If channels.txt doesn't exist, stop early.
    if not os.path.exists(CHANNELS_FILE):
        raise FileNotFoundError(f"Could not find {CHANNELS_FILE} in the current folder.")

    # Print a friendly message so you know the script started.
    print("Starting YouTube + transcript fetch...")

    # Move any existing markdown files from the root folder into the right subfolders.
    auto_organize_existing_files(OUTPUT_DIR)

    # Build a set of videos we've already saved, so rerunning continues where it left off.
    existing_video_ids = get_existing_video_ids(OUTPUT_DIR)
    # Print how many are already saved, so you know the resume feature is working.
    if existing_video_ids:
        print(f"Found {len(existing_video_ids)} already-saved video(s). Will skip them.")

    # Read all channel URLs from channels.txt.
    channel_urls = read_channels_from_file(CHANNELS_FILE)

    # If there are no channels listed, stop early.
    if not channel_urls:
        print("No channels found in channels.txt. Add one URL per line and try again.")
        return

    # Print how many channels we will process.
    print(f"Found {len(channel_urls)} channel URL(s).")

    # Loop over each channel URL in the file.
    for channel_index, channel_url in enumerate(channel_urls, start=1):
        # Print progress for this channel.
        print(f"\n[{channel_index}/{len(channel_urls)}] Resolving channel: {channel_url}")

        # Resolve the URL to a channelId (UC...).
        channel_id = resolve_channel_id(channel_url, youtube_api_key)

        # Get the uploads playlist ID and the channel title (author name).
        uploads_playlist_id, channel_title = get_uploads_playlist_id(channel_id, youtube_api_key)

        # Decide which per-channel folder to use (based on the @handle URL).
        channel_folder_name = channel_folder_name_from_url_and_title(channel_url, channel_title)

        # Print which channel we matched.
        print(f"Channel resolved: {channel_title} (channelId={channel_id})")
        print(f"Saving this channel under folder: {channel_folder_name}")

        # Fetch the most recent video IDs.
        print(f"Fetching {VIDEOS_PER_CHANNEL} most recent videos...")
        video_ids = get_recent_video_ids_from_uploads_playlist(uploads_playlist_id, youtube_api_key, VIDEOS_PER_CHANNEL)

        # If the channel has no videos, skip it.
        if not video_ids:
            print("No videos found for this channel. Skipping.")
            continue

        # Loop over each video ID.
        for video_index, video_id in enumerate(video_ids, start=1):
            # Print progress for this video.
            print(f"  - [{video_index}/{len(video_ids)}] Fetching video details: {video_id}")

            # If we already saved this video earlier, skip it.
            if video_id in existing_video_ids:
                print("    Already saved. Skipping.")
                continue

            # Fetch video details (title, date, description, etc.).
            video = get_video_details(video_id, youtube_api_key)

            # Print the title for clarity.
            print(f"    Title: {video['title']}")

            # Fetch transcript from Supadata.
            print("    Fetching transcript from Supadata (this can take a bit for long videos)...")
            try:
                transcript_text = supadata_get_transcript(video["url"], supadata_api_key)
            except Exception as e:
                # If transcript fetching fails, print the error and continue with the next video.
                # This way, one slow/failed video does not stop the entire run.
                print(f"    Transcript fetch failed for this video. Skipping. Error: {e}")
                continue

            # Save the Markdown file.
            output_path = write_markdown_file_to_channel_folder(video, transcript_text, channel_folder_name)
            # Record this as done so we skip it if the script is restarted again in the same run.
            existing_video_ids.add(video_id)

            # Print where the file was saved.
            print(f"    Saved: {output_path}")

    # Print a final message when everything is done.
    print("\nDone. All transcripts (that were accessible) have been saved.")


# This line makes sure the script only runs when you execute it directly:
# python fetch_youtube.py
if __name__ == "__main__":
    main()

