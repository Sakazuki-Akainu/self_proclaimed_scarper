import asyncio
import re
import logging
from typing import List, Dict, Optional
from urllib.parse import quote
import httpx
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright
from config import Config

logger = logging.getLogger(__name__)

class AnimeWorldScraper:
    def __init__(self):
        self.base_url = Config.BASE_URL
        self.headers = Config.HEADERS
        self.delay = Config.REQUEST_DELAY
    
    async def _get(self, url: str) -> Optional[BeautifulSoup]:
        try:
            await asyncio.sleep(self.delay)
            async with httpx.AsyncClient(headers=self.headers, timeout=30.0, follow_redirects=True) as client:
                response = await client.get(url)
                response.raise_for_status()
                return BeautifulSoup(response.text, 'html.parser')
        except Exception as e:
            logger.error(f"Error fetching {url}: {e}")
            return None

    async def get_raw_video(self, iframe_url: str) -> Optional[str]:
        print(f"\n[1/5] 🌐 Starting extraction for: {iframe_url}")
        m3u8_url = None
        seen_requests = 0
        
        async with async_playwright() as p:
            print("[2/5] 🤖 Launching stealth browser...")
            browser = await p.chromium.launch(
                headless=True,
                args=['--disable-blink-features=AutomationControlled', '--disable-web-security', '--no-sandbox']
            )
            
            context = await browser.new_context(
                user_agent=self.headers['User-Agent'],
                extra_http_headers={"Referer": f"{self.base_url}/"},
                viewport={'width': 1280, 'height': 720}
            )
            page = await context.new_page()

            async def handle_request(request):
                nonlocal m3u8_url, seen_requests
                url = request.url
                if (".m3u8" in url or ".mp4" in url):
                    seen_requests += 1
                    if not m3u8_url: 
                        m3u8_url = url
                        print(f"✅ [FOUND] Video Link Sniffed: {url[:70]}...")

            page.on("request", handle_request)

            try:
                print("[3/5] 📄 Loading player page...")
                await page.goto(iframe_url, wait_until="domcontentloaded", timeout=60000)
                print("[4/5] 🖱️ Performing bypass clicks...")
                await page.wait_for_timeout(4000)
                await page.mouse.click(640, 360)
                await page.wait_for_timeout(1000)
                await page.mouse.click(640, 360)
                print("[5/5] ⏳ Waiting for stream to initialize...")
                await page.wait_for_timeout(8000) 
            except Exception as e:
                print(f"⚠️ [ERROR] Browser issue: {str(e)[:50]}")
            
            await browser.close()
            return m3u8_url

    async def search(self, query: str) -> List[Dict]:
        try:
            url = f"{self.base_url}/?s={quote(query)}"
            soup = await self._get(url)
            if not soup: return []
            
            results = []
            for item in soup.select('article.post, div.tt-item, .item, li.item'):
                try:
                    title_elem = item.select_one('h2.entry-title, h3.title, .title, .post-title, h2, h3')
                    link_elem = item.select_one('a[href]')
                    if title_elem and link_elem:
                        link = link_elem['href']
                        if not link.startswith('http'): link = self.base_url + link if link.startswith('/') else f"{self.base_url}/{link}"
                        results.append({'title': title_elem.get_text(strip=True), 'url': link})
                except: continue
            
            seen = set()
            unique_results = []
            for r in results:
                if r['url'] not in seen:
                    seen.add(r['url'])
                    unique_results.append(r)
            return unique_results[:20]
        except Exception as e:
            logger.error(f"Search error: {e}")
            return []

    async def get_seasons(self, url: str) -> List[Dict]:
        """Fixed: Uses the accurate Colab selectors to find all seasons"""
        soup = await self._get(url)
        if not soup: return []
        seasons = []
        for item in soup.select('ul.aa-cnt.sub-menu li a, .choose-season li a'):
            s_id = item.get('data-season')
            p_id = item.get('data-post')
            if s_id: seasons.append({'id': s_id, 'post_id': p_id, 'name': item.get_text(strip=True)})
        if not seasons: seasons.append({'id': '1', 'name': 'Season 1'})
        return seasons

    async def get_episodes(self, anime_url: str, season_id: str = "1") -> List[Dict]:
        """Fixed: Incorporates the AJAX call required to load non-default seasons"""
        try:
            soup = await self._get(anime_url)
            if not soup: return []
            raw_episodes = []
            seen_urls = set()
            
            ep_container = soup.select_one('ul#episode_by_temp, ul.post-lst')
            if ep_container:
                for ep_item in ep_container.select('article.episodes, article.post, li.episode'):
                    try:
                        link_elem = ep_item.select_one('a[href]')
                        if not link_elem: continue
                        episode_url = link_elem['href']
                        if not episode_url.startswith('http'): 
                            episode_url = self.base_url + episode_url if episode_url.startswith('/') else f"{self.base_url}/{episode_url}"
                        
                        clean_url = episode_url.rstrip('/')
                        if clean_url in seen_urls: continue
                        
                        num_elem = ep_item.select_one('.num-epi, .ep-number')
                        num_str = num_elem.get_text(strip=True) if num_elem else ""
                        title_elem = ep_item.select_one('.ep-title, .episode-title, .title, h2.entry-title, h3, h2')
                        title_str = title_elem.get_text(strip=True) if title_elem else f"Episode {num_str}"
                        
                        if not num_str.startswith(f"{season_id}x") and num_str != "": continue
                        
                        seen_urls.add(clean_url)
                        raw_episodes.append({'number': num_str, 'title': title_str, 'url': episode_url})
                    except: continue

            # If no episodes found for the requested season, do the AJAX call
            if not raw_episodes:
                post_id = ""
                season_link = soup.select_one(f'a[data-season="{season_id}"]')
                if season_link: post_id = season_link.get('data-post', '')
                if not post_id:
                    m = re.search(r'data-post="(\d+)"', str(soup))
                    post_id = m.group(1) if m else ""
                
                if post_id:
                    ajax_url = f"{self.base_url}/wp-admin/admin-ajax.php"
                    params = {'action': 'action_select_season', 'season': season_id, 'post': post_id}
                    ajax_headers = self.headers.copy()
                    ajax_headers.update({'X-Requested-With': 'XMLHttpRequest', 'Referer': anime_url})
                    
                    async with httpx.AsyncClient(headers=ajax_headers, timeout=30.0) as client:
                        response = await client.get(ajax_url, params=params, timeout=30.0)
                        if response.status_code == 200:
                            try:
                                js = response.json()
                                ep_soup = BeautifulSoup(js['html'], 'html.parser') if isinstance(js, dict) and 'html' in js else BeautifulSoup(response.text, 'html.parser')
                            except: ep_soup = BeautifulSoup(response.text, 'html.parser')
                            
                            for item in ep_soup.select('li, article.post'):
                                link_tag = item.select_one('a')
                                if not link_tag: continue
                                link = link_tag['href']
                                if not link.startswith('http'): 
                                    link = self.base_url + link if link.startswith('/') else f"{self.base_url}/{link}"
                                
                                clean_link = link.rstrip('/')
                                if clean_link in seen_urls: continue
                                seen_urls.add(clean_link)
                                
                                title_tag = item.select_one('.entry-title, .title, h2, h3')
                                num_tag = item.select_one('.num-epi')
                                num_str = num_tag.get_text(strip=True) if num_tag else ""
                                title_str = title_tag.get_text(strip=True) if title_tag else f"Episode {num_str}"
                                raw_episodes.append({'title': title_str, 'url': link})
            return raw_episodes
        except: return []

    async def get_episode_video_link(self, episode_url: str) -> Optional[str]:
        soup = await self._get(episode_url)
        if not soup: return None
        try:
            iframe = soup.find('iframe')
            if iframe and iframe.get('src'):
                return self.base_url + iframe['src'] if iframe['src'].startswith('/') else iframe['src']
            div_player = soup.select_one('div[data-src*="play"]')
            if div_player: return div_player['data-src']
            return None
        except: return None
