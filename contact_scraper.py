"""
Contact Scraper App
====================
Searches Google (via the official Custom Search API) for a query, then visits
each resulting website to extract publicly listed contact details (emails,
phone numbers, social links).

SETUP
-----
1. Get a Google Custom Search API key + Search Engine ID (CX):
   - API key:     https://console.cloud.google.com/apis/credentials
                  (enable "Custom Search API" for your project)
   - Search Engine ID: https://programmablesearchengine.google.com/
                  (create an engine, set it to "search the entire web")
2. pip install requests beautifulsoup4
3. Set the two values below (or as environment variables) and run:
       python contact_scraper.py "dentists in Austin TX" 20

NOTES / ETHICS
--------------
- This uses the official Search API (not scraping Google's result pages),
  which is the only reliable + ToS-compliant way to get search results.
- When visiting target websites, this script:
    * checks robots.txt before crawling
    * identifies itself with a real User-Agent
    * rate-limits requests (default: 1 request/sec)
- Only scrapes information that is already publicly published on the page.
- You are responsible for complying with applicable laws (e.g. CAN-SPAM,
  GDPR, CCPA) when using any contact data you collect, especially for
  outbound marketing.
"""

import os
import re
import csv
import time
import argparse
import urllib.robotparser
from urllib.parse import urlparse, urljoin

import requests
from bs4 import BeautifulSoup

# ----------------------------------------------------------------------
# CONFIG — fill these in, or set as environment variables of the same name
# ----------------------------------------------------------------------
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY", "YOUR_API_KEY_HERE")
GOOGLE_CX = os.environ.get("GOOGLE_CX", "YOUR_SEARCH_ENGINE_ID_HERE")

REQUEST_DELAY_SECONDS = 1.0     # politeness delay between site requests
REQUEST_TIMEOUT = 10
USER_AGENT = "Mozilla/5.0 (compatible; ContactScraperBot/1.0; +https://example.com/bot)"

CONTACT_PATHS = ["", "contact", "contact-us", "about", "about-us", "support"]

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
PHONE_RE = re.compile(
    r"(?:(?:\+?\d{1,3}[\s.\-]?)?(?:\(?\d{2,4}\)?[\s.\-]?){2,4}\d{3,4})"
)
SOCIAL_DOMAINS = ["linkedin.com", "twitter.com", "x.com", "facebook.com", "instagram.com"]


# ----------------------------------------------------------------------
# Step 1: Google Custom Search API
# ----------------------------------------------------------------------
def google_search(query, num_results=10):
    """Return a list of result URLs using the official Custom Search JSON API."""
    if GOOGLE_API_KEY.startswith("YOUR_") or GOOGLE_CX.startswith("YOUR_"):
        raise RuntimeError(
            "Set GOOGLE_API_KEY and GOOGLE_CX (env vars or in the script) "
            "before running. See the module docstring for setup steps."
        )

    urls = []
    url = "https://www.googleapis.com/customsearch/v1"
    start = 1
    while len(urls) < num_results:
        batch_size = min(10, num_results - len(urls))  # API max is 10 per call
        params = {
            "key": GOOGLE_API_KEY,
            "cx": GOOGLE_CX,
            "q": query,
            "num": batch_size,
            "start": start,
        }
        resp = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        items = data.get("items", [])
        if not items:
            break
        urls.extend(item["link"] for item in items)
        start += batch_size
        if start > 91:  # API only paginates up to ~100 results
            break
    return urls[:num_results]


# ----------------------------------------------------------------------
# Step 2: robots.txt check
# ----------------------------------------------------------------------
def can_fetch(url):
    try:
        parsed = urlparse(url)
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
        rp = urllib.robotparser.RobotFileParser()
        rp.set_url(robots_url)
        rp.read()
        return rp.can_fetch(USER_AGENT, url)
    except Exception:
        # If robots.txt can't be read, proceed cautiously (assume allowed)
        return True


# ----------------------------------------------------------------------
# Step 3: extract contact details from a page
# ----------------------------------------------------------------------
def extract_contacts_from_html(html, base_url):
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ")

    emails = set(EMAIL_RE.findall(text))
    # also check mailto: links directly
    for a in soup.find_all("a", href=True):
        if a["href"].lower().startswith("mailto:"):
            emails.add(a["href"].split("mailto:")[1].split("?")[0])

    phones = set(m.strip() for m in PHONE_RE.findall(text) if len(re.sub(r"\D", "", m)) >= 7)

    socials = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        for domain in SOCIAL_DOMAINS:
            if domain in href:
                socials.add(urljoin(base_url, href))

    return {
        "emails": emails,
        "phones": phones,
        "socials": socials,
    }


def fetch_page(url):
    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT)
        if resp.status_code == 200:
            return resp.text
    except requests.RequestException:
        pass
    return None


def scrape_site_for_contacts(base_url):
    """Try the homepage plus a few common contact-page paths."""
    parsed = urlparse(base_url)
    root = f"{parsed.scheme}://{parsed.netloc}"

    all_emails, all_phones, all_socials = set(), set(), set()

    for path in CONTACT_PATHS:
        candidate = urljoin(root + "/", path)
        if not can_fetch(candidate):
            continue
        html = fetch_page(candidate)
        time.sleep(REQUEST_DELAY_SECONDS)
        if not html:
            continue
        found = extract_contacts_from_html(html, candidate)
        all_emails |= found["emails"]
        all_phones |= found["phones"]
        all_socials |= found["socials"]

        # Stop early once we've found emails — no need to hit every path
        if all_emails:
            break

    return {
        "url": base_url,
        "emails": "; ".join(sorted(all_emails)),
        "phones": "; ".join(sorted(all_phones)),
        "socials": "; ".join(sorted(all_socials)),
    }


# ----------------------------------------------------------------------
# Main pipeline
# ----------------------------------------------------------------------
def run(query, num_results, output_csv):
    print(f"Searching for: {query!r} ({num_results} results requested)")
    urls = google_search(query, num_results)
    print(f"Found {len(urls)} result URLs.")

    rows = []
    for i, url in enumerate(urls, 1):
        print(f"[{i}/{len(urls)}] Scraping {url} ...")
        try:
            row = scrape_site_for_contacts(url)
        except Exception as e:
            row = {"url": url, "emails": "", "phones": "", "socials": f"ERROR: {e}"}
        rows.append(row)

    with open(output_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["url", "emails", "phones", "socials"])
        writer.writeheader()
        writer.writerows(rows)

    print(f"\nDone. Results saved to {output_csv}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Search Google and scrape contact details from results.")
    parser.add_argument("query", help="Search query, e.g. 'plumbers in Denver CO'")
    parser.add_argument("num_results", nargs="?", type=int, default=10, help="Number of search results to scan")
    parser.add_argument("--output", default="contacts.csv", help="Output CSV file path")
    args = parser.parse_args()

    run(args.query, args.num_results, args.output)
