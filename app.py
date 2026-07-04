"""
Contact Scraper — Streamlit App
================================
Deploy this on Streamlit Community Cloud (share.streamlit.io / *.streamlit.app).

SETUP BEFORE DEPLOYING
-----------------------
1. Get Google Custom Search API credentials:
   - API key: https://console.cloud.google.com/apis/credentials
              (enable "Custom Search API")
   - Search Engine ID (CX): https://programmablesearchengine.google.com/
              (create an engine, set "Search the entire web" ON)

2. Push this repo (app.py + requirements.txt) to GitHub.

3. On Streamlit Community Cloud, add your credentials as SECRETS
   (Settings -> Secrets), NOT as code:

       GOOGLE_API_KEY = "your_api_key_here"
       GOOGLE_CX = "your_search_engine_id_here"

   Locally, create a file .streamlit/secrets.toml with the same content
   (this file should be in .gitignore — never commit real keys).

4. Deploy. The app reads credentials via st.secrets automatically.
"""

import re
import time
import urllib.robotparser
from urllib.parse import urlparse, urljoin

import requests
import pandas as pd
import streamlit as st
from bs4 import BeautifulSoup

# ----------------------------------------------------------------------
# CONFIG
# ----------------------------------------------------------------------
REQUEST_TIMEOUT = 10
USER_AGENT = "Mozilla/5.0 (compatible; ContactScraperBot/1.0; +https://example.com/bot)"
CONTACT_PATHS = ["", "contact", "contact-us", "about", "about-us", "support"]

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
PHONE_RE = re.compile(r"(?:(?:\+?\d{1,3}[\s.\-]?)?(?:\(?\d{2,4}\)?[\s.\-]?){2,4}\d{3,4})")
SOCIAL_DOMAINS = ["linkedin.com", "twitter.com", "x.com", "facebook.com", "instagram.com"]


# ----------------------------------------------------------------------
# Google Custom Search API
# ----------------------------------------------------------------------
def google_search(query, num_results, api_key, cx):
    urls = []
    url = "https://www.googleapis.com/customsearch/v1"
    start = 1
    while len(urls) < num_results:
        batch_size = min(10, num_results - len(urls))
        params = {"key": api_key, "cx": cx, "q": query, "num": batch_size, "start": start}
        resp = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        items = data.get("items", [])
        if not items:
            break
        urls.extend(item["link"] for item in items)
        start += batch_size
        if start > 91:
            break
    return urls[:num_results]


# ----------------------------------------------------------------------
# robots.txt check
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
        return True


# ----------------------------------------------------------------------
# Extraction
# ----------------------------------------------------------------------
def extract_contacts_from_html(html, base_url):
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ")

    emails = set(EMAIL_RE.findall(text))
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

    return {"emails": emails, "phones": phones, "socials": socials}


def fetch_page(url):
    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT)
        if resp.status_code == 200:
            return resp.text
    except requests.RequestException:
        pass
    return None


def scrape_site_for_contacts(base_url, delay):
    parsed = urlparse(base_url)
    root = f"{parsed.scheme}://{parsed.netloc}"

    all_emails, all_phones, all_socials = set(), set(), set()

    for path in CONTACT_PATHS:
        candidate = urljoin(root + "/", path)
        if not can_fetch(candidate):
            continue
        html = fetch_page(candidate)
        time.sleep(delay)
        if not html:
            continue
        found = extract_contacts_from_html(html, candidate)
        all_emails |= found["emails"]
        all_phones |= found["phones"]
        all_socials |= found["socials"]
        if all_emails:
            break

    return {
        "url": base_url,
        "emails": "; ".join(sorted(all_emails)),
        "phones": "; ".join(sorted(all_phones)),
        "socials": "; ".join(sorted(all_socials)),
    }


# ----------------------------------------------------------------------
# Streamlit UI
# ----------------------------------------------------------------------
st.set_page_config(page_title="Contact Scraper", page_icon="📇", layout="centered")
st.title("📇 Contact Scraper")
st.caption(
    "Searches Google (official Custom Search API) and extracts publicly "
    "listed contact details from the resulting websites."
)

with st.expander("⚠️ Usage & compliance notes", expanded=False):
    st.markdown(
        "- Uses Google's official Search API — no scraping of Google itself.\n"
        "- Checks `robots.txt` before crawling each site and rate-limits requests.\n"
        "- Only extracts information already publicly published on the page.\n"
        "- You're responsible for complying with CAN-SPAM / GDPR / CCPA etc. "
        "when using any contact data collected here, especially for outreach."
    )

# --- Credentials: prefer secrets, fall back to manual input ---
api_key = st.secrets.get("GOOGLE_API_KEY", "")
cx = st.secrets.get("GOOGLE_CX", "")

if not api_key or not cx:
    st.info("No API credentials found in secrets — enter them below (used only for this session).")
    col1, col2 = st.columns(2)
    api_key = col1.text_input("Google API Key", type="password", value=api_key)
    cx = col2.text_input("Search Engine ID (CX)", value=cx)

query = st.text_input("Search query", placeholder="e.g. plumbers in Denver CO")
col1, col2 = st.columns(2)
num_results = col1.slider("Number of results to scan", 5, 50, 10)
delay = col2.slider("Delay between requests (seconds)", 0.5, 3.0, 1.0, step=0.5)

run_button = st.button("Run search", type="primary", disabled=not (query and api_key and cx))

if run_button:
    try:
        with st.spinner("Searching Google..."):
            urls = google_search(query, num_results, api_key, cx)

        if not urls:
            st.warning("No results found.")
        else:
            st.success(f"Found {len(urls)} results. Scraping contact details...")
            progress = st.progress(0)
            status = st.empty()
            rows = []

            for i, url in enumerate(urls, 1):
                status.text(f"[{i}/{len(urls)}] {url}")
                try:
                    row = scrape_site_for_contacts(url, delay)
                except Exception as e:
                    row = {"url": url, "emails": "", "phones": "", "socials": f"ERROR: {e}"}
                rows.append(row)
                progress.progress(i / len(urls))

            status.empty()
            df = pd.DataFrame(rows)
            st.session_state["results_df"] = df

    except requests.HTTPError as e:
        st.error(f"Google API error: {e}. Check your API key/CX and quota.")
    except Exception as e:
        st.error(f"Something went wrong: {e}")

if "results_df" in st.session_state:
    df = st.session_state["results_df"]
    st.subheader("Results")
    st.dataframe(df, use_container_width=True)

    csv_bytes = df.to_csv(index=False).encode("utf-8")
    st.download_button(
        "⬇️ Download CSV",
        data=csv_bytes,
        file_name="contacts.csv",
        mime="text/csv",
    )
