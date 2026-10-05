import json
import re
import os
import sys
import socket
import time
import random
import requests
import urllib3.util.connection as urllib3_cn
from concurrent.futures import ThreadPoolExecutor, as_completed
import subprocess

# CRITICAL FIX: Force IPv4 globally to completely bypass timeout/hanging
urllib3_cn.allowed_gai_family = lambda: socket.AF_INET

BASE_URL = "https://m.mymoviebazar.net"
PROGRESS_FILE = 'series_progress.json'
DATA_DIR = 'Series_Data'
HEADERS_USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36'

if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR)

def log_msg(msg, msg_type="info"):
    colors = {"info": "\033[97m", "success": "\033[92m", "error": "\033[91m", "warning": "\033[93m"}
    color = colors.get(msg_type, "\033[97m")
    print(f"{color}[>] {msg}\033[0m")
    sys.stdout.flush()

def load_progress():
    if os.path.exists(PROGRESS_FILE):
        try:
            with open(PROGRESS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except: pass
    return {'currentPage': 1, 'currentSeriesIndex': 0, 'categorizedData': {}}

def save_progress(current_page, current_index, categorized_data):
    with open(PROGRESS_FILE, 'w', encoding='utf-8') as f:
        json.dump({
            'currentPage': current_page,
            'currentSeriesIndex': current_index,
            'categorizedData': categorized_data
        }, f, indent=4)

def get_headers(referer_url=None, is_json_api=True):
    headers = {
        'Accept-Language': 'en-US,en;q=0.9',
        'Connection': 'keep-alive',
        'User-Agent': HEADERS_USER_AGENT,
        'sec-ch-ua': '"Chromium";v="154", "Google Chrome";v="154", "Not A(Brand";v="99"',
        'sec-ch-ua-mobile': '?0',
        'sec-ch-ua-platform': '"Windows"'
    }
    if referer_url:
        headers['Referer'] = referer_url
    if is_json_api:
        headers['Accept'] = '*/*'
        headers['Sec-Fetch-Dest'] = 'empty'
        headers['Sec-Fetch-Mode'] = 'cors'
        headers['Sec-Fetch-Site'] = 'same-origin'
        headers['x-nextjs-data'] = '1'
    else:
        headers['Accept'] = 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8'
        headers['Sec-Fetch-Dest'] = 'document'
        headers['Sec-Fetch-Mode'] = 'navigate'
        headers['Sec-Fetch-Site'] = 'none'
    return headers

def get_build_id(session):
    headers = get_headers(is_json_api=False)
    try:
        response = session.get(f"{BASE_URL}/series", headers=headers, timeout=30)
        if response.status_code == 200:
            match = re.search(r'"buildId":"([^"]+)"', response.text)
            if match:
                return match.group(1)
    except Exception as e:
        log_msg(f"Failed to dynamically fetch Build ID: {repr(e)}", "warning")
    return "0LbgtP84amlc5Y40vjZrV"

def fetch_json_api(session, build_id, page):
    api_url = f"{BASE_URL}/_next/data/{build_id}/series.json"
    referer_url = f"{BASE_URL}/series"
    if page > 1:
        api_url += f"?page={page}"
        referer_url += f"?page={page}"
        
    headers = get_headers(referer_url=referer_url, is_json_api=True)
    for attempt in range(1, 4):
        try:
            response = session.get(api_url, headers=headers, timeout=30)
            if response.status_code == 200:
                return response.json()
            elif response.status_code in [404, 403]:
                return None
        except Exception as e:
            if attempt == 3:
                log_msg(f"API Fetch failed for page {page}: {repr(e)}", "error")
        time.sleep(1.5)
    return None

def fetch_series_detail_json(session, build_id, series_id):
    api_url = f"{BASE_URL}/_next/data/{build_id}/series/watch/{series_id}.json"
    headers = get_headers(referer_url=f"{BASE_URL}/series", is_json_api=True)
    try:
        response = session.get(api_url, headers=headers, timeout=20)
        if response.status_code == 200:
            return series_id, response.json()
    except Exception:
        pass
    return series_id, None

def fetch_multiple_details(session, build_id, urls_dict):
    results = {}
    keys = list(urls_dict.keys())
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(fetch_series_detail_json, session, build_id, s_id) for s_id in keys]
        for future in as_completed(futures):
            s_id, data = future.result()
            results[s_id] = data
    return results

def process_series_details(series_id, detail_json, poster_url):
    if not detail_json: return None
        
    page_props = detail_json.get('pageProps', {})
    series_details = page_props.get('series', page_props)
    
    if not series_details or not isinstance(series_details, dict):
        return None
    
    director = "N/A"
    if isinstance(series_details.get('directors'), list) and series_details['directors']:
        director = ", ".join(series_details['directors'])
        
    release_date = series_details.get('release_date', '')
    release_year = ""
    year_match = re.search(r'\b(19|20)\d{2}\b', str(release_date))
    if year_match:
        release_year = year_match.group(0)
        
    raw_title = series_details.get('title', 'Unknown Title')
    base_title = re.sub(r' \| SE\d+EP\d+ \| S\d+E\d+', '', raw_title)
    title = base_title.strip()
    if release_year and release_year not in title:
        title = f"{title} ({release_year})"
        
    category = "Unknown"
    if series_details.get('platform'):
        category = series_details['platform']
    elif isinstance(series_details.get('genres'), list) and series_details['genres']:
        category = series_details['genres'][0]
        
    safe_category = "".join(c if c.isalnum() else "_" for c in category).strip("_").capitalize()
    if not safe_category:
        safe_category = "Unknown"

    imdb_raw = series_details.get('imdb_rating')
    try:
        imdb_rating = float(imdb_raw) if imdb_raw is not None else round(random.uniform(5.0, 9.9), 1)
    except (ValueError, TypeError):
        imdb_rating = round(random.uniform(5.0, 9.9), 1)

    seasons_data = []
    if isinstance(series_details.get('map'), list):
        for season_index, episodes_array in enumerate(series_details['map']):
            if not episodes_array:
                continue
            season_name = episodes_array[0]
            season_number = season_index + 1
            
            episodes_list = []
            for ep_index, _ in enumerate(episodes_array):
                ep_number = ep_index + 1
                stream_api_url = f"{BASE_URL}/api/series/watch/{series_id}/{season_number}/{ep_number}"
                
                episodes_list.append({
                    "downStatus": "off",
                    "downUrl": stream_api_url,
                    "duration": "--:--",
                    "episode_title": f"E{ep_number}",
                    "headers": {
                        "Referer": "https://m.mymoviebazar.net/",
                        "Origin": "",
                        "User-Agent": HEADERS_USER_AGENT
                    },
                    "posterUrl": poster_url,
                    "streamUrl": stream_api_url,
                    "view": 0
                })
                
            seasons_data.append({
                "episodes": episodes_list,
                "season_title": season_name
            })

    return {
        "id": series_id,
        "category": safe_category,
        "director": director,
        "genre": series_details.get('genres') or ["Unknown"],
        "imdbRating": imdb_rating,
        "imdbVotes": 0,
        "language": "Unknown",
        "posterUrl": poster_url,
        "premium": bool(series_details.get('is_premium')),
        "quality": str(series_details.get('video_quality') or 'HD').split('.')[0],
        "releaseDate": release_year if release_year else release_date,
        "resolution": str(series_details.get('video_quality') or '1080p').split('.')[0],
        "seasons": seasons_data,
        "sliderStatus": "off",
        "sliderUrl": "",
        "status": "on",
        "storyline": series_details.get('plot') or '',
        "title": title,
        "triler": ""
    }

def push_to_github():
    """Automatically commits and pushes ONLY series JSON data to GitHub."""
    log_msg("\n--- Starting GitHub Auto-Push ---", "info")
    try:
        subprocess.run(["git", "add", "."], check=True, capture_output=True)
        
        # Protect local script and progress file from being uploaded
        subprocess.run(["git", "rm", "--cached", "app.py", "-q"], capture_output=True)
        subprocess.run(["git", "rm", "--cached", PROGRESS_FILE, "-q"], capture_output=True)
        
        commit_process = subprocess.run(
            ["git", "commit", "-m", "Auto-update Series JSON data"],
            capture_output=True, text=True
        )
        
        if "nothing to commit" in commit_process.stdout or "working tree clean" in commit_process.stdout:
            log_msg("No new series found. GitHub is already up to date.", "warning")
            return

        log_msg("Uploading JSON files to GitHub server...", "info")
        subprocess.run(["git", "push"], check=True, capture_output=True)
        log_msg("Successfully pushed! (app.py was excluded safely)", "success")
        
    except subprocess.CalledProcessError as e:
        error_msg = e.stderr.decode('utf-8').strip() if e.stderr else str(e)
        log_msg(f"Failed to push to GitHub. Error: {error_msg}", "error")

def main():
    progress = load_progress()
    page = progress['currentPage']
    start_index = progress['currentSeriesIndex']
    categorized_data = progress['categorizedData']
    
    with requests.Session() as session:
        log_msg("Fetching Next.js Build ID to use direct API...", "info")
        build_id = get_build_id(session)
        log_msg(f"Using Direct JSON API with Build ID: {build_id}", "success")

        if page > 1 or start_index > 0:
            log_msg(f"Resuming from page {page} (Index: {start_index})...", "success")

        while True:
            log_msg(f"\n--- Fetching Page: {page} via Next.js API ---", "info")
            list_data = fetch_json_api(session, build_id, page)
            
            if not list_data:
                log_msg(f"No response from page {page}. Assuming end of pagination.", "warning")
                break 
                
            series_props = list_data.get('pageProps', {}).get('series', {})
            
            if isinstance(series_props, dict):
                series_array = series_props.get('data', series_props)
            elif isinstance(series_props, list):
                series_array = series_props
            else:
                series_array = []
                
            if not series_array:
                log_msg(f"Page {page} API returned empty array. Reached the end.", "success")
                break
                
            total_series_in_page = len(series_array)
            current_index = start_index if page == progress['currentPage'] else 0
            
            if current_index >= total_series_in_page:
                page += 1
                start_index = 0
                continue
                
            batch_size = 5
            remaining_series = series_array[current_index:]
            batches = [remaining_series[i:i + batch_size] for i in range(0, len(remaining_series), batch_size)]
            
            for batch in batches:
                urls_to_fetch = {}
                series_posters = {}
                
                for series in batch:
                    series_id = series.get('id')
                    if not series_id: continue
                    series_posters[series_id] = series.get('image_link', '')
                    urls_to_fetch[series_id] = True 
                    
                batch_start = current_index + 1
                batch_end = current_index + len(batch)
                log_msg(f"Batch fetching JSON for series {batch_start} to {batch_end} (Page {page})...", "info")
                
                multi_responses = fetch_multiple_details(session, build_id, urls_to_fetch)
                
                for s_id, detail_json in multi_responses.items():
                    if not detail_json:
                        continue
                        
                    formatted_series = process_series_details(s_id, detail_json, series_posters.get(s_id, ""))
                    
                    if formatted_series:
                        cat = formatted_series['category']
                        if cat not in categorized_data:
                            categorized_data[cat] = []
                            
                        existing_idx = next((i for i, item in enumerate(categorized_data[cat]) if item.get("id") == s_id), -1)
                        if existing_idx >= 0:
                            categorized_data[cat][existing_idx] = formatted_series
                        else:
                            categorized_data[cat].append(formatted_series)
                            
                current_index += len(batch)
                save_progress(page, current_index, categorized_data)
                time.sleep(0.3)
                
            log_msg(f"Successfully processed page {page}.", "success")
            page += 1
            start_index = 0
            save_progress(page, start_index, categorized_data)
            time.sleep(0.5)
            
        log_msg("Scraping completed! Generating final JSON files...", "success")
        
        for cat_name, series_list in categorized_data.items():
            final_list = []
            # User er ager script er niyam onujayi 'id' remove kora hocche final file e
            for item in series_list:
                item_copy = item.copy()
                item_copy.pop('id', None)
                final_list.append(item_copy)
                
            file_path = os.path.join(DATA_DIR, f"{cat_name}.json")
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(final_list, f, indent=4, ensure_ascii=False)
            log_msg(f"Saved: {file_path} ({len(final_list)} series)", "success")
            
        if os.path.exists(PROGRESS_FILE):
            os.remove(PROGRESS_FILE)

        push_to_github()

if __name__ == "__main__":
    main()