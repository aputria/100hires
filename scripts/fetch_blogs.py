# fetch_blogs.py
# This script visits blog websites, extracts post content, and saves each post as a markdown file.
# Posts are organized into subfolders named after each author.

# --- Import Libraries ---
import requests  # Used to download web pages from the internet
from bs4 import BeautifulSoup  # Used to read and search through HTML code
import os  # Used to create folders and build file paths
import time  # Used to pause between requests so we don't overload servers
import re  # Used to search and replace text patterns
from urllib.parse import urljoin, urlparse  # Used to build full URLs from partial ones

# --- Settings ---
BASE_OUTPUT_DIR = "research/other/blogs"  # The root folder where all blog posts will be saved
DELAY_SECONDS = 2  # How many seconds to wait between each web request
MAX_POSTS_PER_SOURCE = 50  # Safety cap — stop after this many posts per source to avoid runaway scraping

# --- Browser Headers ---
# These headers make our requests look like they come from a real web browser
# Some websites block requests that don't look like a real browser
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",  # Tell the site we prefer English content
}

# --- Blog Sources ---
# Each entry defines: which URLs to visit, which author folder(s) to save to, and a display name
# If a source has two authors (like Higher Levels), the same post is saved to both folders
SOURCES = [
    {
        "urls": ["https://www.higherlevels.com/blog"],  # Higher Levels shared blog index
        "authors": ["connor-murray", "elric-legloire"],  # Both authors share this blog
        "name": "Higher Levels (Connor Murray & Elric Legloire)",  # Name shown in progress output
    },
    {
        "urls": ["https://newsletter.outbound.kitchen/t/launching-outbound"],  # Elric's newsletter archive
        "authors": ["elric-legloire"],  # Only Elric writes this newsletter
        "name": "Elric Legloire Newsletter",  # Name shown in progress output
    },
    {
        "urls": ["https://theampsocial.com/blog"],  # Morgan's AMP Social blog index
        "authors": ["morgan-j-ingram"],  # Morgan's author folder
        "name": "Morgan J Ingram (AMP Social)",  # Name shown in progress output
    },
    {
        "urls": [
            "https://joshbraun.com/learn/cold-email/",  # Josh's cold email learning section
            "https://joshbraun.com/learn/cold-calling/",  # Josh's cold calling learning section
        ],
        "authors": ["josh-braun"],  # Josh's author folder
        "name": "Josh Braun",  # Name shown in progress output
    },
    {
        "urls": ["https://www.30mpc.com/blog"],  # Armand's 30 Minutes to President's Club blog
        "authors": ["armand-farrokh"],  # Armand's author folder
        "name": "Armand Farrokh (30MPC)",  # Name shown in progress output
    },
    {
        "urls": ["https://jbarrows.com/blog"],  # John Barrows blog index
        "authors": ["john-barrows"],  # John's author folder
        "name": "John Barrows",  # Name shown in progress output
    },
]

# --- Tags and patterns that usually mean "not an article" ---
# We remove these from the HTML before extracting content
UNWANTED_TAGS = [
    "nav",       # Navigation menus
    "footer",    # Page footer
    "header",    # Page header (site logo, top nav)
    "aside",     # Sidebars
    "script",    # JavaScript code blocks
    "style",     # CSS style blocks
    "noscript",  # Fallback content for when JS is off
    "iframe",    # Embedded frames (ads, videos)
    "form",      # Forms (sign-up, search)
    "button",    # Clickable buttons
    "svg",       # Decorative icons/graphics
]

# CSS class and ID name fragments that suggest non-article content
UNWANTED_ATTR_PATTERNS = [
    "nav", "navigation",
    "footer", "header",
    "sidebar", "widget",
    "advertisement", "advert", "ads", "ad-",
    "cookie", "popup", "modal", "overlay",
    "menu", "breadcrumb", "pagination",
    "related-posts", "related_posts",
    "share", "social-share",
    "comment",
    "subscribe", "newsletter-form", "email-signup",
    "banner", "promo",
    "cta",  # Call to action boxes (usually sign-up prompts)
]

# Common CSS selectors to try when looking for the main article content
CONTENT_SELECTORS = [
    "article",               # Semantic HTML5 article tag
    "main",                  # Semantic HTML5 main tag
    ".post-content",         # WordPress standard class
    ".entry-content",        # WordPress standard class
    ".article-content",      # Generic article class
    ".blog-post-content",    # Blog-specific class
    ".post-body",            # Alternative post body class
    ".content-body",         # Alternative content class
    "[class*='post-content']",  # Any class containing "post-content"
    "[class*='article-body']",  # Any class containing "article-body"
    "[class*='entry-body']",    # Any class containing "entry-body"
    "[class*='blog-content']",  # Any class containing "blog-content"
    "[class*='page-content']",  # Any class containing "page-content"
]


def slugify(text):
    """Turn any string into a safe filename by replacing special characters with hyphens."""
    text = text.lower()  # Convert all characters to lowercase (e.g. "Hello World" → "hello world")
    text = re.sub(r"[^\w\s-]", "", text)  # Remove anything that isn't a letter, number, space, or hyphen
    text = re.sub(r"[\s_]+", "-", text)  # Replace spaces and underscores with a single hyphen
    text = re.sub(r"-+", "-", text)  # Collapse multiple hyphens in a row into one
    text = text.strip("-")  # Remove any hyphens at the very start or end
    return text[:100] if text else "untitled"  # Limit filename to 100 chars; fall back to "untitled"


def fetch_page(url):
    """Download a URL and return a BeautifulSoup object, or None if it fails."""
    print(f"    Fetching: {url}")  # Tell the user which page we're downloading right now
    try:  # Attempt the request and catch any errors
        response = requests.get(url, headers=HEADERS, timeout=20)  # Download the page; timeout after 20 seconds
        response.raise_for_status()  # Raise an exception if the server replied with an error (4xx / 5xx)
        return BeautifulSoup(response.text, "html.parser")  # Parse the HTML and return a searchable object
    except requests.RequestException as e:  # Catch network errors, timeouts, bad HTTP status, etc.
        print(f"    ERROR: Could not fetch {url} — {e}")  # Print the error so the user knows what happened
        return None  # Return nothing so the caller can check for failure


def remove_clutter(soup):
    """Strip navigation, ads, footers, and other non-article elements from the HTML."""
    for tag_name in UNWANTED_TAGS:  # Loop through each tag type we want to delete
        for element in soup.find_all(tag_name):  # Find every occurrence of this tag in the page
            element.decompose()  # Completely delete the element and everything inside it

    for pattern in UNWANTED_ATTR_PATTERNS:  # Loop through each class/ID pattern we want to remove
        # Remove any element whose class attribute contains this pattern (case-insensitive)
        for element in soup.find_all(class_=re.compile(pattern, re.I)):
            element.decompose()  # Delete the element
        # Remove any element whose id attribute contains this pattern (case-insensitive)
        for element in soup.find_all(id=re.compile(pattern, re.I)):
            element.decompose()  # Delete the element

    return soup  # Return the cleaned-up HTML tree


def extract_title(soup):
    """Find and return the blog post title from the HTML."""
    # Try these selectors in order — stop as soon as we find something
    for selector in ["h1", ".post-title", ".entry-title", ".article-title", "h2"]:
        tag = soup.select_one(selector)  # Look for this selector in the HTML
        if tag:  # If we found a matching element
            title = tag.get_text(strip=True)  # Pull out the text and remove leading/trailing whitespace
            if title:  # Make sure the text isn't empty
                break  # We found a good title, stop searching
    else:  # This "else" runs only if the for loop completed without a "break"
        page_title_tag = soup.find("title")  # Fall back to the <title> tag in the <head>
        title = page_title_tag.get_text(strip=True) if page_title_tag else "Untitled"  # Use page title or default

    # Remove common suffixes like " | Blog Name" or " – Site Name" that appear in browser tab titles
    title = re.sub(r"\s*[\|—–\-]\s*.{3,}$", "", title).strip()  # Strip everything after a separator character
    return title if title else "Untitled"  # Never return an empty string


def extract_body(soup):
    """Find and return the main article text from the HTML."""
    content_el = None  # Will hold the HTML element that contains the article

    for selector in CONTENT_SELECTORS:  # Try each selector in our priority list
        content_el = soup.select_one(selector)  # Look for this selector in the HTML
        if content_el:  # If we found something with meaningful text
            break  # Use this element and stop searching

    if not content_el:  # If none of our selectors matched
        content_el = soup.find("body")  # Fall all the way back to the entire page body

    if not content_el:  # If there's genuinely nothing to extract
        return ""  # Return an empty string

    # Convert the HTML element to plain text, inserting newlines between block elements
    raw_text = content_el.get_text(separator="\n", strip=True)

    # Clean up the raw text: split into lines, strip each one, remove blank lines, rejoin with blank lines between
    lines = [line.strip() for line in raw_text.splitlines()]  # Strip every individual line
    non_empty = [line for line in lines if line]  # Throw away blank lines
    return "\n\n".join(non_empty)  # Rejoin with a blank line between each paragraph


def save_markdown(title, body, source_url, author_folder):
    """Write a single blog post to a .md file inside the author's folder."""
    filename = slugify(title) + ".md"  # Build the filename from the post title
    filepath = os.path.join(BASE_OUTPUT_DIR, author_folder, filename)  # Combine folder path and filename

    # Build the full markdown file content
    content = f"# {title}\n\n"  # Level-1 heading with the post title
    content += f"**Source:** {source_url}\n\n"  # Source URL so we know where this came from
    content += "---\n\n"  # Horizontal rule to visually separate the header from the body
    content += body  # The main article text

    try:  # Try to write the file; catch any disk errors
        with open(filepath, "w", encoding="utf-8") as f:  # Open (or create) the file in write mode
            f.write(content)  # Write all the content to the file
        print(f"    Saved → {filepath}")  # Confirm where the file was saved
        return True  # Signal success
    except IOError as e:  # Catch errors like "permission denied" or "disk full"
        print(f"    ERROR: Could not save {filepath} — {e}")  # Show the error
        return False  # Signal failure


def collect_post_links(soup, index_url):
    """
    Scan a blog index page and return a list of URLs that look like individual posts.
    We only keep links that stay on the same domain and go deeper than the index page.
    """
    base_domain = urlparse(index_url).netloc  # The domain of the index page (e.g. "joshbraun.com")
    index_path = urlparse(index_url).path  # The path of the index page (e.g. "/blog")
    index_depth = len([p for p in index_path.split("/") if p])  # Number of path segments in the index URL

    # Patterns in URLs that indicate non-post pages we want to skip
    skip_url_patterns = [
        "/tag/", "/tags/",
        "/category/", "/categories/",
        "/author/", "/authors/",
        "/page/",
        "/search",
        "/login", "/signup", "/register",
        "#",  # In-page anchor links
        "mailto:",  # Email links
        "tel:",  # Phone links
    ]

    # File extensions we don't want to visit
    skip_extensions = [".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg",
                       ".pdf", ".zip", ".mp3", ".mp4", ".mov"]

    seen = set()  # Track URLs we've already added to avoid duplicates
    post_links = []  # The list of post URLs we'll return

    for a in soup.find_all("a", href=True):  # Loop through every <a href="..."> tag on the page
        href = a["href"].strip()  # Get the raw href value and remove any surrounding whitespace
        full_url = urljoin(index_url, href)  # Convert relative URLs to absolute (e.g. "/blog/post" → "https://...")
        full_url = full_url.split("#")[0]  # Remove any #anchor fragment from the URL
        full_url = full_url.rstrip("/")  # Remove trailing slash so "/blog/post/" and "/blog/post" match

        if not full_url:  # Skip if the URL became empty after cleaning
            continue

        parsed = urlparse(full_url)  # Break the URL into its parts (scheme, domain, path, etc.)

        if parsed.netloc != base_domain:  # Skip links that go to a different website
            continue

        if any(pattern in full_url for pattern in skip_url_patterns):  # Skip tag/category/author pages
            continue

        if any(full_url.lower().endswith(ext) for ext in skip_extensions):  # Skip image and media files
            continue

        post_path = parsed.path  # The path portion of this potential post URL
        post_depth = len([p for p in post_path.split("/") if p])  # Number of path segments

        # The post URL must be deeper in the hierarchy than the index page
        # For example: index is "/blog" (depth 1), post is "/blog/my-post" (depth 2) ✓
        # But a link back to "/" (depth 0) or "/blog" (depth 1) would be skipped ✗
        if post_depth <= index_depth:  # If the post URL is not deeper than the index
            continue  # Skip it — it's not a post

        if full_url in seen:  # Skip if we've already added this URL
            continue

        seen.add(full_url)  # Mark this URL as seen
        post_links.append(full_url)  # Add to our list of post URLs

    return post_links  # Return all valid post URLs found on this index page


def scrape_source(source):
    """Visit all URLs for one source, find posts, and save them to the author folder(s)."""
    print(f"\n{'=' * 60}")  # Print a visual divider line
    print(f"  SOURCE: {source['name']}")  # Show which blog/author we're scraping now
    print(f"{'=' * 60}")  # Print another divider

    # Create the output folder for each author linked to this source
    for author in source["authors"]:  # Loop through the list of authors for this source
        folder = os.path.join(BASE_OUTPUT_DIR, author)  # Build the full folder path
        os.makedirs(folder, exist_ok=True)  # Create the folder; do nothing if it already exists
        print(f"  Output folder ready: {folder}")  # Confirm the folder exists

    all_post_urls = []  # Master list of post URLs gathered from all index pages for this source

    # Step 1 — Visit each index URL and collect individual post links
    for index_url in source["urls"]:  # Loop through each index/section URL for this source
        print(f"\n  Visiting index: {index_url}")  # Show which index page we're visiting

        index_soup = fetch_page(index_url)  # Download and parse the index page

        if index_soup is None:  # If the download failed
            print(f"  Skipping index — could not load page.")  # Let the user know
            time.sleep(DELAY_SECONDS)  # Still wait before moving on
            continue  # Move to the next index URL

        time.sleep(DELAY_SECONDS)  # Be polite and wait before continuing

        post_urls = collect_post_links(index_soup, index_url)  # Find all post links on this index page

        if post_urls:  # If we found at least one post link
            print(f"  Found {len(post_urls)} post link(s) on this index page.")  # Show how many we found
            all_post_urls.extend(post_urls)  # Add them all to the master list
        else:  # If no post links were found
            print(f"  No sub-posts found — treating this URL as a single post.")  # Warn the user
            all_post_urls.append(index_url)  # Treat the index page itself as a post

    # Remove duplicates while keeping the original order
    seen = set()  # A set to track which URLs we've already added
    unique_post_urls = []  # The deduplicated list
    for url in all_post_urls:  # Loop through every URL collected
        if url not in seen:  # If we haven't added this URL yet
            seen.add(url)  # Mark it as seen
            unique_post_urls.append(url)  # Add it to the deduplicated list

    # Respect the per-source cap to avoid accidentally scraping hundreds of posts
    if len(unique_post_urls) > MAX_POSTS_PER_SOURCE:  # If we found more posts than the cap allows
        print(f"\n  Capping at {MAX_POSTS_PER_SOURCE} posts (found {len(unique_post_urls)} total).")  # Inform the user
        unique_post_urls = unique_post_urls[:MAX_POSTS_PER_SOURCE]  # Keep only the first N posts

    total = len(unique_post_urls)  # Total number of posts we'll scrape for this source
    print(f"\n  Total posts to scrape: {total}")  # Show the final count

    # Step 2 — Visit each post, extract content, and save it
    for i, post_url in enumerate(unique_post_urls, start=1):  # Loop with a counter starting at 1
        print(f"\n  [{i}/{total}] {post_url}")  # Show progress like [3/15]

        post_soup = fetch_page(post_url)  # Download and parse the blog post page

        if post_soup is None:  # If the download failed
            print(f"  Skipping — could not load this post.")  # Tell the user
            time.sleep(DELAY_SECONDS)  # Wait before the next request
            continue  # Move to the next post

        clean_soup = remove_clutter(post_soup)  # Remove nav, footer, ads, and other clutter

        title = extract_title(clean_soup)  # Pull out the post title
        body = extract_body(clean_soup)  # Pull out the main article text

        print(f"  Title: {title}")  # Show the extracted title so the user can verify it looks right

        if not body.strip():  # If we extracted nothing useful from the article
            print(f"  WARNING: No body content extracted — skipping this post.")  # Warn the user
            time.sleep(DELAY_SECONDS)  # Wait before the next request
            continue  # Skip to the next post

        # Save the post to each author folder linked to this source
        for author in source["authors"]:  # Loop through each author for this source
            save_markdown(title, body, post_url, author)  # Write the markdown file

        time.sleep(DELAY_SECONDS)  # Wait before the next request to be polite to the server

    print(f"\n  Finished scraping: {source['name']}")  # Confirm this source is done


def main():
    """Entry point — runs the full scraping pipeline for all sources."""
    print("=" * 60)  # Opening divider line
    print("  Blog Scraper — Starting")  # Welcome message
    print(f"  Saving posts to: {os.path.abspath(BASE_OUTPUT_DIR)}")  # Show the full output path
    print("=" * 60)  # Closing divider line

    os.makedirs(BASE_OUTPUT_DIR, exist_ok=True)  # Create the root output folder if it doesn't exist yet

    total_sources = len(SOURCES)  # How many sources we're going to scrape

    for i, source in enumerate(SOURCES, start=1):  # Loop through every source with a counter
        print(f"\n[Source {i} of {total_sources}]")  # Show overall source progress
        scrape_source(source)  # Scrape this source completely before moving to the next

    print("\n" + "=" * 60)  # Final divider
    print("  All done! Check the folder below for your markdown files:")  # Final message
    print(f"  {os.path.abspath(BASE_OUTPUT_DIR)}")  # Show the full path one more time
    print("=" * 60)  # Closing divider


if __name__ == "__main__":  # Only run main() if this file is executed directly (not imported as a module)
    main()  # Start everything
