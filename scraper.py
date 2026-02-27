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
        """Make GET request with delay and return BeautifulSoup object"""
        try:
            await asyncio.sleep(self.delay)  # Respectful delay
            async with httpx.AsyncClient(headers=self.headers, timeout=30.0, follow_redirects=True) as client:
                response = await client.get(url)
                response.raise_for_status()
                return BeautifulSoup(response.text, 'html.parser')
        except httpx.HTTPError as e:
            logger.error(f"HTTP error for {url}: {e}")
            return None
        except Exception as e:
            logger.error(f"Error fetching {url}: {e}")
            return None

    async def get_raw_video(self, iframe_url: str) -> Optional[str]:
        """Uses Playwright to extract the raw link with detailed step-by-step reporting."""
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
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                extra_http_headers={"Referer": "https://watchanimeworld.net/"},
                viewport={'width': 1280, 'height': 720}
            )
            page = await context.new_page()

            async def handle_request(request):
                nonlocal m3u8_url, seen_requests
                url = request.url
                
                # Check for video files (.m3u8 or .mp4)
                if ".m3u8" in url or ".mp4" in url:
                    seen_requests += 1
                    # FIX: We now ALLOW "master" links because that is exactly what we need!
                    if not m3u8_url: 
                        m3u8_url = url
                        print(f"✅ [FOUND] Video Link Sniffed: {url[:70]}...")

            page.on("request", handle_request)

            try:
                print("[3/5] 📄 Loading player page...")
                # Use domcontentloaded because networkidle never happens on ad-heavy sites
                await page.goto(iframe_url, wait_until="domcontentloaded", timeout=60000)
                
                print("[4/5] 🖱️ Performing bypass clicks...")
                await page.wait_for_timeout(4000)
                # Click center to trigger player
                await page.mouse.click(640, 360)
                await page.wait_for_timeout(1000)
                # Click again to clear ad overlays
                await page.mouse.click(640, 360)
                
                print("[5/5] ⏳ Waiting for stream to initialize...")
                # Give the JavaScript 8 seconds to process the clicks and request the video
                await page.wait_for_timeout(8000) 
                
            except Exception as e:
                print(f"⚠️ [ERROR] Browser encountered an issue: {str(e)[:50]}")
            
            await browser.close()
            
            if m3u8_url:
                print(f"🎉 [SUCCESS] Extraction complete. Link captured.")
            else:
                if seen_requests > 0:
                    print(f"❌ [FAILED] Saw {seen_requests} media requests, but none were accepted by the filter.")
                else:
                    print("❌ [FAILED] Browser loaded but the player never requested a video file.")
                
            return m3u8_url

    async def search(self, query: str) -> List[Dict]:
        """Search for anime"""
        try:
            url = f"{self.base_url}/?s={quote(query)}"
            soup = await self._get(url)
            if not soup: return []
            
            results = []
            selectors = ['article.post', 'div.post', 'div.tt-item', 'li.tt-item', 'div.item', 'li.item']
             
            for selector in selectors:
                for item in soup.select(selector):
                    try:
                        title_elem = item.select_one('h2.entry-title, h3.title, .title, .post-title, h2, h3, h4')
                        if not title_elem: continue
                        
                        title = title_elem.get_text(strip=True)
                        link_elem = title_elem.find('a') or item.select_one('a[href]')
                        if not link_elem: continue
                        
                        link = link_elem['href']
                        if not link.startswith('http'):
                            link = self.base_url + link if link.startswith('/') else f"{self.base_url}/{link}"
                        
                        results.append({'title': title, 'url': link})
                    except Exception: continue
            
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

    async def get_anime_info(self, url: str) -> Optional[Dict]:
        """Get detailed anime information"""
        soup = await self._get(url)
        if not soup: return None
        try:
            title_elem = soup.select_one('h1.entry-title, h1.title, .title, .post-title, h1')
            title = title_elem.get_text(strip=True) if title_elem else ""
            desc_elem = soup.select_one('div.entry-content, .wp-content, .synopsis, .summary')
            description = desc_elem.get_text(strip=True)[:500] if desc_elem else ""
            
            return {'title': title, 'description': description}
        except Exception: return None

    async def get_seasons(self, url: str) -> List[Dict]:
        """Get available seasons"""
        soup = await self._get(url)
        if not soup: return []
        seasons = []
        season_menu = soup.select_one('ul.sub-menu, div.aa-cnt, div.aa-tb')
        if season_menu:
            for item in season_menu.select('li, a'):
                season_id = item.get('data-season') or item.get('data-id')
                if season_id:
                    seasons.append({'id': season_id, 'name': item.get_text(strip=True)})
        
        if not seasons:
            seasons.append({'id': '1', 'name': 'Season 1'})
        return seasons

    async def get_episodes(self, anime_url: str, season_id: str = "1") -> List[Dict]:
        """Get episodes for a specific season"""
        soup = await self._get(anime_url)
        if not soup: return []
        episodes = []
        ep_container = soup.select_one('ul#episode_by_temp, ul.post-lst')
        if ep_container:
            for ep_item in ep_container.select('article.episodes, article.post, li.episode'):
                try:
                    link_elem = ep_item.select_one('a[href]')
                    if not link_elem: continue
                    episode_url = link_elem['href']
                    if not episode_url.startswith('http'):
                        episode_url = self.base_url + episode_url if episode_url.startswith('/') else f"{self.base_url}/{episode_url}"
                    
                    num_elem = ep_item.select_one('.num-epi, .ep-number')
                    episodes.append({
                        'number': num_elem.get_text(strip=True) if num_elem else "",
                        'title': ep_item.select_one('h2, h3, .title').get_text(strip=True),
                        'url': episode_url
                    })
                except Exception: continue
        return episodes

    async def get_episode_video_link(self, episode_url: str) -> Optional[str]:
        """Extract iframe link from episode page"""
        soup = await self._get(episode_url)
        if not soup: return None
        try:
            iframe = soup.find('iframe')
            if iframe and iframe.get('src'):
                iframe_src = iframe['src']
                if not iframe_src.startswith('http'):
                    iframe_src = self.base_url + iframe_src if iframe_src.startswith('/') else f"{self.base_url}/{iframe_src}"
                return iframe_src
            return None
        except Exception: return None
