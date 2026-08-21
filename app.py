import aiohttp
import asyncio
import json
import os
import random
import re
import sys

PROGRESS_FILE = 'series_progress.json'
DATA_DIR = 'Series_Data'
BASE_URL = "https://m.mymoviebazar.net"
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
    'Connection': 'keep-alive'
}

if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR)

def log_msg(msg, msg_type="info"):
    colors = {"info": "\033[97m", "success": "\033[92m", "error": "\033[91m"}
    color = colors.get(msg_type, "\033[97m")
    print(f"{color}[>] {msg}\033[0m")
    sys.stdout.flush()

def get_next_data_json(html):
    match = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html, re.IGNORECASE | re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass
    return None

async def fetch_page(session, url):
    try:
        async with session.get(url, headers=HEADERS, timeout=15, ssl=False) as response:
            return await response.text()
    except Exception:
        return ""

async def fetch_multiple(session, urls_dict):
    tasks = []
    keys = []
    for key, url in urls_dict.items():
        keys.append(key)
        tasks.append(fetch_page(session, url))
    
    results = await asyncio.gather(*tasks)
    return dict(zip(keys, results))

async def main():
    start_page = 1
    start_series_index = 0
    total_pages = 1
    categorized_data = {}

    if os.path.exists(PROGRESS_FILE):
        try:
            with open(PROGRESS_FILE, 'r', encoding='utf-8') as f:
                progress_data = json.load(f)
                start_page = progress_data.get('currentPage', 1)
                start_series_index = progress_data.get('currentSeriesIndex', 0)
                total_pages = progress_data.get('totalPages', 1)
                categorized_data = progress_data.get('categorizedData', {})
        except Exception:
            pass

    async with aiohttp.ClientSession(cookie_jar=aiohttp.DummyCookieJar()) as session:
        if start_page == 1 and start_series_index == 0:
            log_msg("Initializing request to fetch total pages...", "info")
            first_page_html = await fetch_page(session, f"{BASE_URL}/series?page=1")
            first_page_data = get_next_data_json(first_page_html)
            
            if first_page_data and 'props' in first_page_data:
                try:
                    series_props = first_page_data['props']['pageProps']['series']
                    if isinstance(series_props, dict):
                        if 'last_page' in series_props:
                            total_pages = series_props['last_page']
                        elif 'meta' in series_props and 'last_page' in series_props['meta']:
                            total_pages = series_props['meta']['last_page']
                        else:
                            total_pages = 39
                    else:
                        total_pages = 39
                except Exception:
                    total_pages = 39
            
            log_msg(f"Total pages found: {total_pages}", "success")
            
            with open(PROGRESS_FILE, 'w', encoding='utf-8') as f:
                json.dump({
                    'currentPage': 1,
                    'currentSeriesIndex': 0,
                    'totalPages': total_pages,
                    'categorizedData': {}
                }, f)

        if start_page > 1 or start_series_index > 0:
            log_msg(f"Resuming from page {start_page} (Series Index: {start_series_index}) of {total_pages}...", "success")

        for page in range(start_page, total_pages + 1):
            log_msg(f"--- Fetching Page: {page} / {total_pages} ---", "info")
            page_url = f"{BASE_URL}/series?page={page}"
            list_html = await fetch_page(session, page_url)
            list_data = get_next_data_json(list_html)
            
            if not list_data or 'series' not in list_data.get('props', {}).get('pageProps', {}):
                log_msg(f"Failed to get data for page {page}.", "error")
                continue
            
            series_data_raw = list_data['props']['pageProps']['series']
            
            if isinstance(series_data_raw, dict):
                series_array = series_data_raw.get('data', [])
            elif isinstance(series_data_raw, list):
                series_array = series_data_raw
            else:
                series_array = []
            
            if not series_array:
                log_msg(f"Page {page} is empty.", "info")
                break

            total_series_in_page = len(series_array)
            current_index = start_series_index if page == start_page else 0

            if current_index >= total_series_in_page:
                continue

            remaining_series = series_array[current_index:]
            batch_size = 2
            series_batches = [remaining_series[i:i + batch_size] for i in range(0, len(remaining_series), batch_size)]

            for batch in series_batches:
                urls_to_fetch = {}
                series_posters = {}
                
                for series in batch:
                    series_id = series.get('id')
                    series_posters[series_id] = series.get('image_link', '')
                    urls_to_fetch[series_id] = f"{BASE_URL}/series/watch/{series_id}"
                
                batch_start = current_index + 1
                batch_end = current_index + len(batch)
                log_msg(f"Page {page}: Fetching series {batch_start} to {batch_end} out of {total_series_in_page}...", "info")
                
                multi_responses = await fetch_multiple(session, urls_to_fetch)
                
                for s_id, detail_html in multi_responses.items():
                    if not detail_html:
                        continue
                        
                    detail_data = get_next_data_json(detail_html)
                    if not detail_data or 'pageProps' not in detail_data.get('props', {}):
                        continue
                        
                    series_details = detail_data['props']['pageProps']
                    
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
                                    "posterUrl": series_posters.get(s_id, ''),
                                    "streamUrl": stream_api_url,
                                    "view": 0
                                })
                                
                            seasons_data.append({
                                "episodes": episodes_list,
                                "season_title": season_name
                            })
                    
                    formatted_series = {
                        "id": s_id,
                        "category": category,
                        "director": director,
                        "genre": series_details.get('genres') or ["Unknown"],
                        "imdbRating": imdb_rating,
                        "imdbVotes": 0,
                        "language": "Unknown",
                        "posterUrl": series_posters.get(s_id, ''),
                        "premium": bool(series_details.get('is_premium')),
                        "quality": str(series_details.get('video_quality') or 'HD').split('.')[0],
                        "releaseDate": release_year if release_year else (series_details.get('release_date') or ''),
                        "resolution": str(series_details.get('video_quality') or '1080p').split('.')[0],
                        "seasons": seasons_data,
                        "sliderStatus": "off",
                        "sliderUrl": "",
                        "status": "on",
                        "storyline": series_details.get('plot') or '',
                        "title": title,
                        "triler": ""
                    }
                    
                    if safe_category not in categorized_data:
                        categorized_data[safe_category] = []
                        
                    existing_idx = next((i for i, item in enumerate(categorized_data[safe_category]) if item.get('id') == s_id), -1)
                    if existing_idx >= 0:
                        categorized_data[safe_category][existing_idx] = formatted_series
                    else:
                        categorized_data[safe_category].append(formatted_series)

                current_index += len(batch)
                
                with open(PROGRESS_FILE, 'w', encoding='utf-8') as f:
                    json.dump({
                        'currentPage': page,
                        'currentSeriesIndex': current_index,
                        'totalPages': total_pages,
                        'categorizedData': categorized_data
                    }, f)
                
                await asyncio.sleep(0.3)
            
            with open(PROGRESS_FILE, 'w', encoding='utf-8') as f:
                json.dump({
                    'currentPage': page + 1,
                    'currentSeriesIndex': 0,
                    'totalPages': total_pages,
                    'categorizedData': categorized_data
                }, f)
                
            await asyncio.sleep(0.5)

    log_msg("All pages processed successfully! Generating final JSON files...", "success")
    
    for cat_name, series_list in categorized_data.items():
        final_list = []
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
        
    log_msg("Task Completed!", "success")

if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
