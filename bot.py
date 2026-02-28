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

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

user_sessions: Dict[int, Dict] = {}

# ==========================================
# RENDER HEALTH CHECK SERVER
# ==========================================
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

# ==========================================
# PYROGRAM BOT LOGIC
# ==========================================
app = Client(
    "anime_bot",
    api_id=Config.API_ID,
    api_hash=Config.API_HASH,
    bot_token=Config.BOT_TOKEN,
    in_memory=True, # Critical for Render (prevents SQLite lock)
    ipv6=False      # Critical for Render (prevents 404 Auth errors)
)

scraper = AnimeWorldScraper()

@app.on_message(filters.command("start"))
async def start_cmd(client, message):
    await message.reply("🚀 **Pyrogram 2GB Document Bot Online!**\nUse `/anime <name>` to search.")

@app.on_message(filters.command("anime"))
async def search_anime(client, message):
    user_id = message.from_user.id
    if len(message.command) < 2:
        return await message.reply("❌ Usage: `/anime naruto`")

    query = ' '.join(message.command[1:])
    await client.send_chat_action(chat_id=message.chat.id, action=enums.ChatAction.TYPING)
    
    msg = await message.reply("🔍 Searching...")
    results = await scraper.search(query)
    if not results:
        return await msg.edit("❌ No results found.")

    user_sessions[user_id] = {'results': results}
    keyboard = [[InlineKeyboardButton(f"{i+1}. {a['title'][:35]}", callback_data=f"sel_ani:{i}")] for i, a in enumerate(results[:10])]
    await msg.edit(f"🔍 Search results for '{query}':", reply_markup=InlineKeyboardMarkup(keyboard))

@app.on_callback_query()
async def handle_callback(client, query):
    user_id = query.from_user.id
    data = query.data

    try:
        if data.startswith('sel_ani:'):
            idx = int(data.split(':')[1])
            selected = user_sessions[user_id]['results'][idx]
            seasons = await scraper.get_seasons(selected['url'])
            user_sessions[user_id].update({'anime': selected, 'seasons': seasons})
            keyboard = [[InlineKeyboardButton(f"📋 {s['name']}", callback_data=f"sel_sea:{i}")] for i, s in enumerate(seasons)]
            keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="back")])
            await query.edit_message_text(f"📺 **{selected['title']}**\nSelect Season:", reply_markup=InlineKeyboardMarkup(keyboard))

        elif data.startswith('sel_sea:'):
            idx = int(data.split(':')[1])
            session = user_sessions[user_id]
            selected_season = session['seasons'][idx]
            session['selected_season_index'] = idx
            await query.edit_message_text(f"⏳ Loading episodes for {selected_season['name']}...")
            episodes = await scraper.get_episodes(session['anime']['url'], selected_season['id'])
            session['episodes'] = episodes
            await send_ep_page(query, session, 0)

        elif data.startswith('ep_pg:'):
            page = int(data.split(':')[1])
            session = user_sessions[user_id]
            await send_ep_page(query, session, page)

        elif data.startswith('dl_ep:'):
            idx = int(data.split(':')[1])
            session = user_sessions[user_id]
            ep = session['episodes'][idx]
            await query.edit_message_text(f"⏳ Extracting player for **{ep['title']}**...")

            iframe = await scraper.get_episode_video_link(ep['url'])
            raw_url = await scraper.get_raw_video(iframe)
            if not raw_url: return await query.edit_message_text("❌ Failed to extract link.")

            session.update({'current_raw_url': raw_url, 'current_title': ep['title']})

            def get_info():
                opts = {'http_headers': {'Referer': 'https://play.zephyrflick.top/'}, 'quiet': True, 'nocheckcertificate': True}
                with yt_dlp.YoutubeDL(opts) as ydl: return ydl.extract_info(raw_url, download=False)

            info = await asyncio.to_thread(get_info)
            session['current_formats'] = info['formats']
            heights = sorted(list(set(f.get('height') for f in info['formats'] if f.get('height'))), reverse=True)

            keyboard = [[InlineKeyboardButton(f"📺 {h}p", callback_data=f"res:{idx}:{h}")] for h in heights]
            await query.edit_message_text("✅ Link Extracted! Select Resolution:", reply_markup=InlineKeyboardMarkup(keyboard))

        elif data.startswith('res:'):
            parts = data.split(':')
            idx, height = parts[1], parts[2]
            session = user_sessions[user_id]
            audio = []
            for f in session['current_formats']:
                if f.get('acodec') != 'none' and f.get('vcodec') == 'none':
                    lang = f.get('language') or f.get('format_note') or f.get('format_id')
                    if lang not in [x[0] for x in audio]: audio.append((lang, f['format_id']))

            keyboard = [[InlineKeyboardButton(f"🔊 {l.upper()}", callback_data=f"aud:{idx}:{height}:{fid}")] for l, fid in audio]
            await query.edit_message_text("Select Audio Language:", reply_markup=InlineKeyboardMarkup(keyboard))

        elif data.startswith('aud:'):
            parts = data.split(':')
            idx, height, aud_id = parts[1], parts[2], parts[3]
            session = user_sessions[user_id]
            
            raw_title = session.get('current_title', 'Episode 00') 
            anime_base = session.get('anime', {}).get('title', 'Unknown Anime') 
            
            # Extract number from title
            num_match = re.search(r'(\d+)', raw_title)
            ep_num = num_match.group(1) if num_match else "00"
            
            clean_anime = re.sub(r'[^a-zA-Z0-9\-]+', '_', anime_base).strip('_')
            final_filename = f"Ep_{ep_num}_{clean_anime}_{height}p.mp4"

            await query.edit_message_text(f"📥 Downloading: `{final_filename}`...\nWatch Render logs for speed.")

            def dl():
                opts = {
                    'format': f'bestvideo[height<={height}]+{aud_id}/best',
                    'outtmpl': final_filename,
                    'http_headers': {'Referer': 'https://play.zephyrflick.top/'},
                    'nocheckcertificate': True, 'quiet': False
                }
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(session['current_raw_url'], download=True)
                    return ydl.prepare_filename(info)

            try:
                file = await asyncio.to_thread(dl)
                size = os.path.getsize(file) / (1024*1024)

                await query.edit_message_text(f"✅ Downloaded {size:.1f}MB!\n📤 Uploading Document (2GB limit)...")

                await client.send_document(
                    chat_id=query.message.chat.id,
                    document=file,
                    caption=f"`{final_filename}`",
                    force_document=True
                )

                await query.edit_message_text(f"✅ Upload Complete: `{final_filename}`")
                if os.path.exists(file): os.remove(file)

            except Exception as e:
                await query.edit_message_text(f"❌ Error: {str(e)[:50]}")

        elif data == 'back':
            await query.edit_message_text("🔍 Search again with `/anime <name>`")
            
    except Exception as e:
        logger.error(f"Callback error: {e}")

async def send_ep_page(query, session, page):
    eps = session['episodes']
    start = page * 15
    end = min(start + 15, len(eps))
    keyboard = [[InlineKeyboardButton(f"📥 {eps[i]['title'][:35]}", callback_data=f"dl_ep:{i}")] for i in range(start, end)]

    nav = []
    if page > 0: nav.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"ep_pg:{page-1}"))
    if end < len(eps): nav.append(InlineKeyboardButton("Next ➡️", callback_data=f"ep_pg:{page+1}"))
    if nav: keyboard.append(nav)
    keyboard.append([InlineKeyboardButton("🔙 Back to Seasons", callback_data=f"sel_ani:0")])

    await query.edit_message_text(f"📋 **{session['seasons'][session['selected_season_index']]['name']}**:", reply_markup=InlineKeyboardMarkup(keyboard))

def main():
    if not hasattr(Config, 'BOT_TOKEN') or not hasattr(Config, 'API_ID') or not hasattr(Config, 'API_HASH'):
        logger.error("❌ Missing BOT_TOKEN, API_ID, or API_HASH. Check config.py and Render environment variables.")
        return
    
    # Start Health Check Server in a background thread for Render
    threading.Thread(target=run_health_check, daemon=True).start()
    
    logger.info("🤖 Starting Pyrogram Bot on Render...")
    app.run()

if __name__ == '__main__':
    main()
