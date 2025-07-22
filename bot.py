import os
import uuid
import datetime
import logging 

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
PUBLIC_CHANNEL_USERNAME = os.getenv("PUBLIC_CHANNEL_USERNAME")
PUBLIC_CHANNEL_ID = int(os.getenv("PUBLIC_CHANNEL_ID")) 

EXTERNAL_API_BASE_URL = os.getenv("EXTERNAL_API_BASE_URL") 

UPDATES_CHANNEL_LINK = "https://t.me/asbhai_bsr" 

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
            original_token = param[len("download_"):]
            logger.info(f"Download deep link received for token: {original_token}")
            
            file_data = files_collection.find_one({"token": original_token})

            if file_data:
                upload_time = file_data.get("upload_time")
                # 5 मिनट (300 सेकंड) के बाद लिंक समाप्त करें
                if upload_time and (datetime.datetime.now() - upload_time).total_seconds() > 300: 
                    await update.message.reply_text(
                        "यह डाउनलोड लिंक समाप्त हो गई है। कृपया एक नई लिंक प्राप्त करने के लिए फ़ाइल को फिर से अपलोड करें।"
                    )
                    files_collection.delete_one({"token": original_token}) # MongoDB से समाप्त लिंक हटाएँ
                    logger.info(f"Expired token {original_token} deleted from DB.")
                    return

                # सुनिश्चित करें कि फ़ाइल केवल उसी यूज़र को मिले जिसने इसे अपलोड किया है
                if update.effective_chat.id != file_data.get("user_chat_id"):
                    await update.message.reply_text("यह फ़ाइल आपके लिए नहीं है, या लिंक अमान्य है।")
                    logger.warning(f"Unauthorized download attempt for token {original_token} by user {update.effective_chat.id}")
                    return

                telegram_file_id = file_data["telegram_file_id"]
                original_filename = file_data["original_filename"]
                try:
                    if file_data.get("file_type") == "video": 
                        await update.message.reply_video(
                            video=telegram_file_id,
                            caption=f"यहाँ आपकी वीडियो है: {original_filename}",
                            filename=original_filename # फ़ाइल को उसके मूल नाम से भेजें
                        )
                        logger.info(f"Video {original_filename} sent to user {update.effective_user.id}")
                    else: # assume it's a document
                        await update.message.reply_document(
                            document=telegram_file_id,
                            caption=f"यहाँ आपकी फ़ाइल है: {original_filename}",
                            filename=original_filename # फ़ाइल को उसके मूल नाम से भेजें
                        )
                        logger.info(f"Document {original_filename} sent to user {update.effective_user.id}")
                    # files_collection.delete_one({"token": original_token}) # यदि एक बार डाउनलोड के बाद हटाना चाहते हैं
                except Exception as e:
                    logger.error(f"Error sending file {original_filename} to user {update.effective_user.id}: {e}")
                    await update.message.reply_text(f"क्षमा करें, फ़ाइल नहीं भेजी जा सकी। एक त्रुटि हुई: {e}")
            else:
                logger.warning(f"Invalid or expired token {original_token} requested by user {update.effective_user.id}")
                await update.message.reply_text("अमान्य या समाप्त डाउनलोड अनुरोध। कृपया पुनः प्रयास करें या एक नई फ़ाइल अपलोड करें।")
        else:
            await send_welcome_message(update, context) 
    else:
        await send_welcome_message(update, context)

async def send_welcome_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.info("Sending welcome message.")
    keyboard = [
        [InlineKeyboardButton("Updates Channel", url=UPDATES_CHANNEL_LINK)],
        [InlineKeyboardButton("Help", callback_data="help_command")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    # जाँच करें कि क्या अपडेट एक मैसेज है या कॉलबैक क्वेरी
    if update.message:
        await update.message.reply_text(
            "👋 नमस्ते! मैं आपकी फ़ाइल साझा करने वाला बॉट हूँ। मैं आपकी फ़ाइलों के लिए साझा करने योग्य लिंक बनाने में आपकी मदद कर सकता हूँ।",
            reply_markup=reply_markup
        )
    elif update.callback_query:
        # कॉलबैक क्वेरी के लिए, मूल संदेश को संपादित करें
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
        await update.callback_query.answer() # कॉलबैक क्वेरी को स्वीकार करें
        chat_id = update.callback_query.message.chat_id
        message_id = update.callback_query.message.message_id
        
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=help_text,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("पीछे", callback_data="back_to_welcome")]])
        )
        logger.info("Help message sent via callback edit.")
    else: # यदि यह सीधे /help कमांड के रूप में आया है
        await update.message.reply_text(help_text)
        logger.info("Help message sent via direct command.")

async def back_to_welcome(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.info("Back to welcome button pressed.")
    await update.callback_query.answer() # कॉलबैक क्वेरी को स्वीकार करें
    await send_welcome_message(update, context) # welcome message will use edit_text due to update.callback_query

# --- Single File Link Generation ---
async def link_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.info(f"/link command received from {update.effective_user.id}")
    user_id = update.effective_user.id
    # सुनिश्चित करें कि कोई भी लंबित बैच स्थिति साफ़ हो
    if user_id in batch_files_in_progress:
        del batch_files_in_progress[user_id]
        logger.info(f"Cleared pending batch for user {user_id} when /link was used.")
    context.user_data['current_mode'] = 'single_file'
    await update.message.reply_text("कृपया मुझे वह फ़ाइल (डॉक्यूमेंट या वीडियो) भेजें जिसकी आप लिंक जनरेट करना चाहते हैं।")

# --- Batch File Link Generation ---
async def batch_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.info(f"/batch command received from {update.effective_user.id}")
    user_id = update.effective_user.id
    
    # यदि पहले से कोई बैच चल रहा है, तो उसे रीसेट करें
    if user_id in batch_files_in_progress:
        logger.info(f"Existing batch for user {user_id} found, resetting.")
        batch_files_in_progress[user_id] = []
    else:
        batch_files_in_progress[user_id] = [] # बैच फ़ाइलों के लिए सूची को इनिशियलाइज़ करें

    context.user_data['current_mode'] = 'batch_file'

    keyboard = [[InlineKeyboardButton("लिंक जनरेट करें", callback_data="generate_batch_links")]]
    # रद्द करने का बटन जोड़ें ताकि यूज़र फंसें नहीं
    keyboard.append([InlineKeyboardButton("रद्द करें", callback_data="cancel_batch_generation")]) 
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "ठीक है, मुझे एक-एक करके फ़ाइलें (डॉक्यूमेंट या वीडियो) भेजें। "
        "प्रत्येक फ़ाइल भेजने के बाद मैं आपको सूचित करूँगा।\n\n"
        "जब आप सभी फ़ाइलें भेज दें, तो 'लिंक जनरेट करें' बटन पर क्लिक करें। यदि आप रद्द करना चाहते हैं तो 'रद्द करें' दबाएं।",
        reply_markup=reply_markup
    )
    return SENDING_BATCH_FILES # बातचीत को इस स्टेट में रखें

async def handle_batch_file_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    logger.info(f"Batch file received from {user_id}")
    if user_id not in batch_files_in_progress:
        logger.warning(f"File received for batch from {user_id} but no batch started. Falling back to single file.")
        # यदि कोई बैच प्रगति पर नहीं है, तो इसे सिंगल फ़ाइल के रूप में हैंडल करें
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
        return # unsupported file type, stay in conversation

    original_filename = file.file_name if file.file_name else f"unnamed_{file_type}"
    user_chat_id = update.message.chat_id

    try:
        # फ़ाइल को सार्वजनिक चैनल पर फॉरवर्ड करें
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
            return # stay in conversation

    except Exception as e:
        logger.error(f"Error forwarding file {original_filename} to storage channel: {e}")
        await update.message.reply_text(f"स्टोरेज चैनल पर फ़ाइल फ़ॉरवर्ड करने में त्रुटि: {e}")
        return # stay in conversation

    unique_token = str(uuid.uuid4())

    file_info = {
        "token": unique_token,
        "telegram_file_id": permanent_telegram_file_id,
        "original_filename": original_filename,
        "user_chat_id": user_chat_id,
        "upload_time": datetime.datetime.now(),
        "file_type": file_type
    }
    files_collection.insert_one(file_info)
    logger.info(f"File {original_filename} (token: {unique_token}) saved to MongoDB.")

    batch_files_in_progress[user_id].append(unique_token)

    keyboard = [[InlineKeyboardButton("लिंक जनरेट करें", callback_data="generate_batch_links")]]
    keyboard.append([InlineKeyboardButton("रद्द करें", callback_data="cancel_batch_generation")]) # रद्द करें बटन
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "फ़ाइल प्राप्त हुई! अधिक फ़ाइलें भेजें या समाप्त करने के लिए 'लिंक जनरेट करें' पर क्लिक करें।",
        reply_markup=reply_markup
    )
    return SENDING_BATCH_FILES # बातचीत को इसी स्टेट में रखें


# MarkdownV2 स्पेशल कैरेक्टर को एस्केप करने के लिए सहायक फ़ंक्शन
def escape_markdown_v2(text: str) -> str:
    # केवल वे कैरेक्टर एस्केप करें जो MarkdownV2 में विशेष अर्थ रखते हैं
    # और जो आपके literal text में आ सकते हैं।
    # URL के अंदर के कैरेक्टर को एस्केप करने की आवश्यकता नहीं होती,
    # केवल डिस्प्ले टेक्स्ट में।
    escape_chars = r'_*[]()~`>#+-=|{}.!' 
    return ''.join(['\\' + char if char in escape_chars else char for char in text])


async def generate_batch_links(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    logger.info(f"Generate batch links button pressed by {update.effective_user.id}")
    await update.callback_query.answer("लिंक जनरेट कर रहा हूँ...") # यूज़र को तुरंत फीडबैक दें
    user_id = update.effective_user.id
    
    if user_id not in batch_files_in_progress or not batch_files_in_progress[user_id]:
        await update.callback_query.message.reply_text("कोई फ़ाइलें नहीं मिलीं जिनके लिए लिंक जनरेट की जा सकें। कृपया पहले फ़ाइलें भेजें।")
        logger.warning(f"Generate batch links pressed but no files in progress for user {user_id}")
        return ConversationHandler.END # बातचीत खत्म करें
    
    links_text = "यहाँ आपकी डाउनलोड लिंक्स हैं:\n\n"
    
    for token in batch_files_in_progress[user_id]:
        external_api_link = f"{EXTERNAL_API_BASE_URL}?return_to_bot={token}"
        
        # डिस्प्ले टेक्स्ट में टोकन के पहले 8 कैरेक्टर को एस्केप करें और "..." को भी
        display_text = escape_markdown_v2(token[:8]) + escape_markdown_v2("...")
        
        # Markdown V2 लिंक फ़ॉर्मेट: [text](<url>) - URL को <> से घेरना सुनिश्चित करें
        links_text += f"👉 [{display_text}](<{external_api_link}>)\n"
    
    try:
        await update.callback_query.message.reply_text(
            links_text, 
            parse_mode='MarkdownV2', 
            disable_web_page_preview=True # ताकि Telegram लिंक का प्रीव्यू न बनाए
        )
        logger.info(f"Batch links sent to user {user_id}")
    except telegram.error.BadRequest as e:
        logger.error(f"Error sending MarkdownV2 batch links to user {user_id}: {e}")
        # यदि अभी भी कोई पार्सिंग एरर आती है, तो बिना Markdown के भेजें
        fallback_links_text = "लिंक जनरेट करने में समस्या हुई। यहाँ रॉ लिंक्स हैं (कृपया मैन्युअल रूप से कॉपी करें):\n\n" + \
                              "\n".join([f"👉 {EXTERNAL_API_BASE_URL}?return_to_bot={t}" 
                                         for t in batch_files_in_progress[user_id]])
        await update.callback_query.message.reply_text(fallback_links_text)
    
    del batch_files_in_progress[user_id] # बैच क्लियर करें
    context.user_data.pop('current_mode', None) # मोड क्लियर करें
    return ConversationHandler.END # बातचीत खत्म करें

async def cancel_batch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    logger.info(f"Batch cancelled by {update.effective_user.id}")
    user_id = update.effective_user.id
    if user_id in batch_files_in_progress:
        del batch_files_in_progress[user_id]
        logger.info(f"Cleared batch in progress for user {user_id}.")
    context.user_data.pop('current_mode', None)
    
    # यदि कॉलबैक क्वेरी से आया है (जैसे बटन से)
    if update.callback_query:
        await update.callback_query.answer("बैच जनरेशन रद्द कर दिया गया।")
        await update.callback_query.message.reply_text(
            "बैच फ़ाइल जनरेशन रद्द कर दिया गया।"
        )
    else: # यदि कमांड से आया है (/cancel)
        await update.message.reply_text(
            "बैच फ़ाइल जनरेशन रद्द कर दिया गया।"
        )
    
    return ConversationHandler.END


# --- General File Handler (for /link command or fallback) ---
async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # यदि यूज़र बैच मोड में है, तो बैच हैंडलर को भेजें
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

    unique_token = str(uuid.uuid4())

    file_info = {
        "token": unique_token,
        "telegram_file_id": permanent_telegram_file_id,
        "original_filename": original_filename,
        "user_chat_id": user_chat_id,
        "upload_time": datetime.datetime.now(),
        "file_type": file_type
    }
    files_collection.insert_one(file_info)
    logger.info(f"Single file {original_filename} (token: {unique_token}) saved to MongoDB.")

    external_api_link = f"{EXTERNAL_API_BASE_URL}?return_to_bot={unique_token}"
    
    keyboard = [
        [InlineKeyboardButton("फ़ाइल डाउनलोड करें", url=external_api_link)],
        [InlineKeyboardButton("फ़ाइल कैसे डाउनलोड करें", url="https://google.com")] # <-- इस URL को अपनी वास्तविक मदद पेज लिंक से बदलें!
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "आपकी फ़ाइल सहेजी गई है! आगे बढ़ने और एक छोटा सा कार्य पूरा करने के लिए 'फ़ाइल डाउनलोड करें' पर क्लिक करें:",
        reply_markup=reply_markup
    )
    context.user_data.pop('current_mode', None) # सिंगल फ़ाइल प्रोसेसिंग के बाद मोड क्लियर करें


def main() -> None:
    # सुनिश्चित करें कि सभी आवश्यक पर्यावरण चर सेट हैं
    required_env_vars = ["TELEGRAM_BOT_TOKEN", "MONGO_URI", "PUBLIC_CHANNEL_USERNAME", "PUBLIC_CHANNEL_ID", "EXTERNAL_API_BASE_URL"]
    for var in required_env_vars:
        if not os.getenv(var):
            logger.error(f"त्रुटि: आवश्यक पर्यावरण चर '{var}' गायब है। कृपया इसे सेट करें।")
            exit(1) # यदि कोई महत्वपूर्ण चर गायब है तो बाहर निकलें

    # Flask ऐप को एक अलग थ्रेड में चलाएं
    threading.Thread(target=run_flask_app).start()

    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # --- हैंडलर ---
    application.add_handler(CommandHandler("start", start))
    
    # कॉलबैक क्वेरी हैंडलर
    application.add_handler(CallbackQueryHandler(help_command, pattern="^help_command$"))
    application.add_handler(CallbackQueryHandler(back_to_welcome, pattern="^back_to_welcome$"))

    # सिंगल फ़ाइल लिंक जनरेशन
    application.add_handler(CommandHandler("link", link_command))

    # बैच फ़ाइल लिंक जनरेशन के लिए ConversationHandler
    batch_conv_handler = ConversationHandler(
        entry_points=[CommandHandler("batch", batch_start)], # /batch कमांड से बातचीत शुरू करें
        states={
            SENDING_BATCH_FILES: [
                MessageHandler(filters.ATTACHMENT, handle_batch_file_received), # अटैचमेंट (फ़ाइलें) हैंडल करें
                CallbackQueryHandler(generate_batch_links, pattern="^generate_batch_links$"), # "लिंक जनरेट करें" बटन
                CallbackQueryHandler(cancel_batch, pattern="^cancel_batch_generation$"), # "रद्द करें" बटन
                CommandHandler("cancel", cancel_batch) # /cancel कमांड
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel_batch), CallbackQueryHandler(cancel_batch, pattern="^cancel_batch_generation$")], # बातचीत से बाहर निकलने के लिए फ़ॉलबैक
    )
    application.add_handler(batch_conv_handler)

    # सभी अटैचमेंट (डॉक्यूमेंट/वीडियो) को हैंडल करने के लिए सामान्य हैंडलर (जो किसी विशिष्ट मोड में न हों)
    application.add_handler(MessageHandler(filters.ATTACHMENT, handle_file))

    logger.info("बॉट चल रहा है...")
    # Telegram बॉट को पोलिंग मोड में चलाएं
    # allowed_updates=Update.ALL_TYPES को हटाना अक्सर Conflict एरर को कम करने में मदद करता है
    application.run_polling() 

if __name__ == "__main__":
    main()
