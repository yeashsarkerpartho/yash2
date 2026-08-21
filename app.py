import asyncio
import aiohttp
import json
import re
import os
import sys
from urllib.parse import urljoin

BASE_URL = "https://m.mymoviebazar.net"
SERIES_LIST_URL = f"{BASE_URL}/series"
PROGRESS_FILE = 'series_progress.json'
OUTPUT_DIR = 'Series_Data'

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
    'Referer': 'https://www.google.com/',
    'Connection': 'keep-alive',
}

def log_msg(message, msg_type='info'):
    colors = {
        'info': '\033[94m',
        'success': '\033[92m',
        'error': '\033[91m',
        'warning': '\033[93m',
        'reset': '\033[0m'
    }
    color = colors.get(msg_type, colors['reset'])
    print(f"{color}[>] {message}{colors['reset']}")

def get_next_data_json(html_content):
    pattern = r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>'
    match = re.search(pattern, html_content, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            return None
    return None

def clean_filename(name):
    return re.sub(r'[\\/*?:"<>|]', "", name).strip()

async def fetch_webpage(session, url, retries=3):
    for attempt in range(retries):
        try:
            async with session.get(url, headers=HEADERS, timeout=15) as response:
                if response.status == 200:
                    return await response.text()
                else:
                    log_msg(f"HTTP {response.status} Error for {url}", 'error')
        except Exception as e:
            if attempt == retries - 1:
                log_msg(f"Failed to fetch {url}: {str(e)}", 'error')
            await asyncio.sleep(2)
    return None

async def fetch_multiple_urls(session, urls_dict):
    tasks = {}
    for series_id, url in urls_dict.items():
        tasks[series_id] = asyncio.create_task(fetch_webpage(session, url))
    
    results = {}
    for series_id, task in tasks.items():
        results[series_id] = await task
    return results

async def main():
    log_msg("Initializing Series Scraper...", "info")
    
    if not os.path.exists(OUTPUT_DIR):
        os.makedirs(OUTPUT_DIR)

    start_page = 1
    start_series_index = 0
    total_pages = 1
    categorized_data = {}

    if os.path.exists(PROGRESS_FILE):
        try:
            with open(PROGRESS_FILE, 'r', encoding='utf-8') as f:
                progress = json.load(f)
                start_page = progress.get('currentPage', 1)
                start_series_index = progress.get('currentSeriesIndex', 0)
                total_pages = progress.get('totalPages', 1)
                categorized_data = progress.get('categorizedData', {})
            log_msg(f"Resuming from Page {start_page} (Index: {start_series_index})", "success")
        except Exception:
            log_msg("Progress file corrupted, starting from scratch.", "warning")

    connector = aiohttp.TCPConnector(ssl=False)
    async with aiohttp.ClientSession(connector=connector, cookie_jar=aiohttp.CookieJar()) as session:
        
        if start_page == 1 and start_series_index == 0:
            log_msg("Fetching total pages...", "info")
            first_page_html = await fetch_webpage(session, f"{SERIES_LIST_URL}?page=1")
            if first_page_html:
                first_page_data = get_next_data_json(first_page_html)
                if first_page_data and 'props' in first_page_data:
                    series_props = first_page_data['props']['pageProps']['series']
                    if 'last_page' in series_props:
                        total_pages = series_props['last_page']
                    elif 'meta' in series_props and 'last_page' in series_props['meta']:
                        total_pages = series_props['meta']['last_page']
                    else:
                        total_pages = 39
            log_msg(f"Total pages found: {total_pages}", "success")
            
            with open(PROGRESS_FILE, 'w', encoding='utf-8') as f:
                json.dump({'currentPage': 1, 'currentSeriesIndex': 0, 'totalPages': total_pages, 'categorizedData': {}}, f)

        for page in range(start_page, total_pages + 1):
            log_msg(f"\n--- Fetching Page: {page} / {total_pages} ---", "warning")
            
            page_url = f"{SERIES_LIST_URL}?page={page}"
            list_html = await fetch_webpage(session, page_url)
            
            if not list_html:
                log_msg(f"Failed to load page {page}. Please restart to retry.", "error")
                break
                
            list_data = get_next_data_json(list_html)
            if not list_data or 'series' not in list_data.get('props', {}).get('pageProps', {}):
                log_msg(f"Invalid data on page {page}.", "error")
                continue

            series_data_raw = list_data['props']['pageProps']['series']
            series_array = series_data_raw.get('data', series_data_raw)
            
            if not series_array:
                log_msg(f"Page {page} is empty.", "info")
                break

            total_series_in_page = len(series_array)
            current_index = start_series_index if page == start_page else 0
            
            batch_size = 2
            
            while current_index < total_series_in_page:
                batch = series_array[current_index : current_index + batch_size]
                urls_to_fetch = {}
                series_posters = {}
                
                for series in batch:
                    s_id = str(series['id'])
                    series_posters[s_id] = series.get('image_link', '')
                    urls_to_fetch[s_id] = f"{BASE_URL}/series/watch/{s_id}"
                
                batch_start = current_index + 1
                batch_end = current_index + len(batch)
                log_msg(f"Fetching details for series {batch_start} to {batch_end}...", "info")
                
                multi_responses = await fetch_multiple_urls(session, urls_to_fetch)
                
                for s_id, detail_html in multi_responses.items():
                    if not detail_html: continue
                    
                    detail_data = get_next_data_json(detail_html)
                    if detail_data and 'props' in detail_data:
                        series_details = detail_data['props']['pageProps']
                        
                        director = "N/A"
                        if series_details.get('directors'):
                            director = ", ".join(series_details['directors'])
                            
                        release_year = ""
                        release_date = series_details.get('release_date', '')
                        if release_date:
                            year_match = re.search(r'\b(19|20)\d{2}\b', release_date)
                            if year_match:
                                release_year = year_match.group(0)
                                
                        raw_title = series_details.get('title', 'Unknown Title')
                        base_title = re.sub(r' \| SE\d+EP\d+ \| S\d+E\d+', '', raw_title)
                        title = base_title
                        if release_year and release_year not in base_title:
                            title = f"{base_title.strip()} ({release_year})"
                            
                        category = "Unknown"
                        if series_details.get('platform'):
                            category = series_details['platform']
                        elif series_details.get('genres') and isinstance(series_details['genres'], list):
                            category = series_details['genres'][0]
                            
                        category_key = clean_filename(category)
                        if not category_key:
                            category_key = "Other"

                        seasons_data = []
                        map_data = series_details.get('map', [])
                        if isinstance(map_data, list):
                            for season_index, episodes_array in enumerate(map_data):
                                if not episodes_array: continue
                                
                                season_name = episodes_array[0]
                                season_number = season_index + 1
                                episodes_list = []
                                
                                for ep_index, ep_name in enumerate(episodes_array):
                                    ep_number = ep_index + 1
                                    stream_api_url = f"{BASE_URL}/api/series/watch/{s_id}/{season_number}/{ep_number}"
                                    
                                    episodes_list.append({
                                        "downStatus": "off",
                                        "downUrl": stream_api_url,
                                        "duration": "--:--",
                                        "episode_title": f"E{ep_number}",
                                        "headers": {
                                            "Referer": "https://m.mymoviebazar.net/",
                                            "Origin": "",
                                            "User-Agent": HEADERS['User-Agent']
                                        },
                                        "posterUrl": series_posters.get(s_id, ""),
                                        "streamUrl": stream_api_url,
                                        "view": 0
                                    })
                                    
                                seasons_data.append({
                                    "episodes": episodes_list,
                                    "season_title": season_name
                                })
                                
                        formatted_series = {
                            "category": category,
                            "director": director,
                            "genre": series_details.get('genres', ["Unknown"]),
                            "imdbRating": float(series_details.get('imdb_rating', 0.0)),
                            "imdbVotes": 0,
                            "language": "Unknown",
                            "posterUrl": series_posters.get(s_id, ""),
                            "premium": series_details.get('is_premium', False),
                            "quality": series_details.get('video_quality', 'HD').split('.')[0] if series_details.get('video_quality') else "HD",
                            "releaseDate": release_year or release_date,
                            "resolution": series_details.get('video_quality', '1080p').split('.')[0] if series_details.get('video_quality') else "1080p",
                            "seasons": seasons_data,
                            "sliderStatus": "off",
                            "sliderUrl": "",
                            "status": "on",
                            "storyline": series_details.get('plot', ''),
                            "title": title,
                            "triler": ""
                        }
                        
                        if category_key not in categorized_data:
                            categorized_data[category_key] = []
                            
                        is_duplicate = any(item['title'] == title for item in categorized_data[category_key])
                        if not is_duplicate:
                            categorized_data[category_key].append(formatted_series)

                current_index += len(batch)
                
                with open(PROGRESS_FILE, 'w', encoding='utf-8') as f:
                    json.dump({
                        'currentPage': page,
                        'currentSeriesIndex': current_index,
                        'totalPages': total_pages,
                        'categorizedData': categorized_data
                    }, f)
                    
                await asyncio.sleep(0.3)
                
            log_msg(f"Successfully processed Page {page}.", "success")
            start_series_index = 0
            
            with open(PROGRESS_FILE, 'w', encoding='utf-8') as f:
                json.dump({
                    'currentPage': page + 1,
                    'currentSeriesIndex': 0,
                    'totalPages': total_pages,
                    'categorizedData': categorized_data
                }, f)
                
            await asyncio.sleep(0.5)

    log_msg("\nScraping Completed! Saving category files...", "success")
    
    total_series_saved = 0
    for cat_name, series_list in categorized_data.items():
        file_path = os.path.join(OUTPUT_DIR, f"{cat_name}.json")
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(series_list, f, ensure_ascii=False, indent=4)
        
        log_msg(f"Saved {len(series_list)} series in -> {file_path}", "info")
        total_series_saved += len(series_list)

    log_msg(f"Total {total_series_saved} series successfully sorted into categories!", "success")

    if os.path.exists(PROGRESS_FILE):
        os.remove(PROGRESS_FILE)

if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
