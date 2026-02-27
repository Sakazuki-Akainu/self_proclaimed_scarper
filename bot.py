import os
import asyncio
import logging
import yt_dlp
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

class AnimeBot:
    def __init__(self):
        self.scraper = AnimeWorldScraper()
    
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Send welcome message"""
        welcome_text = """
🚀 *Welcome to Anime World Bot!*

I can help you search and browse anime from watchanimeworld.net

📋 *Commands:*
/anime <query> - Search for anime
/help - Show this help message
/recent - Show recent additions

📱 *Example:*
`/anime Naruto`
`/anime Dragon Ball Super`
        """
        await update.message.reply_text(welcome_text, parse_mode='Markdown')
    
    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Help command"""
        await self.start(update, context)
    
    async def search_anime(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Search for anime"""
        user_id = update.effective_user.id
        
        if not context.args:
            await update.message.reply_text(
                "🔍 *Usage:* `/anime <search query>`\n"
                "Example: `/anime One Piece`",
                parse_mode='Markdown'
            )
            return
        
        query = ' '.join(context.args)
        await update.message.chat.send_action('typing')
        
        try:
            results = await self.scraper.search(query)
            
            if not results:
                await update.message.reply_text(f"❌ No results found for '{query}'")
                return
            
            # Store results in session
            user_sessions[user_id] = {
                'step': 'search_results',
                'results': results,
                'query': query
            }
             
            # Create inline keyboard
            keyboard = []
            for i, anime in enumerate(results[:10]):  
                btn_text = f"{i+1}. {anime['title'][:35]}..."
                keyboard.append([InlineKeyboardButton(
                    btn_text, 
                    callback_data=f"select_anime:{i}"
                )])
            
            if len(results) > 10:
                keyboard.append([InlineKeyboardButton(
                    "➡️ More results", 
                    callback_data=f"more_results:10"
                )])
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.message.reply_text(
                f"🔍 Found {len(results)} results for '{query}':\n\n"
                "Select an anime to view details:",
                reply_markup=reply_markup
            )
             
        except Exception as e:
            logger.error(f"Search error: {e}")
            await update.message.reply_text("❌ Error occurred while searching. Please try again.")
    
    async def recent_anime(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show recent additions"""
        await update.message.chat.send_action('typing')
        
        try:
            recent = await self.scraper.get_recent(limit=10)
            if not recent:
                await update.message.reply_text("❌ No recent anime found.")
                return
            
            keyboard = []
            for i, anime in enumerate(recent[:8]):
                btn_text = f"📺 {anime['title'][:35]}..."
                keyboard.append([InlineKeyboardButton(
                    btn_text, 
                    callback_data=f"recent_anime:{i}"
                )])
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.message.reply_text(
                "🆕 *Recent Additions:*\n\nSelect an anime:",
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
        except Exception as e:
            logger.error(f"Recent error: {e}")
            await update.message.reply_text("❌ Error fetching recent anime.")
    
    async def button_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle inline button callbacks"""
        query = update.callback_query
        await query.answer()
        
        user_id = update.effective_user.id
        callback_data = query.data
        
        try:
            if callback_data.startswith('more_results:'):
                await self._handle_more_results(query, user_id, callback_data)
                return
            if callback_data.startswith('recent_anime:'):
                await self._handle_recent_selection(query, user_id, callback_data)
                return
            if callback_data.startswith('select_anime:'):
                await self._handle_anime_selection(query, user_id, callback_data)
                return
            if callback_data.startswith('select_season:'):
                await self._handle_season_selection(query, user_id, callback_data)
                return
            if callback_data.startswith('ep_page:'):
                await self._handle_episode_pagination(query, user_id, callback_data)
                return
            if callback_data.startswith('dl_ep:'):
                await self._handle_download(query, user_id, callback_data)
                return
            if callback_data.startswith('dl_vid:'): # NEW HANDLER FOR RESOLUTION
                await self._process_download(query, user_id, callback_data)
                return
            if callback_data == 'back_to_search':
                await self._handle_back_to_search(query, user_id)
                return
            if callback_data == 'new_search':
                await query.edit_message_text("🔍 Use /anime to search again.")
                return
                
        except Exception as e:
            logger.error(f"Callback error: {e}")
            await query.edit_message_text("❌ Error occurred. Please try again.")
    
    async def _handle_more_results(self, query, user_id: int, callback_data: str):
        offset = int(callback_data.split(':')[1])
        session = user_sessions.get(user_id)
        if not session or session['step'] != 'search_results':
            await query.edit_message_text("Session expired. Please search again.")
            return
        
        results = session['results']
        end_idx = min(offset + 10, len(results))
        
        keyboard = []
        for i in range(offset, end_idx):
            anime = results[i]
            btn_text = f"{i+1}. {anime['title'][:35]}..."
            keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"select_anime:{i}")])
        
        if end_idx < len(results):
            keyboard.append([InlineKeyboardButton("➡️ Next 10", callback_data=f"more_results:{end_idx}")])
        
        keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="more_results:0")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            f"🔍 Results {offset+1}-{end_idx} of {len(results)}:\n\nSelect an anime:",
            reply_markup=reply_markup
        )
    
    async def _handle_anime_selection(self, query, user_id: int, callback_data: str):
        index = int(callback_data.split(':')[1])
        session = user_sessions.get(user_id)
        
        if not session or session['step'] != 'search_results':
            await query.edit_message_text("Session expired. Please search again.")
            return
        
        results = session['results']
        if index >= len(results):
            return
        
        selected = results[index]
        await query.edit_message_text(f"⏳ Fetching details for:\n*{selected['title']}*", parse_mode='Markdown')
        
        try:
            info = await self.scraper.get_anime_info(selected['url'])
            seasons = await self.scraper.get_seasons(selected['url'])
            
            user_sessions[user_id] = {
                'step': 'anime_selected',
                'anime': selected,
                'info': info,
                'seasons': seasons
            }
             
            msg = f"📺 *{info['title'] if info else selected['title']}*\n\n"
            if info:
                if info.get('description'):
                    desc = info['description'][:200] + "..." if len(info['description']) > 200 else info['description']
                    msg += f"📝 *Description:* {desc}\n\n"
                if info.get('status'): msg += f"📊 *Status:* {info['status']}\n"
                if info.get('type'): msg += f"🔀 *Type:* {info['type']}\n"
                if info.get('episodes'): msg += f"🎬 *Episodes:* {info['episodes']}\n"
                if info.get('rating'): msg += f"⭐ *Rating:* {info['rating']}/10\n"
            msg += f"\n🔗 [Watch on Website]({selected['url']})"
            
            keyboard = []
            if seasons:
                for i, season in enumerate(seasons[:8]):
                    season_name = season['name'][:30] + "..." if len(season['name']) > 30 else season['name']
                    keyboard.append([InlineKeyboardButton(f"📋 {season_name}", callback_data=f"select_season:{i}")])
            else:
                keyboard.append([InlineKeyboardButton("📺 Get Episodes", callback_data="select_season:0")])
            
            keyboard.append([InlineKeyboardButton("🔙 Back to search", callback_data="back_to_search")])
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(msg, reply_markup=reply_markup, parse_mode='Markdown', disable_web_page_preview=True)
             
        except Exception as e:
            logger.error(f"Anime info error: {e}")
            await query.edit_message_text("❌ Error fetching anime details.")
    
    async def _handle_season_selection(self, query, user_id: int, callback_data: str):
        index = int(callback_data.split(':')[1])
        session = user_sessions.get(user_id)
        if not session or session['step'] != 'anime_selected': return
        
        seasons = session['seasons']
        if index >= len(seasons): return
        
        selected_season = seasons[index]
        anime = session['anime']
        session['selected_season_index'] = index
        
        await query.edit_message_text(f"⏳ Fetching episodes for *{selected_season['name']}*...", parse_mode='Markdown')
         
        try:
            episodes = await self.scraper.get_episodes(anime['url'], selected_season['id'])
            if not episodes:
                await query.edit_message_text(f"😔 No episodes found for {selected_season['name']}.")
                return
            
            session['episodes'] = episodes
            session['current_page'] = 0
            session['episodes_per_page'] = 15
            await self._send_episode_page(query, session, anime, selected_season, 0)
            
        except Exception as e:
            logger.error(f"Episodes error: {e}")
            await query.edit_message_text("❌ Error fetching episodes.")
    
    async def _send_episode_page(self, query, session, anime, selected_season, page):
        episodes = session['episodes']
        episodes_per_page = session['episodes_per_page']
        total_episodes = len(episodes)
        total_pages = (total_episodes + episodes_per_page - 1) // episodes_per_page
        
        start = page * episodes_per_page
        end = min(start + episodes_per_page, total_episodes)
        
        episodes_text = f"📋 *{selected_season['name']}* - Episodes {start+1}-{end} of {total_episodes}:\n\n"
        
        keyboard = []
        for i in range(start, end):
            ep = episodes[i]
            ep_title = ep.get('title', f"Episode {i+1}").strip()[:35]
            keyboard.append([InlineKeyboardButton(f"📥 Download {ep_title}", callback_data=f"dl_ep:{i}")])
        
        page_buttons = []
        if page > 0: page_buttons.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"ep_page:{page-1}"))
        if page < total_pages - 1: page_buttons.append(InlineKeyboardButton("Next ➡️", callback_data=f"ep_page:{page+1}"))
        if page_buttons: keyboard.append(page_buttons)
        
        keyboard.append([InlineKeyboardButton("🔙 Back to seasons", callback_data=f"back_to_search")])
        keyboard.append([InlineKeyboardButton("🔍 New search", callback_data="new_search")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        try:
            if page == 0: await query.edit_message_text(episodes_text, reply_markup=reply_markup, parse_mode='Markdown')
            else: await query.message.reply_text(episodes_text, reply_markup=reply_markup, parse_mode='Markdown')
        except:
            await query.message.reply_text(episodes_text, reply_markup=reply_markup, parse_mode='Markdown')
             
    async def _handle_episode_pagination(self, query, user_id: int, callback_data: str):
        page = int(callback_data.split(':')[1])
        session = user_sessions.get(user_id)
        if not session or 'episodes' not in session: return
        
        anime = session['anime']
        selected_season = session['seasons'][session.get('selected_season_index', 0)]
        try: await query.message.delete()
        except: pass
        await self._send_episode_page(query, session, anime, selected_season, page)

    async def _handle_download(self, query, user_id: int, callback_data: str):
        """Step 1: Extract the link and read the available resolutions"""
        index = int(callback_data.split(':')[1])
        session = user_sessions.get(user_id)
        
        if not session or 'episodes' not in session:
            await query.edit_message_text("Session expired. Please search again.")
            return
            
        episodes = session['episodes']
        selected_ep = episodes[index]
        safe_title = "".join(c for c in selected_ep.get('title', f'Episode_{index+1}') if c.isalnum() or c in " -_").strip()
        ep_url = selected_ep.get('url') or selected_ep.get('link')

        await query.edit_message_text(f"⏳ Extracting player for *{safe_title}*...\n_Bypassing protection..._", parse_mode='Markdown')
        
        try:
            iframe_link = await self.scraper.get_episode_video_link(ep_url)
            if not iframe_link:
                await query.edit_message_text(f"❌ Could not find video player for {safe_title}.")
                return
                
            raw_video_url = await self.scraper.get_raw_video(iframe_link)
            if not raw_video_url:
                await query.edit_message_text("❌ Failed to bypass player protection.")
                return

            # Store the raw m3u8 url in the session so we don't have to extract it again
            session['episodes'][index]['m3u8_url'] = raw_video_url

            await query.edit_message_text(f"✅ **Link Extracted!**\n\n🔍 Reading available qualities...", parse_mode='Markdown')

            # Use yt-dlp to read the menu of available resolutions
            def get_formats():
                ydl_opts = {
                    'http_headers': {'Referer': 'https://play.zephyrflick.top/'},
                    'quiet': True,
                    'no_warnings': True
                }
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    return ydl.extract_info(raw_video_url, download=False)

            info = await asyncio.to_thread(get_formats)
            
            # Find all available heights (resolutions)
            resolutions = set()
            for f in info.get('formats', []):
                h = f.get('height')
                if h and f.get('vcodec') != 'none':  # Only look at video tracks
                    resolutions.add(h)
                    
            sorted_res = sorted(list(resolutions), reverse=True)
            
            # Build the resolution selection keyboard
            keyboard = []
            if sorted_res:
                for res in sorted_res:
                    keyboard.append([InlineKeyboardButton(f"📺 {res}p Quality", callback_data=f"dl_vid:{index}:{res}")])
            else:
                # Fallback if yt-dlp can't read the separate heights
                keyboard.append([InlineKeyboardButton(f"📺 Download Best Available", callback_data=f"dl_vid:{index}:0")])
                
            keyboard.append([InlineKeyboardButton("🔙 Cancel", callback_data=f"ep_page:0")])
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(
                f"🎬 *{safe_title}*\n\nSelect your preferred resolution:",
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
                
        except Exception as e:
            logger.error(f"Extraction error: {e}")
            await query.edit_message_text(f"❌ Error during extraction: {str(e)}")

    async def _process_download(self, query, user_id: int, callback_data: str):
        """Step 2: Download the specific resolution the user chose"""
        parts = callback_data.split(':')
        index = int(parts[1])
        height = int(parts[2])
        
        session = user_sessions.get(user_id)
        if not session or 'episodes' not in session:
            return
            
        selected_ep = session['episodes'][index]
        safe_title = "".join(c for c in selected_ep.get('title', f'Episode_{index+1}') if c.isalnum() or c in " -_").strip()
        raw_video_url = selected_ep.get('m3u8_url')
        
        if not raw_video_url:
            await query.edit_message_text("❌ Lost the raw video link. Please try extracting again.")
            return

        res_text = f"{height}p" if height > 0 else "Best Quality"
        await query.edit_message_text(f"📥 Downloading *{safe_title}* at **{res_text}**...\n_This might take a few minutes..._", parse_mode='Markdown')

        def download_video():
            # Tell yt-dlp to get the specific resolution + best audio
            if height > 0:
                fmt = f'bestvideo[height<={height}]+bestaudio/best[height<={height}]/best'
            else:
                fmt = 'best'
                
            ydl_opts = {
                'format': fmt,
                'outtmpl': f'{safe_title}_{res_text}.%(ext)s',
                'http_headers': {'Referer': 'https://play.zephyrflick.top/'}, 
                'quiet': True,
                'no_warnings': True
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(raw_video_url, download=True)
                return ydl.prepare_filename(info)

        try:
            filename = await asyncio.to_thread(download_video)
            
            file_size_mb = os.path.getsize(filename) / (1024 * 1024)
            if file_size_mb > 50:
                await query.edit_message_text(
                    f"⚠️ **File too large for standard Telegram Bot ({file_size_mb:.1f} MB)!**\n\n"
                    f"Standard bots can only upload 50MB. You need a Local Bot API Server for this.\n\n"
                    f"Here is your direct {res_text} stream link instead:\n`{raw_video_url}`", 
                    parse_mode='Markdown'
                )
                os.remove(filename)
                return

            await query.edit_message_text(f"✅ **Downloaded ({file_size_mb:.1f} MB)!** Uploading to Telegram...", parse_mode='Markdown')

            with open(filename, 'rb') as video_file:
                await query.message.reply_video(
                    video=video_file,
                    caption=f"🎬 *{safe_title}* ({res_text})\nDownloaded via Anime World Bot",
                    parse_mode='Markdown',
                    supports_streaming=True
                )
            
            os.remove(filename)
            await query.edit_message_text(f"✅ **Successfully sent {safe_title}!**", parse_mode='Markdown')
                
        except Exception as e:
            logger.error(f"Download error: {e}")
            await query.edit_message_text(f"❌ Error during download/upload: {str(e)}")
            if 'filename' in locals() and os.path.exists(filename):
                os.remove(filename)

    async def _handle_recent_selection(self, query, user_id: int, callback_data: str):
        index = int(callback_data.split(':')[1])
        session = user_sessions.get(user_id)
        recent = await self.scraper.get_recent(limit=20)
        if index >= len(recent): return
        selected = recent[index]
        await query.edit_message_text(f"⏳ Fetching details for:\n*{selected['title']}*", parse_mode='Markdown')
        try:
            info = await self.scraper.get_anime_info(selected['url'])
            seasons = await self.scraper.get_seasons(selected['url'])
            user_sessions[user_id] = {'step': 'anime_selected', 'anime': selected, 'info': info, 'seasons': seasons}
             
            msg = f"📺 *{info['title'] if info else selected['title']}*\n\n"
            if info:
                if info.get('description'): msg += f"📝 *Description:* {info['description'][:200]}...\n\n"
                if info.get('status'): msg += f"📊 *Status:* {info['status']}\n"
                if info.get('episodes'): msg += f"🎬 *Episodes:* {info['episodes']}\n"
            
            keyboard = []
            if seasons:
                for i, season in enumerate(seasons[:8]):
                    keyboard.append([InlineKeyboardButton(f"📋 {season['name'][:30]}", callback_data=f"select_season:{i}")])
            else:
                keyboard.append([InlineKeyboardButton("📺 Get Episodes", callback_data="select_season:0")])
            
            keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="back_to_search")])
            await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown', disable_web_page_preview=True)
             
        except Exception as e:
            logger.error(f"Recent selection error: {e}")
            await query.edit_message_text("❌ Error fetching anime details.")
    
    async def _handle_back_to_search(self, query, user_id: int):
        await query.edit_message_text("🔍 Use /anime to search again.")
    
    async def back_to_search(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        if query:
            await query.answer()
            await query.edit_message_text("🔍 Use /anime to search again.")

def main():
    if not Config.BOT_TOKEN:
        print("❌ BOT_TOKEN not set in .env file!")
        return
    
    bot = AnimeBot()
    application = Application.builder().token(Config.BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", bot.start))
    application.add_handler(CommandHandler("help", bot.help_command))
    application.add_handler(CommandHandler("anime", bot.search_anime))
    application.add_handler(CommandHandler("recent", bot.recent_anime))
    application.add_handler(CallbackQueryHandler(bot.button_callback))
    
    print("🤖 Anime World Bot is running...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
