import os
import asyncio
import re
import logging
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Dict, List
import yt_dlp

# Pyrogram imports
from pyrogram import Client, filters, enums
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from scraper import AnimeWorldScraper
from config import Config

# Enable logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# User session storage
user_sessions: Dict[int, Dict] = {}

# --- RENDER HEALTH CHECK SERVER ---
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is alive!")

def run_health_check():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    logger.info(f"Health check server started on port {port}")
    server.serve_forever()
# ----------------------------------

# Initialize Pyrogram Client
app = Client(
    "anime_bot",
    api_id=Config.API_ID,
    api_hash=Config.API_HASH,
    bot_token=Config.BOT_TOKEN,
    in_memory=True, # Prevents SQLite locking errors on Render
    ipv6=False      # Prevents 404 Auth Key errors
)

scraper = AnimeWorldScraper()

@app.on_message(filters.command("start"))
async def start_cmd(client, message):
    welcome_text = """
🚀 *Welcome to Anime World Bot (Pyrogram 2GB Edition)!*

I can help you search and browse anime from watchanimeworld.net

📋 *Commands:*
/anime <query> - Search for anime
/recent - Show recent additions

📱 *Example:*
`/anime Naruto`
    """
    await message.reply_text(welcome_text)

@app.on_message(filters.command("anime"))
async def search_anime(client, message):
    user_id = message.from_user.id
    
    if len(message.command) < 2:
        await message.reply_text("🔍 *Usage:* `/anime <query>`")
        return
    
    query = ' '.join(message.command[1:])
    await client.send_chat_action(chat_id=message.chat.id, action=enums.ChatAction.TYPING)
    
    try:
        results = await scraper.search(query)
        if not results:
            await message.reply_text(f"❌ No results found for '{query}'")
            return
        
        user_sessions[user_id] = {'step': 'search_results', 'results': results, 'query': query}
         
        keyboard = []
        for i, anime in enumerate(results[:10]):  
            keyboard.append([InlineKeyboardButton(f"{i+1}. {anime['title'][:35]}...", callback_data=f"select_anime:{i}")])
        
        await message.reply_text(f"🔍 Found results for '{query}':", reply_markup=InlineKeyboardMarkup(keyboard))
    except Exception as e:
        logger.error(f"Search error: {e}")
        await message.reply_text("❌ Error during search.")

@app.on_message(filters.command("recent"))
async def recent_anime(client, message):
    try:
        recent = await scraper.get_recent(limit=10)
        keyboard = [[InlineKeyboardButton(f"📺 {a['title'][:35]}", callback_data=f"recent_anime:{i}")] for i, a in enumerate(recent)]
        await message.reply_text("🆕 *Recent Additions:*", reply_markup=InlineKeyboardMarkup(keyboard))
    except Exception as e:
        logger.error(f"Recent error: {e}")

@app.on_callback_query()
async def button_callback(client, query):
    user_id = query.from_user.id
    data = query.data
    
    try:
        if data.startswith('select_anime:'): await _handle_anime_selection(client, query, user_id, data)
        elif data.startswith('recent_anime:'): await _handle_recent_selection(client, query, user_id, data)
        elif data.startswith('select_season:'): await _handle_season_selection(client, query, user_id, data)
        elif data.startswith('ep_page:'): await _handle_episode_pagination(client, query, user_id, data)
        elif data.startswith('dl_ep:'): await _handle_download_start(client, query, user_id, data)
        elif data.startswith('res_sel:'): await _handle_res_selection(client, query, user_id, data)
        elif data.startswith('aud_sel:'): await _process_final_download(client, query, user_id, data)
        elif data == 'back_to_search': await query.edit_message_text("🔍 Use /anime to search again.")
    except Exception as e:
        logger.error(f"Callback error: {e}")

async def _handle_anime_selection(client, query, user_id, data):
    index = int(data.split(':')[1])
    selected = user_sessions[user_id]['results'][index]
    info = await scraper.get_anime_info(selected['url'])
    seasons = await scraper.get_seasons(selected['url'])
    user_sessions[user_id].update({'step': 'anime_selected', 'anime': selected, 'seasons': seasons})
    
    keyboard = [[InlineKeyboardButton(f"📋 {s['name']}", callback_data=f"select_season:{i}")] for i, s in enumerate(seasons[:8])]
    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="back_to_search")])
    await query.edit_message_text(f"📺 **{selected['title']}**\n\nSelect Season:", reply_markup=InlineKeyboardMarkup(keyboard))

async def _handle_season_selection(client, query, user_id, data):
    index = int(data.split(':')[1])
    session = user_sessions[user_id]
    selected_season = session['seasons'][index]
    session['selected_season_index'] = index
    
    await query.edit_message_text("⏳ Loading episodes...")
    episodes = await scraper.get_episodes(session['anime']['url'], selected_season['id'])
    session['episodes'] = episodes
    session['episodes_per_page'] = 15
    await _send_episode_page(client, query, session, session['anime'], selected_season, 0)

async def _send_episode_page(client, query, session, anime, selected_season, page):
    episodes = session['episodes']
    start = page * 15
    end = min(start + 15, len(episodes))
    keyboard = [[InlineKeyboardButton(f"📥 {episodes[i]['title'][:35]}", callback_data=f"dl_ep:{i}")] for i in range(start, end)]
    
    nav = []
    if page > 0: nav.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"ep_page:{page-1}"))
    if end < len(episodes): nav.append(InlineKeyboardButton("Next ➡️", callback_data=f"ep_page:{page+1}"))
    if nav: keyboard.append(nav)
    
    await query.edit_message_text(f"📋 **{selected_season['name']}**:", reply_markup=InlineKeyboardMarkup(keyboard))

async def _handle_episode_pagination(client, query, user_id, data):
    page = int(data.split(':')[1])
    session = user_sessions[user_id]
    await _send_episode_page(client, query, session, session['anime'], session['seasons'][session['selected_season_index']], page)

# --- DOWNLOAD FLOW ---

async def _handle_download_start(client, query, user_id, data):
    """Step 1: Extract and show Resolutions"""
    index = int(data.split(':')[1])
    session = user_sessions[user_id]
    selected_ep = session['episodes'][index]
    
    await query.edit_message_text(f"⏳ Extracting player for **{selected_ep['title']}**...")
    
    iframe_link = await scraper.get_episode_video_link(selected_ep['url'])
    raw_url = await scraper.get_raw_video(iframe_link)
    
    if not raw_url:
        await query.edit_message_text("❌ Failed to capture link.")
        return

    session['current_raw_url'] = raw_url
    session['current_ep_title'] = selected_ep['title']

    def get_info():
        ydl_opts = {'http_headers': {'Referer': 'https://play.zephyrflick.top/'}, 'quiet': True, 'nocheckcertificate': True}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            return ydl.extract_info(raw_url, download=False)

    info = await asyncio.to_thread(get_info)
    session['current_formats'] = info['formats']

    heights = sorted(list(set(f.get('height') for f in info['formats'] if f.get('height'))), reverse=True)
    keyboard = [[InlineKeyboardButton(f"📺 {h}p", callback_data=f"res_sel:{index}:{h}")] for h in heights]
    keyboard.append([InlineKeyboardButton("🔙 Cancel", callback_data="ep_page:0")])
    
    await query.edit_message_text("✅ Link Extracted!\nSelect Resolution:", reply_markup=InlineKeyboardMarkup(keyboard))

async def _handle_res_selection(client, query, user_id, data):
    """Step 2: Show Audio Options"""
    parts = data.split(':')
    index, height = parts[1], parts[2]
    session = user_sessions[user_id]
    
    audio_tracks = []
    for f in session['current_formats']:
        if f.get('acodec') != 'none' and f.get('vcodec') == 'none':
            lang = f.get('language') or f.get('format_note') or f.get('format_id')
            if lang not in [x[0] for x in audio_tracks]:
                audio_tracks.append((lang, f['format_id']))

    keyboard = [[InlineKeyboardButton(f"🔊 {l.upper()}", callback_data=f"aud_sel:{index}:{height}:{fid}")] for l, fid in audio_tracks]
    await query.edit_message_text("Select Audio Language:", reply_markup=InlineKeyboardMarkup(keyboard))

async def _process_final_download(client, query, user_id, data):
    """Step 3: Document Upload with Custom Naming and 2GB Bypass"""
    parts = data.split(':')
    index, height, audio_id = parts[1], parts[2], parts[3]
    session = user_sessions[user_id]
    
    raw_title = session.get('current_ep_title', 'Episode 00') 
    anime_base = session.get('anime', {}).get('title', 'Unknown Anime') 
    
    # Extract episode number (e.g., "21")
    num_match = re.search(r'(\d+)', raw_title)
    ep_num = num_match.group(1) if num_match else "00"
    
    # Clean anime name (alphanumeric, hyphens, and underscores only)
    clean_anime = re.sub(r'[^a-zA-Z0-9\-]+', '_', anime_base).strip('_')
    
    # Format: Ep_NUM_CleanName_RES.mp4 -> Ep_21_BanG_Dream-chan_1080p.mp4
    final_filename = f"Ep_{ep_num}_{clean_anime}_{height}p.mp4"

    await query.edit_message_text(f"📥 Downloading: `{final_filename}`...\nWatch Render logs for speed.")

    def dl():
        ydl_opts = {
            'format': f'bestvideo[height<={height}]+{audio_id}/best',
            'outtmpl': final_filename,
            'http_headers': {'Referer': 'https://play.zephyrflick.top/'},
            'nocheckcertificate': True, 
            'quiet': False 
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(session['current_raw_url'], download=True)
            return ydl.prepare_filename(info)

    try:
        filename = await asyncio.to_thread(dl)
        size = os.path.getsize(filename) / (1024*1024)
        
        await query.edit_message_text(f"✅ Downloaded {size:.1f}MB!\n📤 Uploading as document (Bypassing 50MB limit)...")
        
        # Uploads directly as a file document
        await client.send_document(
            chat_id=query.message.chat.id,
            document=filename,
            caption=f"`{final_filename}`",
            force_document=True
        )
        
        await query.edit_message_text(f"✅ Finished: `{final_filename}`")
        
        if os.path.exists(filename): os.remove(filename)
    except Exception as e:
        await query.edit_message_text(f"❌ Error: {str(e)[:50]}")

async def _handle_recent_selection(client, query, user_id, data):
    index = int(data.split(':')[1])
    recent = await scraper.get_recent(limit=20)
    selected = recent[index]
    info = await scraper.get_anime_info(selected['url'])
    seasons = await scraper.get_seasons(selected['url'])
    user_sessions[user_id] = {'anime': selected, 'seasons': seasons}
    keyboard = [[InlineKeyboardButton(f"📋 {s['name']}", callback_data=f"select_season:{i}")] for i, s in enumerate(seasons)]
    await query.edit_message_text(f"📺 **{selected['title']}**", reply_markup=InlineKeyboardMarkup(keyboard))

def main():
    if not hasattr(Config, 'BOT_TOKEN') or not hasattr(Config, 'API_ID') or not hasattr(Config, 'API_HASH'):
        logger.error("❌ Missing BOT_TOKEN, API_ID, or API_HASH in your Config.")
        return
    
    # Start Health Check Server in a background thread for Render
    threading.Thread(target=run_health_check, daemon=True).start()
    
    logger.info("🤖 Starting Pyrogram MTProto Bot on Render...")
    app.run()

if __name__ == '__main__':
    main()
