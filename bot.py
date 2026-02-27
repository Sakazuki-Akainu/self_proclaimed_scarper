import os
import asyncio
import logging
import yt_dlp
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Dict, List
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters
)
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

class AnimeBot:
    def __init__(self):
        self.scraper = AnimeWorldScraper()
    
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        welcome_text = """
🚀 *Welcome to Anime World Bot!*

I can help you search and browse anime from watchanimeworld.net

📋 *Commands:*
/anime <query> - Search for anime
/recent - Show recent additions

📱 *Example:*
`/anime Naruto`
        """
        await update.message.reply_text(welcome_text, parse_mode='Markdown')
    
    async def search_anime(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if not context.args:
            await update.message.reply_text("🔍 *Usage:* `/anime <query>`", parse_mode='Markdown')
            return
        
        query = ' '.join(context.args)
        await update.message.chat.send_action('typing')
        
        try:
            results = await self.scraper.search(query)
            if not results:
                await update.message.reply_text(f"❌ No results found for '{query}'")
                return
            
            user_sessions[user_id] = {'step': 'search_results', 'results': results, 'query': query}
             
            keyboard = []
            for i, anime in enumerate(results[:10]):  
                keyboard.append([InlineKeyboardButton(f"{i+1}. {anime['title'][:35]}...", callback_data=f"select_anime:{i}")])
            
            await update.message.reply_text(f"🔍 Found results for '{query}':", reply_markup=InlineKeyboardMarkup(keyboard))
        except Exception as e:
            logger.error(f"Search error: {e}")
            await update.message.reply_text("❌ Error during search.")
    
    async def recent_anime(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            recent = await self.scraper.get_recent(limit=10)
            keyboard = [[InlineKeyboardButton(f"📺 {a['title'][:35]}", callback_data=f"recent_anime:{i}")] for i, a in enumerate(recent)]
            await update.message.reply_text("🆕 *Recent Additions:*", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
        except Exception as e:
            logger.error(f"Recent error: {e}")

    async def button_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        user_id = update.effective_user.id
        data = query.data
        
        try:
            if data.startswith('select_anime:'): await self._handle_anime_selection(query, user_id, data)
            elif data.startswith('recent_anime:'): await self._handle_recent_selection(query, user_id, data)
            elif data.startswith('select_season:'): await self._handle_season_selection(query, user_id, data)
            elif data.startswith('ep_page:'): await self._handle_episode_pagination(query, user_id, data)
            elif data.startswith('dl_ep:'): await self._handle_download_start(query, user_id, data)
            elif data.startswith('res_sel:'): await self._handle_res_selection(query, user_id, data)
            elif data.startswith('aud_sel:'): await self._process_final_download(query, user_id, data)
            elif data == 'back_to_search': await query.edit_message_text("🔍 Use /anime to search again.")
        except Exception as e:
            logger.error(f"Callback error: {e}")

    async def _handle_anime_selection(self, query, user_id, data):
        index = int(data.split(':')[1])
        selected = user_sessions[user_id]['results'][index]
        info = await self.scraper.get_anime_info(selected['url'])
        seasons = await self.scraper.get_seasons(selected['url'])
        user_sessions[user_id].update({'step': 'anime_selected', 'anime': selected, 'seasons': seasons})
        
        keyboard = [[InlineKeyboardButton(f"📋 {s['name']}", callback_data=f"select_season:{i}")] for i, s in enumerate(seasons[:8])]
        keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="back_to_search")])
        await query.edit_message_text(f"📺 *{selected['title']}*\n\nSelect Season:", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

    async def _handle_season_selection(self, query, user_id, data):
        index = int(data.split(':')[1])
        session = user_sessions[user_id]
        selected_season = session['seasons'][index]
        session['selected_season_index'] = index
        episodes = await self.scraper.get_episodes(session['anime']['url'], selected_season['id'])
        session['episodes'] = episodes
        session['episodes_per_page'] = 15
        await self._send_episode_page(query, session, session['anime'], selected_season, 0)

    async def _send_episode_page(self, query, session, anime, selected_season, page):
        episodes = session['episodes']
        start = page * 15
        end = min(start + 15, len(episodes))
        keyboard = [[InlineKeyboardButton(f"📥 {episodes[i]['title'][:35]}", callback_data=f"dl_ep:{i}")] for i in range(start, end)]
        
        nav = []
        if page > 0: nav.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"ep_page:{page-1}"))
        if end < len(episodes): nav.append(InlineKeyboardButton("Next ➡️", callback_data=f"ep_page:{page+1}"))
        if nav: keyboard.append(nav)
        
        await query.edit_message_text(f"📋 *{selected_season['name']}*:", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

    async def _handle_episode_pagination(self, query, user_id, data):
        page = int(data.split(':')[1])
        session = user_sessions[user_id]
        await self._send_episode_page(query, session, session['anime'], session['seasons'][session['selected_season_index']], page)

    # --- DOWNLOAD FLOW ---

    async def _handle_download_start(self, query, user_id, data):
        """Step 1: Extract and show Resolutions"""
        index = int(data.split(':')[1])
        session = user_sessions[user_id]
        selected_ep = session['episodes'][index]
        
        await query.edit_message_text(f"⏳ Extracting player for *{selected_ep['title']}*...", parse_mode='Markdown')
        
        iframe_link = await self.scraper.get_episode_video_link(selected_ep['url'])
        raw_url = await self.scraper.get_raw_video(iframe_link)
        
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

    async def _handle_res_selection(self, query, user_id, data):
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

    async def _process_final_download(self, query, user_id, data):
        """Step 3: Download and Upload"""
        parts = data.split(':')
        index, height, audio_id = parts[1], parts[2], parts[3]
        session = user_sessions[user_id]
        
        safe_title = "".join(c for c in session['current_ep_title'] if c.isalnum() or c in " ").strip().replace(" ", "_")
        await query.edit_message_text(f"📥 Downloading *{safe_title}* ({height}p)...", parse_mode='Markdown')

        def dl():
            ydl_opts = {
                'format': f'bestvideo[height<={height}]+{audio_id}/best',
                'outtmpl': f'{safe_title}.%(ext)s',
                'http_headers': {'Referer': 'https://play.zephyrflick.top/'},
                'nocheckcertificate': True, 'quiet': True
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(session['current_raw_url'], download=True)
                return ydl.prepare_filename(info)

        try:
            filename = await asyncio.to_thread(dl)
            size = os.path.getsize(filename) / (1024*1024)
            
            if size > 50:
                await query.edit_message_text(f"⚠️ File too large ({size:.1f}MB).\nLink: `{session['current_raw_url']}`", parse_mode="Markdown")
            else:
                await query.edit_message_text("✅ Uploading to Telegram...")
                with open(filename, 'rb') as v:
                    await query.message.reply_video(video=v, caption=f"🎬 {session['current_ep_title']}", supports_streaming=True)
                await query.edit_message_text("✅ Done!")
            
            if os.path.exists(filename): os.remove(filename)
        except Exception as e:
            await query.edit_message_text(f"❌ Error: {str(e)[:50]}")

    async def _handle_recent_selection(self, query, user_id, data):
        index = int(data.split(':')[1])
        recent = await self.scraper.get_recent(limit=20)
        selected = recent[index]
        info = await self.scraper.get_anime_info(selected['url'])
        seasons = await self.scraper.get_seasons(selected['url'])
        user_sessions[user_id] = {'anime': selected, 'seasons': seasons}
        keyboard = [[InlineKeyboardButton(f"📋 {s['name']}", callback_data=f"select_season:{i}")] for i, s in enumerate(seasons)]
        await query.edit_message_text(f"📺 *{selected['title']}*", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

    async def _handle_back_to_search(self, query, user_id):
        await query.edit_message_text("🔍 Use /anime to search again.")

def main():
    if not Config.BOT_TOKEN: return
    
    # Start Health Check Server in a background thread
    threading.Thread(target=run_health_check, daemon=True).start()
    
    bot = AnimeBot()
    application = Application.builder().token(Config.BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", bot.start))
    application.add_handler(CommandHandler("anime", bot.search_anime))
    application.add_handler(CommandHandler("recent", bot.recent_anime))
    application.add_handler(CallbackQueryHandler(bot.button_callback))
    
    logger.info("🤖 Bot is running...")
    application.run_polling()

if __name__ == '__main__':
    main()
