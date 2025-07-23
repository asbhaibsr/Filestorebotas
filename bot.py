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
PUBLIC_CHANNEL_USERNAME = os.getenv("PUBLIC_CHANNEL_USERNAME") # <-- यह वेरिएबल आपके बॉट का यूजरनेम रखता है (जैसे 'istreamfilestorebot')
PUBLIC_CHANNEL_ID = int(os.getenv("PUBLIC_CHANNEL_ID")) # <-- यह वह चैनल ID है जहाँ फ़ाइलें फ़ॉरवर्ड की जाएंगी (जैसे -1001234567890)

UPDATES_CHANNEL_LINK = "https://t.me/asbhai_bsr" 

# **महत्वपूर्ण:** अपनी Google Apps Script वेब ऐप का URL यहां डालें
# यह वही URL है जो आपको Google Apps Script को डिप्लॉय करने के बाद मिला था (Apps Script का doGet endpoint)
GOOGLE_APPS_SCRIPT_API_URL = os.getenv("GOOGLE_APPS_SCRIPT_API_URL", "https://script.google.com/macros/s/AKfycbwDqKLE1bZjwBcNT8wDA2SlKs821Gq7bhea8JOygiHfyPyGuATAKXWY_LtvOwlFwL9n6w/exec") 
# सुनिश्चित करें कि यह Apps Script का नया डिप्लॉयमेंट URL है!

# MongoDB Configuration
MONGO_URI = os.getenv("MONGO_URI")
client = MongoClient(MONGO_URI)
db = client.file_bot 
files_collection = db.files # फ़ाइल मेटाडेटा के लिए
batches_collection = db.batches # बैच जानकारी के लिए
users_collection = db.users # यूजर जानकारी के लिए (stats और broadcast के लिए)

batch_files_in_progress = {} 

# Admin User ID for broadcast and dellink commands (replace with your Telegram User ID)
ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID", "YOUR_ADMIN_TELEGRAM_ID_HERE")) # <-- इसे अपनी Telegram यूजर ID से बदलें

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

# --- Helper function to update user info ---
async def update_user_info(user_id: int, username: str, first_name: str):
    users_collection.update_one(
        {"_id": user_id},
        {"$set": {"username": username, "first_name": first_name}, "$inc": {"interactions": 1}},
        upsert=True
    )

# --- Bot Handlers ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    await update_user_info(user.id, user.username, user.first_name)
    logger.info(f"Received /start command from {user.id}")
    args = context.args 

    if args:
        param = args[0]
        if param.startswith("download_batch_"):
            # यह तब होता है जब यूजर ब्लॉगर पेज पर बैच सत्यापन के बाद वापस बॉट पर रीडायरेक्ट होता है।
            batch_id = param[len("download_batch_"):]
            logger.info(f"Batch download deep link received after verification: {batch_id}")
            
            batch_data = batches_collection.find_one({"_id": batch_id})

            if batch_data and batch_data.get("permanent_tokens"):
                permanent_tokens = batch_data["permanent_tokens"]
                await update.message.reply_text("आपकी बैच फ़ाइलें भेजी जा रही हैं...")
                for token in permanent_tokens:
                    file_data = files_collection.find_one({"token": token})
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
                            else: # assume it's a document
                                await update.message.reply_document(
                                    document=telegram_file_id,
                                    caption=f"यहाँ आपकी फ़ाइल है: {original_filename}",
                                    filename=original_filename 
                                )
                            logger.info(f"Batch file {original_filename} sent to user {user.id}")
                        except Exception as e:
                            logger.error(f"Error sending batch file {original_filename} to user {user.id}: {e}")
                            await update.message.reply_text(f"क्षमा करें, बैच फ़ाइल '{original_filename}' नहीं भेजी जा सकी। एक त्रुटि हुई: {e}")
                    else:
                        await update.message.reply_text(f"क्षमा करें, बैच में एक फ़ाइल के लिए डेटा नहीं मिला: {token}")
                await update.message.reply_text("सभी बैच फ़ाइलें भेजी गईं!")
                # बैच को एक बार भेजने के बाद हटा दें ताकि इसे दोबारा एक्सेस न किया जा सके
                batches_collection.delete_one({"_id": batch_id})
                logger.info(f"Batch {batch_id} deleted from MongoDB after sending.")
                return 
            else:
                logger.warning(f"Invalid or expired batch token {batch_id} requested by user {user.id} after verification.")
                await update.message.reply_text("अमान्य या समाप्त बैच डाउनलोड अनुरोध। कृपया पुनः प्रयास करें या एक नई फ़ाइल अपलोड करें।")
                return 
        elif param.startswith("download_"):
            # यह तब होता है जब यूजर ब्लॉगर पेज पर सिंगल फ़ाइल सत्यापन के बाद वापस बॉट पर रीडायरेक्ट होता है।
            original_permanent_token = param[len("download_"):]
            logger.info(f"Single file download deep link received after verification: {original_permanent_token}")
            
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
                        logger.info(f"Video {original_filename} sent to user {user.id}")
                    else: # assume it's a document
                        await update.message.reply_document(
                            document=telegram_file_id,
                            caption=f"यहाँ आपकी फ़ाइल है: {original_filename}",
                            filename=original_filename 
                        )
                        logger.info(f"Document {original_filename} sent to user {user.id}")
                except Exception as e:
                    logger.error(f"Error sending file {original_filename} to user {user.id}: {e}")
                    await update.message.reply_text(f"क्षमा करें, फ़ाइल नहीं भेजी जा सकी। एक त्रुटि हुई: {e}")
                return 
            else:
                logger.warning(f"Invalid permanent token {original_permanent_token} requested by user {user.id} after verification.")
                await update.message.reply_text("अमान्य या समाप्त डाउनलोड अनुरोध। कृपया पुनः प्रयास करें या एक नई फ़ाइल अपलोड करें।")
                return 
        else:
            # यह तब होता है जब यूजर स्थायी Telegram डीप लिंक पर क्लिक करता है (पहली बार)
            # यह अब सीधे Apps Script पर रीडायरेक्ट करेगा
            permanent_token_from_deep_link = param 
            logger.info(f"Initial permanent deep link received: {permanent_token_from_deep_link}")

            # Apps Script के doGet को कॉल करें जो ब्लॉगर पर रीडायरेक्ट करेगा
            # Apps Script URL में token या batch_token के रूप में permanent_token_from_deep_link भेजें
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
    user = update.effective_user
    await update_user_info(user.id, user.username, user.first_name)
    logger.info(f"/link command received from {user.id}")
    user_id = user.id
    if user_id in batch_files_in_progress:
        del batch_files_in_progress[user_id]
        logger.info(f"Cleared pending batch for user {user_id} when /link was used.")
    context.user_data['current_mode'] = 'single_file'
    await update.message.reply_text("कृपया मुझे वह फ़ाइल (डॉक्यूमेंट या वीडियो) भेजें जिसकी आप लिंक जनरेट करना चाहते हैं।")

async def batch_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    await update_user_info(user.id, user.username, user.first_name)
    logger.info(f"/batch command received from {user.id}")
    user_id = user.id
    
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
    user = update.effective_user
    await update_user_info(user.id, user.username, user.first_name)
    user_id = user.id
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
        "token": permanent_token, 
        "telegram_file_id": permanent_telegram_file_id,
        "original_filename": original_filename,
        "user_chat_id": user_chat_id,
        "upload_time": datetime.datetime.now(), 
        "file_type": file_type
    }
    files_collection.insert_one(file_info)
    logger.info(f"File {original_filename} (permanent token: {permanent_token}) saved to MongoDB.")

    batch_files_in_progress[user_id].append(permanent_token) 

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
    user = update.effective_user
    await update_user_info(user.id, user.username, user.first_name)
    logger.info(f"Generate batch links button pressed by {user.id}")
    await update.callback_query.answer("लिंक जनरेट कर रहा हूँ...") 
    user_id = user.id
    
    if user_id not in batch_files_in_progress or not batch_files_in_progress[user_id]:
        await update.callback_query.message.reply_text("कोई फ़ाइलें नहीं मिलीं जिनके लिए लिंक जनरेट की जा सकें। कृपया पहले फ़ाइलें भेजें।")
        logger.warning(f"Generate batch links pressed but no files in progress for user {user_id}")
        return ConversationHandler.END 
    
    # एक नया बैच ID जनरेट करें और MongoDB में सहेजें
    batch_id = str(uuid.uuid4())
    batch_info = {
        "_id": batch_id, # बैच ID को _id के रूप में उपयोग करें
        "permanent_tokens": batch_files_in_progress[user_id],
        "user_id": user_id,
        "creation_time": datetime.datetime.now()
    }
    batches_collection.insert_one(batch_info)
    logger.info(f"Batch {batch_id} saved to MongoDB with {len(batch_files_in_progress[user_id])} files.")

    # Apps Script URL बनाएं जो ब्लॉगर पर रीडायरेक्ट करेगा (batch_token के साथ)
    apps_script_redirect_url = f"{GOOGLE_APPS_SCRIPT_API_URL}?batch_token={batch_id}"
    logger.info(f"Generated Apps Script redirect URL for batch Blogger: {apps_script_redirect_url}")

    keyboard = [
        [InlineKeyboardButton("बैच फ़ाइलें डाउनलोड करें", url=apps_script_redirect_url)], 
        [InlineKeyboardButton("कॉपी लिंक", callback_data=f"copy_batch_link_{batch_id}")] # नया कॉपी लिंक बटन
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.callback_query.message.reply_text(
        "आपकी बैच फ़ाइलें सहेजी गई हैं! यह लिंक स्थायी है। आगे बढ़ने और एक छोटा सा कार्य पूरा करने के लिए 'बैच फ़ाइलें डाउनलोड करें' पर क्लिक करें:",
        reply_markup=reply_markup
    )
    
    del batch_files_in_progress[user_id]
    context.user_data.pop('current_mode', None)
    return ConversationHandler.END

async def cancel_batch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    await update_user_info(user.id, user.username, user.first_name)
    logger.info(f"Batch cancelled by {user.id}")
    user_id = user.id
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
    user = update.effective_user
    await update_user_info(user.id, user.username, user.first_name)
    if context.user_data.get('current_mode') == 'batch_file':
        logger.info(f"File received in batch mode from {user.id}. Passing to batch handler.")
        return await handle_batch_file_received(update, context)

    logger.info(f"Single file received from {user.id}")
    file = None
    file_type = ""
    if update.message.document:
        file = update.message.document
        file_type = "document"
    elif update.message.video:
        file = update.message.video
        file_type = "video"
    else:
        logger.info(f"Unsupported file type received from {user.id} in single mode.")
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
        "token": permanent_token, 
        "telegram_file_id": permanent_telegram_file_id,
        "original_filename": original_filename,
        "user_chat_id": user_chat_id,
        "upload_time": datetime.datetime.now(), 
        "file_type": file_type
    }
    files_collection.insert_one(file_info)
    logger.info(f"Single file {original_filename} (permanent token: {permanent_token}) saved to MongoDB.")

    # अब Apps Script URL बनाएं जो ब्लॉगर पर रीडायरेक्ट करेगा
    apps_script_redirect_url = f"{GOOGLE_APPS_SCRIPT_API_URL}?token={permanent_token}" 
    logger.info(f"Generated Apps Script redirect URL for Blogger: {apps_script_redirect_url}")
    
    keyboard = [
        [InlineKeyboardButton("फ़ाइल डाउनलोड करें", url=apps_script_redirect_url)], 
        [InlineKeyboardButton("कॉपी लिंक", callback_data=f"copy_link_{permanent_token}")] # नया कॉपी लिंक बटन
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "आपकी फ़ाइल सहेजी गई है! यह लिंक स्थायी है। आगे बढ़ने और एक छोटा सा कार्य पूरा करने के लिए 'फ़ाइल डाउनलोड करें' पर क्लिक करें:",
        reply_markup=reply_markup
    )
    context.user_data.pop('current_mode', None) 

# --- New Callback Handler for Copy Link ---
async def copy_link_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer("लिंक कॉपी करने के लिए तैयार...")

    data = query.data
    apps_script_url = ""
    if data.startswith("copy_batch_link_"):
        batch_id = data[len("copy_batch_link_"):]
        apps_script_url = f"{GOOGLE_APPS_SCRIPT_API_URL}?batch_token={batch_id}"
        message_text = f"यह आपकी बैच फ़ाइलों के लिए स्थायी लिंक है:\n\n`{apps_script_url}`\n\nइसे कॉपी करने के लिए टैप करके रखें।"
    elif data.startswith("copy_link_"):
        permanent_token = data[len("copy_link_"):]
        apps_script_url = f"{GOOGLE_APPS_SCRIPT_API_URL}?token={permanent_token}"
        message_text = f"यह आपकी फ़ाइल के लिए स्थायी लिंक है:\n\n`{apps_script_url}`\n\nइसे कॉपी करने के लिए टैप करके रखें।"
    else:
        message_text = "कॉपी करने के लिए अमान्य लिंक प्रकार।"
    
    await query.message.reply_text(message_text, parse_mode='MarkdownV2')


# --- New Commands ---
async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    await update_user_info(user.id, user.username, user.first_name)
    logger.info(f"/stats command received from {user.id}")

    total_files = files_collection.count_documents({})
    total_users = users_collection.count_documents({})
    total_batches = batches_collection.count_documents({})
    
    stats_text = (
        f"📊 **बॉट आंकड़े**\n"
        f"कुल फ़ाइलें संग्रहीत: `{total_files}`\n"
        f"कुल बैच: `{total_batches}`\n"
        f"कुल उपयोगकर्ता: `{total_users}`"
    )
    await update.message.reply_text(stats_text, parse_mode='MarkdownV2')

async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    await update_user_info(user.id, user.username, user.first_name)
    logger.info(f"/broadcast command received from {user.id}")

    if user.id != ADMIN_USER_ID:
        await update.message.reply_text("आपको यह कमांड उपयोग करने की अनुमति नहीं है।")
        logger.warning(f"Unauthorized broadcast attempt by user {user.id}")
        return

    if not context.args:
        await update.message.reply_text("कृपया प्रसारण के लिए एक संदेश प्रदान करें।\nउदाहरण: `/broadcast नमस्ते सभी को!`")
        return

    message_to_send = " ".join(context.args)
    
    users = users_collection.find({})
    sent_count = 0
    failed_count = 0

    await update.message.reply_text("प्रसारण संदेश भेज रहा हूँ...")

    for target_user in users:
        try:
            await context.bot.send_message(chat_id=target_user["_id"], text=message_to_send)
            sent_count += 1
        except Exception as e:
            logger.error(f"Failed to send broadcast to user {target_user['_id']}: {e}")
            failed_count += 1
    
    await update.message.reply_text(f"प्रसारण समाप्त।\nभेजा गया: {sent_count}\nविफल: {failed_count}")
    logger.info(f"Broadcast completed. Sent: {sent_count}, Failed: {failed_count}")

async def dellink_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    await update_user_info(user.id, user.username, user.first_name)
    logger.info(f"/dellink command received from {user.id}")

    if user.id != ADMIN_USER_ID:
        await update.message.reply_text("आपको यह कमांड उपयोग करने की अनुमति नहीं है।")
        logger.warning(f"Unauthorized dellink attempt by user {user.id}")
        return

    if not context.args:
        await update.message.reply_text("कृपया हटाने के लिए स्थायी टोकन प्रदान करें।\nउदाहरण: `/dellink 1234abcd-5678-ijkl-90mn-opqrstuvwxyz`")
        return

    token_to_delete = context.args[0]
    
    # files_collection से हटाएँ
    delete_result_file = files_collection.delete_one({"token": token_to_delete})
    
    # batches_collection से हटाएँ यदि यह किसी बैच का हिस्सा है
    # हम बैच को अपडेट करेंगे, पूरे बैच को नहीं हटाएंगे जब तक कि यह आखिरी फ़ाइल न हो
    batches_collection.update_many(
        {"permanent_tokens": token_to_delete},
        {"$pull": {"permanent_tokens": token_to_delete}}
    )
    # यदि कोई बैच खाली हो जाता है, तो उसे हटा दें
    batches_collection.delete_many({"permanent_tokens": {"$size": 0}})

    if delete_result_file.deleted_count > 0:
        await update.message.reply_text(f"टोकन `{token_to_delete}` और संबंधित फ़ाइल जानकारी सफलतापूर्वक हटा दी गई।")
        logger.info(f"Token {token_to_delete} deleted by admin {user.id}.")
    else:
        await update.message.reply_text(f"टोकन `{token_to_delete}` नहीं मिला।")
        logger.warning(f"Dellink command: Token {token_to_delete} not found for deletion by admin {user.id}.")


def main() -> None:
    required_env_vars = ["TELEGRAM_BOT_TOKEN", "MONGO_URI", "PUBLIC_CHANNEL_USERNAME", "PUBLIC_CHANNEL_ID", "GOOGLE_APPS_SCRIPT_API_URL", "ADMIN_USER_ID"] 
    for var in required_env_vars:
        if not os.getenv(var):
            logger.error(f"त्रुटि: आवश्यक पर्यावरण चर '{var}' गायब है। कृपया इसे सेट करें।")
            exit(1) 
    
    # ADMIN_USER_ID को int में बदलने का प्रयास करें
    try:
        global ADMIN_USER_ID
        ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID"))
    except (ValueError, TypeError):
        logger.error("त्रुटि: ADMIN_USER_ID एक वैध पूर्णांक नहीं है। कृपया इसे सही ढंग से सेट करें।")
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
    
    # New command handlers
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CommandHandler("broadcast", broadcast_command))
    application.add_handler(CommandHandler("dellink", dellink_command))
    
    # New CallbackQueryHandler for copy link buttons
    application.add_handler(CallbackQueryHandler(copy_link_callback, pattern="^copy_link_.*"))
    application.add_handler(CallbackQueryHandler(copy_link_callback, pattern="^copy_batch_link_.*"))


    logger.info("बॉट चल रहा है...")
    application.run_polling() 

if __name__ == "__main__":
    main()
