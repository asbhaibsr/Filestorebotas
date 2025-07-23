import os
import uuid
import datetime
import logging 
import requests 
import json     

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters, ContextTypes,
    ConversationHandler,
    CallbackQueryHandler 
)
from pymongo import MongoClient
from flask import Flask
import threading

# --- लॉगिंग कॉन्फ़िगरेशन ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Configuration ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
PUBLIC_CHANNEL_USERNAME = os.getenv("PUBLIC_CHANNEL_USERNAME") # <-- यह वेरिएबल आपके बॉट का यूजरनेम रखता है
PUBLIC_CHANNEL_ID = int(os.getenv("PUBLIC_CHANNEL_ID")) 

UPDATES_CHANNEL_LINK = "https://t.me/asbhai_bsr" 

# **महत्वपूर्ण:** अपनी Google Apps Script वेब ऐप का URL यहां डालें
# यह वही URL है जो आपको Google Apps Script को डिप्लॉय करने के बाद मिला था (Apps Script का doGet endpoint)
GOOGLE_APPS_SCRIPT_API_URL = os.getenv("GOOGLE_APPS_SCRIPT_API_URL", "https://script.google.com/macros/s/AKfycbwDqKLE1bZjwBcNT8wDA2SlKs821Gq7bhea8JOygiHfyPyGuATAKXWY_LtvOwlFwL9n6w/exec") 
# सुनिश्चित करें कि यह Apps Script का नया डिप्लॉयमेंट URL है!

# MongoDB Configuration
MONGO_URI = os.getenv("MONGO_URI")
client = MongoClient(MONGO_URI)
db = client.file_bot 
files_collection = db.files 
batch_files_in_progress = {} 

# --- Conversation States for Batch Command ---
SENDING_BATCH_FILES = 1

# --- Flask App for Health Check ---
flask_app = Flask(__name__)

@flask_app.route('/health')
def health_check():
    return "Bot is healthy!", 200

def run_flask_app():
    port = int(os.getenv("PORT", 8000))
    flask_app.run(host='0.0.0.0', port=port)
    logger.info(f"Flask health check server running on port {port}")

# --- Bot Handlers ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.info(f"Received /start command from {update.effective_user.id}")
    args = context.args 

    if args:
        param = args[0]
        if param.startswith("download_"):
            # यह तब होता है जब यूजर ब्लॉगर पेज पर सत्यापन के बाद वापस बॉट पर रीडायरेक्ट होता है।
            original_permanent_token = param[len("download_"):]
            logger.info(f"Download deep link received for permanent token after verification: {original_permanent_token}")
            
            file_data = files_collection.find_one({"token": original_permanent_token})

            if file_data:
                telegram_file_id = file_data["telegram_file_id"]
                original_filename = file_data["original_filename"]
                try:
                    if file_data.get("file_type") == "video": 
                        await update.message.reply_video(
                            video=telegram_file_id,
                            caption=f"यहाँ आपकी वीडियो है: {original_filename}",
                            filename=original_filename 
                        )
                        logger.info(f"Video {original_filename} sent to user {update.effective_user.id}")
                    else: # assume it's a document
                        await update.message.reply_document(
                            document=telegram_file_id,
                            caption=f"यहाँ आपकी फ़ाइल है: {original_filename}",
                            filename=original_filename 
                        )
                        logger.info(f"Document {original_filename} sent to user {update.effective_user.id}")
                except Exception as e:
                    logger.error(f"Error sending file {original_filename} to user {update.effective_user.id}: {e}")
                    await update.message.reply_text(f"क्षमा करें, फ़ाइल नहीं भेजी जा सकी। एक त्रुटि हुई: {e}")
                return 
            else:
                logger.warning(f"Invalid permanent token {original_permanent_token} requested by user {update.effective_user.id} after verification.")
                await update.message.reply_text("अमान्य या समाप्त डाउनलोड अनुरोध। कृपया पुनः प्रयास करें या एक नई फ़ाइल अपलोड करें।")
                return 
        else:
            # यह तब होता है जब यूजर स्थायी Telegram डीप लिंक पर क्लिक करता है (पहली बार)
            permanent_token_from_deep_link = param 
            logger.info(f"Initial permanent deep link received: {permanent_token_from_deep_link}")

            # Apps Script के doGet को कॉल करें जो ब्लॉगर पर रीडायरेक्ट करेगा
            apps_script_redirect_url = f"{GOOGLE_APPS_SCRIPT_API_URL}?token={permanent_token_from_deep_link}"
            logger.info(f"Redirecting user to Apps Script for Blogger: {apps_script_redirect_url}")

            keyboard = [[InlineKeyboardButton("जारी रखें", url=apps_script_redirect_url)]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.message.reply_text(
                "आपकी फ़ाइल तैयार है! कृपया सत्यापन के लिए जारी रखें बटन पर क्लिक करें।",
                reply_markup=reply_markup
            )
            return 
    else:
        await send_welcome_message(update, context)

async def send_welcome_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.info("Sending welcome message.")
    keyboard = [
        [InlineKeyboardButton("Updates Channel", url=UPDATES_CHANNEL_LINK)],
        [InlineKeyboardButton("Help", callback_data="help_command")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    if update.message:
        await update.message.reply_text(
            "👋 नमस्ते! मैं आपकी फ़ाइल साझा करने वाला बॉट हूँ। मैं आपकी फ़ाइलों के लिए साझा करने योग्य लिंक बनाने में आपकी मदद कर सकता हूँ।",
            reply_markup=reply_markup
        )
    elif update.callback_query:
        await update.callback_query.message.edit_text(
            "👋 नमस्ते! मैं आपकी फ़ाइल साझा करने वाला बॉट हूँ। मैं आपकी फ़ाइलों के लिए साझा करने योग्य लिंक बनाने में आपकी मदद कर सकता हूँ।",
            reply_markup=reply_markup
        )
    logger.info("Welcome message sent.")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.info("Help command received.")
    help_text = (
        "यहाँ वे कमांड दिए गए हैं जिनका आप उपयोग कर सकते हैं:\n\n"
        "➡️ /start - स्वागत संदेश प्राप्त करें।\n"
        "➡️ /link - एक फ़ाइल के लिए साझा करने योग्य लिंक प्राप्त करें।\n"
        "➡️ /batch - एक साथ कई फ़ाइलों के लिए लिंक जनरेट करें।\n\n"
        "कमांड /link या /batch का उपयोग करने के बाद मुझे कोई भी डॉक्यूमेंट या वीडियो भेजें।"
    )
    
    if update.callback_query:
        await update.callback_query.answer() 
        chat_id = update.callback_query.message.chat_id
        message_id = update.callback_query.message.message_id
        
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=help_text,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("पीछे", callback_data="back_to_welcome")]])
        )
        logger.info("Help message sent via callback edit.")
    else: 
        await update.message.reply_text(help_text)
        logger.info("Help message sent via direct command.")

async def back_to_welcome(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.info("Back to welcome button pressed.")
    await update.callback_query.answer() 
    await send_welcome_message(update, context) 

async def link_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.info(f"/link command received from {update.effective_user.id}")
    user_id = update.effective_user.id
    if user_id in batch_files_in_progress:
        del batch_files_in_progress[user_id]
        logger.info(f"Cleared pending batch for user {user_id} when /link was used.")
    context.user_data['current_mode'] = 'single_file'
    await update.message.reply_text("कृपया मुझे वह फ़ाइल (डॉक्यूमेंट या वीडियो) भेजें जिसकी आप लिंक जनरेट करना चाहते हैं।")

async def batch_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.info(f"/batch command received from {update.effective_user.id}")
    user_id = update.effective_user.id
    
    if user_id in batch_files_in_progress:
        logger.info(f"Existing batch for user {user_id} found, resetting.")
        batch_files_in_progress[user_id] = []
    else:
        batch_files_in_progress[user_id] = [] 

    context.user_data['current_mode'] = 'batch_file'

    keyboard = [[InlineKeyboardButton("लिंक जनरेट करें", callback_data="generate_batch_links")]]
    keyboard.append([InlineKeyboardButton("रद्द करें", callback_data="cancel_batch_generation")]) 
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "ठीक है, मुझे एक-एक करके फ़ाइलें (डॉक्यूमेंट या वीडियो) भेजें। "
        "प्रत्येक फ़ाइल भेजने के बाद मैं आपको सूचित करूँगा।\n\n"
        "जब आप सभी फ़ाइलें भेज दें, तो 'लिंक जनरेट करें' बटन पर क्लिक करें। यदि आप रद्द करना चाहते हैं तो 'रद्द करें' दबाएं।",
        reply_markup=reply_markup
    )
    return SENDING_BATCH_FILES 

async def handle_batch_file_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    logger.info(f"Batch file received from {user_id}")
    if user_id not in batch_files_in_progress:
        logger.warning(f"File received for batch from {user_id} but no batch started. Falling back to single file.")
        return await handle_file(update, context) 

    file = None
    file_type = ""
    if update.message.document:
        file = update.message.document
        file_type = "document"
    elif update.message.video:
        file = update.message.video
        file_type = "video"
    else:
        logger.info(f"Unsupported file type received from {user_id} during batch.")
        await update.message.reply_text("कृपया एक डॉक्यूमेंट या एक वीडियो भेजें। अन्य फ़ाइल प्रकार बैच के लिए समर्थित नहीं हैं।")
        return 

    original_filename = file.file_name if file.file_name else f"unnamed_{file_type}"
    user_chat_id = update.message.chat_id

    try:
        sent_message = await context.bot.forward_message(
            chat_id=PUBLIC_CHANNEL_ID,
            from_chat_id=user_chat_id,
            message_id=update.message.message_id
        )
        permanent_telegram_file_id = None
        if sent_message.document:
            permanent_telegram_file_id = sent_message.document.file_id
        elif sent_message.video:
            permanent_telegram_file_id = sent_message.video.file_id
        
        if not permanent_telegram_file_id:
            logger.error(f"Failed to get file ID from forwarded message for file {original_filename}")
            await update.message.reply_text("फ़ॉरवर्डेड मैसेज से फ़ाइल ID प्राप्त करने में विफल।")
            return 

    except Exception as e:
        logger.error(f"Error forwarding file {original_filename} to storage channel: {e}")
        await update.message.reply_text(f"स्टोरेज चैनल पर फ़ाइल फ़ॉरवर्ड करने में त्रुटि: {e}")
        return 

    # स्थायी टोकन जनरेट करें और MongoDB में सहेजें
    permanent_token = str(uuid.uuid4())

    file_info = {
        "token": permanent_token, # अब यह स्थायी टोकन है
        "telegram_file_id": permanent_telegram_file_id,
        "original_filename": original_filename,
        "user_chat_id": user_chat_id,
        "upload_time": datetime.datetime.now(), # यह केवल रिकॉर्ड के लिए है, समाप्ति के लिए नहीं
        "file_type": file_type
    }
    files_collection.insert_one(file_info)
    logger.info(f"File {original_filename} (permanent token: {permanent_token}) saved to MongoDB.")

    batch_files_in_progress[user_id].append(permanent_token) # बैच के लिए स्थायी टोकन स्टोर करें

    keyboard = [[InlineKeyboardButton("लिंक जनरेट करें", callback_data="generate_batch_links")]]
    keyboard.append([InlineKeyboardButton("रद्द करें", callback_data="cancel_batch_generation")]) 
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "फ़ाइल प्राप्त हुई! अधिक फ़ाइलें भेजें या समाप्त करने के लिए 'लिंक जनरेट करें' पर क्लिक करें।",
        reply_markup=reply_markup
    )
    return SENDING_BATCH_FILES 

def escape_markdown_v2(text: str) -> str:
    escape_chars = r'_*[]()~`>#+-=|{}.!' 
    return ''.join(['\\' + char if char in escape_chars else char for char in text])

async def generate_batch_links(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    logger.info(f"Generate batch links button pressed by {update.effective_user.id}")
    await update.callback_query.answer("लिंक जनरेट कर रहा हूँ...") 
    user_id = update.effective_user.id
    
    if user_id not in batch_files_in_progress or not batch_files_in_progress[user_id]:
        await update.callback_query.message.reply_text("कोई फ़ाइलें नहीं मिलीं जिनके लिए लिंक जनरेट की जा सकें। कृपया पहले फ़ाइलें भेजें।")
        logger.warning(f"Generate batch links pressed but no files in progress for user {user_id}")
        return ConversationHandler.END 
    
    links_text = "यहाँ आपकी डाउनलोड लिंक्स हैं:\n\n"
    
    for permanent_token in batch_files_in_progress[user_id]: # स्थायी टोकन पर लूप करें
        file_data = files_collection.find_one({"token": permanent_token})
        if not file_data:
            logger.warning(f"File data not found for permanent token {permanent_token} during batch link generation.")
            continue 

        original_filename = file_data["original_filename"]

        # स्थायी Telegram डीप लिंक बनाएं
        # TELEGRAM_BOT_USERNAME के बजाय PUBLIC_CHANNEL_USERNAME का उपयोग करें
        permanent_telegram_deep_link = f"https://t.me/{PUBLIC_CHANNEL_USERNAME}?start={permanent_token}"
        
        display_text = escape_markdown_v2(original_filename) 
        links_text += f"👉 [{display_text}](<{permanent_telegram_deep_link}>)\n"

    try:
        await update.callback_query.message.reply_text(
            links_text, 
            parse_mode='MarkdownV2', 
            disable_web_page_preview=True
        )
        logger.info(f"Batch links sent to user {user_id}")
    except telegram.error.BadRequest as e:
        logger.error(f"Error sending MarkdownV2 batch links to user {user_id}: {e}")
        fallback_links_text = "लिंक जनरेट करने में समस्या हुई। यहाँ रॉ लिंक्स हैं (कृपया मैन्युअल रूप से कॉपी करें):\n\n"
        for permanent_token in batch_files_in_progress[user_id]:
            file_data = files_collection.find_one({"token": permanent_token})
            if file_data:
                # TELEGRAM_BOT_USERNAME के बजाय PUBLIC_CHANNEL_USERNAME का उपयोग करें
                fallback_links_text += f"👉 {file_data['original_filename']}: https://t.me/{PUBLIC_CHANNEL_USERNAME}?start={permanent_token}\n"
        await update.callback_query.message.reply_text(fallback_links_text)
    
    del batch_files_in_progress[user_id]
    context.user_data.pop('current_mode', None)
    return ConversationHandler.END

async def cancel_batch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    logger.info(f"Batch cancelled by {update.effective_user.id}")
    user_id = update.effective_user.id
    if user_id in batch_files_in_progress:
        del batch_files_in_progress[user_id]
        logger.info(f"Cleared batch in progress for user {user_id}.")
    context.user_data.pop('current_mode', None)
    
    if update.callback_query:
        await update.callback_query.answer("बैच जनरेशन रद्द कर दिया गया।")
        await update.callback_query.message.reply_text(
            "बैच फ़ाइल जनरेशन रद्द कर दिया गया।"
        )
    else: 
        await update.message.reply_text(
            "बैच फ़ाइल जनरेशन रद्द कर दिया गया।"
        )
    
    return ConversationHandler.END

async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if context.user_data.get('current_mode') == 'batch_file':
        logger.info(f"File received in batch mode from {update.effective_user.id}. Passing to batch handler.")
        return await handle_batch_file_received(update, context)

    logger.info(f"Single file received from {update.effective_user.id}")
    file = None
    file_type = ""
    if update.message.document:
        file = update.message.document
        file_type = "document"
    elif update.message.video:
        file = update.message.video
        file_type = "video"
    else:
        logger.info(f"Unsupported file type received from {update.effective_user.id} in single mode.")
        await update.message.reply_text("कृपया एक डॉक्यूमेंट या एक वीडियो भेजें।")
        return

    original_filename = file.file_name if file.file_name else f"unnamed_{file_type}"
    user_chat_id = update.message.chat_id

    try:
        sent_message = await context.bot.forward_message(
            chat_id=PUBLIC_CHANNEL_ID,
            from_chat_id=user_chat_id,
            message_id=update.message.message_id
        )
        permanent_telegram_file_id = None
        if sent_message.document:
            permanent_telegram_file_id = sent_message.document.file_id
        elif sent_message.video:
            permanent_telegram_file_id = sent_message.video.file_id
        
        if not permanent_telegram_file_id:
            logger.error(f"Failed to get file ID from forwarded message for single file {original_filename}")
            await update.message.reply_text("फ़ॉरवर्डेड मैसेज से फ़ाइल ID प्राप्त करने में विफल।")
            return

    except Exception as e:
        logger.error(f"Error forwarding single file {original_filename} to storage channel: {e}")
        await update.message.reply_text(f"स्टोरेज चैनल पर फ़ाइल फ़ॉरवर्ड करने में त्रुटि: {e}")
        return

    # स्थायी टोकन जनरेट करें और MongoDB में सहेजें
    permanent_token = str(uuid.uuid4())

    file_info = {
        "token": permanent_token, # अब यह स्थायी टोकन है
        "telegram_file_id": permanent_telegram_file_id,
        "original_filename": original_filename,
        "user_chat_id": user_chat_id,
        "upload_time": datetime.datetime.now(), # यह केवल रिकॉर्ड के लिए है, समाप्ति के लिए नहीं
        "file_type": file_type
    }
    files_collection.insert_one(file_info)
    logger.info(f"Single file {original_filename} (permanent token: {permanent_token}) saved to MongoDB.")

    # अब सीधे स्थायी Telegram डीप लिंक बनाएं
    # TELEGRAM_BOT_USERNAME के बजाय PUBLIC_CHANNEL_USERNAME का उपयोग करें
    permanent_telegram_deep_link = f"https://t.me/{PUBLIC_CHANNEL_USERNAME}?start={permanent_token}"
    logger.info(f"Generated permanent Telegram deep link: {permanent_telegram_deep_link}")
    
    keyboard = [
        [InlineKeyboardButton("फ़ाइल डाउनलोड करें", url=permanent_telegram_deep_link)],
        [InlineKeyboardButton("फ़ाइल कैसे डाउनलोड करें", url="https://google.com")] 
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "आपकी फ़ाइल सहेजी गई है! आगे बढ़ने और एक छोटा सा कार्य पूरा करने के लिए 'फ़ाइल डाउनलोड करें' पर क्लिक करें:",
        reply_markup=reply_markup
    )
    context.user_data.pop('current_mode', None) 


def main() -> None:
    required_env_vars = ["TELEGRAM_BOT_TOKEN", "MONGO_URI", "PUBLIC_CHANNEL_USERNAME", "PUBLIC_CHANNEL_ID", "GOOGLE_APPS_SCRIPT_API_URL"]
    for var in required_env_vars:
        if not os.getenv(var):
            logger.error(f"त्रुटि: आवश्यक पर्यावरण चर '{var}' गायब है। कृपया इसे सेट करें।")
            exit(1) 

    threading.Thread(target=run_flask_app).start()

    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(help_command, pattern="^help_command$"))
    application.add_handler(CallbackQueryHandler(back_to_welcome, pattern="^back_to_welcome$"))
    application.add_handler(CommandHandler("link", link_command))

    batch_conv_handler = ConversationHandler(
        entry_points=[CommandHandler("batch", batch_start)], 
        states={
            SENDING_BATCH_FILES: [
                MessageHandler(filters.ATTACHMENT, handle_batch_file_received), 
                CallbackQueryHandler(generate_batch_links, pattern="^generate_batch_links$"), 
                CallbackQueryHandler(cancel_batch, pattern="^cancel_batch_generation$"), 
                CommandHandler("cancel", cancel_batch) 
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel_batch), CallbackQueryHandler(cancel_batch, pattern="^cancel_batch_generation$")], 
    )
    application.add_handler(batch_conv_handler)

    application.add_handler(MessageHandler(filters.ATTACHMENT, handle_file))

    logger.info("बॉट चल रहा है...")
    application.run_polling() 

if __name__ == "__main__":
    main()
