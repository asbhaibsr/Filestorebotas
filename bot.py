import os
import uuid
import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters, ContextTypes,
    ConversationHandler,
    CallbackQueryHandler # <--- यह इम्पोर्ट किया गया है
)
from pymongo import MongoClient
from flask import Flask
import threading

# --- Configuration ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
# सुनिश्चित करें कि यह आपके PUBLIC चैनल का यूज़रनेम है (बिना @ के)
PUBLIC_CHANNEL_USERNAME = os.getenv("PUBLIC_CHANNEL_USERNAME")
# PUBLIC_CHANNEL_ID को हमेशा int में बदलें
PUBLIC_CHANNEL_ID = int(os.getenv("PUBLIC_CHANNEL_ID")) 

# आपका External API (Google Apps Script) बेस URL
EXTERNAL_API_BASE_URL = os.getenv("EXTERNAL_API_BASE_URL") # <-- सुनिश्चित करें कि Koyeb पर यह ENV VAR सेट है

# आपका Updates Channel Link
UPDATES_CHANNEL_LINK = "https://t.me/asbhai_bsr" # <-- यह लिंक अब सही है

# MongoDB Configuration
MONGO_URI = os.getenv("MONGO_URI")
client = MongoClient(MONGO_URI)
db = client.file_bot # आपका डेटाबेस नाम
files_collection = db.files # आपका कलेक्शन नाम
# बैच फ़ाइलों के लिए अस्थायी स्टोरेज प्रति यूज़र
# Key: user_id, Value: list of tokens
batch_files_in_progress = {} 

# --- Conversation States for Batch Command ---
SENDING_BATCH_FILES = 1

# --- Flask App for Health Check ---
flask_app = Flask(__name__)

@flask_app.route('/health')
def health_check():
    """
    Koyeb जैसे डिप्लॉयमेंट प्लेटफ़ॉर्म द्वारा उपयोग के लिए एक साधारण हेल्थ चेक एंडपॉइंट।
    यह पुष्टि करता है कि वेब सर्वर चल रहा है।
    """
    return "Bot is healthy!", 200

def run_flask_app():
    """
    Flask एप्लिकेशन को एक अलग थ्रेड में चलाने के लिए फ़ंक्शन।
    यह सुनिश्चित करता है कि बॉट का `run_polling` ब्लॉक न हो।
    """
    # पोर्ट को पर्यावरण वेरिएबल से प्राप्त करें, डिफ़ॉल्ट 8000
    port = int(os.getenv("PORT", 8000))
    flask_app.run(host='0.0.0.0', port=port)

# --- Bot Handlers ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args # /start कमांड से पैरामीटर प्राप्त करें

    if args:
        param = args[0]
        if param.startswith("download_"):
            # यह बाहरी API से प्राप्त कॉल बैक है
            original_token = param[len("download_"):]
            
            # MongoDB से फ़ाइल जानकारी प्राप्त करें
            file_data = files_collection.find_one({"token": original_token})

            if file_data:
                # 5 मिनट की वैधता की जाँच करें
                upload_time = file_data.get("upload_time")
                # 300 सेकंड = 5 मिनट
                if upload_time and (datetime.datetime.now() - upload_time).total_seconds() > 300: 
                    await update.message.reply_text(
                        "यह डाउनलोड लिंक समाप्त हो गई है। कृपया एक नई लिंक प्राप्त करने के लिए फ़ाइल को फिर से अपलोड करें।"
                    )
                    # DB से समाप्त टोकन को हटाएँ
                    files_collection.delete_one({"token": original_token}) 
                    return

                # सुनिश्चित करें कि फ़ाइल सही यूज़र (जिसने डाउनलोड शुरू किया था) को भेजी गई है
                if update.effective_chat.id != file_data.get("user_chat_id"):
                    await update.message.reply_text("यह फ़ाइल आपके लिए नहीं है, या लिंक अमान्य है।")
                    return

                # वास्तविक फ़ाइल भेजें
                telegram_file_id = file_data["telegram_file_id"]
                original_filename = file_data["original_filename"]
                try:
                    # फ़ाइल के प्रकार के अनुसार भेजें
                    if file_data.get("file_type") == "video": 
                        await update.message.reply_video(
                            video=telegram_file_id,
                            caption=f"यहाँ आपकी वीडियो है: {original_filename}",
                            filename=original_filename
                        )
                    else: # अन्य प्रकारों के लिए डिफ़ॉल्ट रूप से डॉक्यूमेंट
                        await update.message.reply_document(
                            document=telegram_file_id,
                            caption=f"यहाँ आपकी फ़ाइल है: {original_filename}",
                            filename=original_filename
                        )
                    # वैकल्पिक रूप से सफल डाउनलोड के बाद टोकन हटाएँ
                    # files_collection.delete_one({"token": original_token})
                except Exception as e:
                    await update.message.reply_text(f"क्षमा करें, फ़ाइल नहीं भेजी जा सकी। एक त्रुटि हुई: {e}")
            else:
                await update.message.reply_text("अमान्य या समाप्त डाउनलोड अनुरोध। कृपया पुनः प्रयास करें या एक नई फ़ाइल अपलोड करें।")
        else:
            # यदि /start कमांड को सीधे कोई टोकन मिलता है जो डाउनलोड_ से शुरू नहीं होता
            await send_welcome_message(update, context) 
    else:
        # बिना डीप लिंक के मानक /start कमांड
        await send_welcome_message(update, context)

async def send_welcome_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    keyboard = [
        [InlineKeyboardButton("Updates Channel", url=UPDATES_CHANNEL_LINK)],
        [InlineKeyboardButton("Help", callback_data="help_command")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    # यदि अपडेट एक मैसेज है
    if update.message:
        await update.message.reply_text(
            "👋 नमस्ते! मैं आपकी फ़ाइल साझा करने वाला बॉट हूँ। मैं आपकी फ़ाइलों के लिए साझा करने योग्य लिंक बनाने में आपकी मदद कर सकता हूँ।",
            reply_markup=reply_markup
        )
    # यदि अपडेट एक कॉलबैक क्वेरी (जैसे /start कमांड के बाद 'Back' बटन) है
    elif update.callback_query:
        await update.callback_query.message.edit_text(
            "👋 नमस्ते! मैं आपकी फ़ाइल साझा करने वाला बॉट हूँ। मैं आपकी फ़ाइलों के लिए साझा करने योग्य लिंक बनाने में आपकी मदद कर सकता हूँ।",
            reply_markup=reply_markup
        )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # जाँच करें कि क्या यह कॉलबैक क्वेरी से है या सीधे कमांड से
    if update.callback_query:
        await update.callback_query.answer() # कॉलबैक स्वीकार करें
        chat_id = update.callback_query.message.chat_id
        message_id = update.callback_query.message.message_id
        
        help_text = (
            "यहाँ वे कमांड दिए गए हैं जिनका आप उपयोग कर सकते हैं:\n\n"
            "➡️ /start - स्वागत संदेश प्राप्त करें।\n"
            "➡️ /link - एक फ़ाइल के लिए साझा करने योग्य लिंक प्राप्त करें।\n"
            "➡️ /batch - एक साथ कई फ़ाइलों के लिए लिंक जनरेट करें।\n\n"
            "कमांड /link या /batch का उपयोग करने के बाद मुझे कोई भी डॉक्यूमेंट या वीडियो भेजें।"
        )
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=help_text,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("पीछे", callback_data="back_to_welcome")]]) # वापस जाने वाला बटन जोड़ें
        )
    else: # यदि यह एक सीधा /help कमांड है
        help_text = (
            "यहाँ वे कमांड दिए गए हैं जिनका आप उपयोग कर सकते हैं:\n\n"
            "➡️ /start - स्वागत संदेश प्राप्त करें।\n"
            "➡️ /link - एक फ़ाइल के लिए साझा करने योग्य लिंक प्राप्त करें।\n"
            "➡️ /batch - एक साथ कई फ़ाइलों के लिए लिंक जनरेट करें।\n\n"
            "कमांड /link या /batch का उपयोग करने के बाद मुझे कोई भी डॉक्यूमेंट या वीडियो भेजें।"
        )
        await update.message.reply_text(help_text)

async def back_to_welcome(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()
    await send_welcome_message(update, context) # स्वागत संदेश को फिर से भेजें (यह edit_text का उपयोग करेगा)

# --- Single File Link Generation ---
async def link_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data['current_mode'] = 'single_file' # handle_file के लिए मोड सेट करें
    await update.message.reply_text("कृपया मुझे वह फ़ाइल (डॉक्यूमेंट या वीडियो) भेजें जिसकी आप लिंक जनरेट करना चाहते हैं।")

# --- Batch File Link Generation ---
async def batch_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    batch_files_in_progress[user_id] = [] # बैच फ़ाइलों के लिए सूची को इनिशियलाइज़ करें
    context.user_data['current_mode'] = 'batch_file' # handle_file के लिए मोड सेट करें

    keyboard = [[InlineKeyboardButton("लिंक जनरेट करें", callback_data="generate_batch_links")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "ठीक है, मुझे एक-एक करके फ़ाइलें (डॉक्यूमेंट या वीडियो) भेजें। "
        "जब आप पूरा कर लें, तो 'लिंक जनरेट करें' बटन पर क्लिक करें।",
        reply_markup=reply_markup
    )
    return SENDING_BATCH_FILES # बातचीत की स्थिति में प्रवेश करें

async def handle_batch_file_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if user_id not in batch_files_in_progress:
        # यदि यूज़र बैच शुरू किए बिना फ़ाइल भेजता है, तो इसे एकल फ़ाइल के रूप में मानें
        return await handle_file(update, context) # एकल फ़ाइल पर फ़ॉलबैक

    file = None
    file_type = ""
    if update.message.document:
        file = update.message.document
        file_type = "document"
    elif update.message.video:
        file = update.message.video
        file_type = "video"
    else:
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
            await update.message.reply_text("फ़ॉरवर्डेड मैसेज से फ़ाइल ID प्राप्त करने में विफल।")
            return

    except Exception as e:
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

    # यूज़र की बैच सूची में टोकन स्टोर करें
    batch_files_in_progress[user_id].append(unique_token)

    keyboard = [[InlineKeyboardButton("लिंक जनरेट करें", callback_data="generate_batch_links")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "फ़ाइल प्राप्त हुई! अधिक फ़ाइलें भेजें या समाप्त करने के लिए 'लिंक जनरेट करें' पर क्लिक करें।",
        reply_markup=reply_markup
    )
    return SENDING_BATCH_FILES # उसी स्थिति में रहें


# MarkdownV2 स्पेशल कैरेक्टर को एस्केप करने के लिए सहायक फ़ंक्शन
def escape_markdown_v2(text: str) -> str:
    # केवल वे कैरेक्टर एस्केप करें जो MarkdownV2 में विशेष अर्थ रखते हैं
    # और जो आपके literal text में आ सकते हैं।
    # URL के अंदर के कैरेक्टर को एस्केप करने की आवश्यकता नहीं होती,
    # केवल डिस्प्ले टेक्स्ट में।
    escape_chars = r'_*[]()~`>#+-=|{}.!' # वे कैरेक्टर जिन्हें एस्केप करना चाहिए
    return ''.join(['\\' + char if char in escape_chars else char for char in text])


async def generate_batch_links(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer("लिंक जनरेट कर रहा हूँ...")
    user_id = update.effective_user.id
    
    if user_id not in batch_files_in_progress or not batch_files_in_progress[user_id]:
        await update.callback_query.message.reply_text("कोई फ़ाइलें नहीं मिलीं जिनके लिए लिंक जनरेट की जा सकें। कृपया पहले फ़ाइलें भेजें।")
        return ConversationHandler.END # बातचीत खत्म करें
    
    links_text = "यहाँ आपकी डाउनलोड लिंक्स हैं:\n\n"
    for token in batch_files_in_progress[user_id]:
        external_api_link = f"{EXTERNAL_API_BASE_URL}?return_to_bot={token}"
        
        # MarkdownV2 डिस्प्ले टेक्स्ट के लिए विशेष कैरेक्टर को एस्केप करें
        # टोकन के पहले 8 कैरेक्टर को एस्केप करें
        escaped_token_part = escape_markdown_v2(token[:8])
        
        # Markdown V2 लिंक फ़ॉर्मेट: [text](<url>)
        links_text += f"👉 [{escaped_token_part}...](<{external_api_link}>)\n"
    
    # मैसेज भेजें
    await update.callback_query.message.reply_text(
        links_text, 
        parse_mode='MarkdownV2', 
        disable_web_page_preview=True
    )
    
    del batch_files_in_progress[user_id] # बैच क्लियर करें
    context.user_data.pop('current_mode', None) # मोड क्लियर करें
    return ConversationHandler.END # बातचीत खत्म करें

async def cancel_batch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    if user_id in batch_files_in_progress:
        del batch_files_in_progress[user_id]
    context.user_data.pop('current_mode', None)
    await update.message.reply_text(
        "बैच फ़ाइल जनरेशन रद्द कर दिया गया।", 
        reply_markup=ReplyKeyboardRemove()
    )
    return ConversationHandler.END


# --- General File Handler (for /link command) ---
async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # यदि यूज़र बैच मोड में है, तो बैच हैंडलर को भेजें
    if context.user_data.get('current_mode') == 'batch_file':
        return await handle_batch_file_received(update, context)

    # अन्यथा, एकल फ़ाइल के रूप में प्रोसेस करें
    file = None
    file_type = ""
    if update.message.document:
        file = update.message.document
        file_type = "document"
    elif update.message.video:
        file = update.message.video
        file_type = "video"
    else:
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
            await update.message.reply_text("फ़ॉरवर्डेड मैसेज से फ़ाइल ID प्राप्त करने में विफल।")
            return

    except Exception as e:
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

    # --- सिंगल फ़ाइल: सीधा API लिंक प्रदान करें ---
    external_api_link = f"{EXTERNAL_API_BASE_URL}?return_to_bot={unique_token}"
    
    keyboard = [
        [InlineKeyboardButton("फ़ाइल डाउनलोड करें", url=external_api_link)],
        [InlineKeyboardButton("फ़ाइल कैसे डाउनलोड करें", url="https://your_help_page_link.com")] # <-- अपनी वास्तविक मदद पेज लिंक से बदलें
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "आपकी फ़ाइल सहेजी गई है! आगे बढ़ने और एक छोटा सा कार्य पूरा करने के लिए 'फ़ाइल डाउनलोड करें' पर क्लिक करें:",
        reply_markup=reply_markup
    )
    context.user_data.pop('current_mode', None) # एकल फ़ाइल के बाद मोड क्लियर करें


def main() -> None:
    # सुनिश्चित करें कि टोकन, MONGO_URI, आदि के लिए पर्यावरण चर सेट हैं
    if not TELEGRAM_BOT_TOKEN or not MONGO_URI or not PUBLIC_CHANNEL_USERNAME or not PUBLIC_CHANNEL_ID or not EXTERNAL_API_BASE_URL:
        print("त्रुटि: आवश्यक पर्यावरण चर गायब हैं। कृपया TELEGRAM_BOT_TOKEN, MONGO_URI, PUBLIC_CHANNEL_USERNAME, PUBLIC_CHANNEL_ID, EXTERNAL_API_BASE_URL सेट करें।")
        exit(1) 

    # Flask ऐप को एक अलग थ्रेड में चलाएं
    # Koyeb पर बॉट की हेल्थ चेक के लिए यह आवश्यक है
    threading.Thread(target=run_flask_app).start()

    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # --- हैंडलर ---
    # स्टार्ट कमांड हैंडलर
    application.add_handler(CommandHandler("start", start))
    
    # हेल्प और बैक बटन के लिए कॉलबैक क्वेरी हैंडलर
    application.add_handler(CallbackQueryHandler(help_command, pattern="^help_command$"))
    application.add_handler(CallbackQueryHandler(back_to_welcome, pattern="^back_to_welcome$"))

    # सिंगल लिंक जनरेशन कमांड
    application.add_handler(CommandHandler("link", link_command))

    # बैच प्रोसेसिंग के लिए कन्वर्सेशन हैंडलर
    batch_conv_handler = ConversationHandler(
        entry_points=[CommandHandler("batch", batch_start)],
        states={
            SENDING_BATCH_FILES: [
                MessageHandler(filters.ATTACHMENT, handle_batch_file_received),
                CallbackQueryHandler(generate_batch_links, pattern="^generate_batch_links$"),
                CommandHandler("cancel", cancel_batch) # यूज़र को बैच रद्द करने की अनुमति दें
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel_batch)],
    )
    application.add_handler(batch_conv_handler)

    # फ़ाइलों के लिए सामान्य मैसेज हैंडलर (/link द्वारा भी उपयोग किया जाएगा)
    # यह हैंडलर केवल तभी ट्रिगर होगा जब कन्वर्सेशन हैंडलर की स्थिति में न हो
    application.add_handler(MessageHandler(filters.ATTACHMENT, handle_file))

    print("बॉट चल रहा है...")
    # Telegram बॉट को पोलिंग मोड में चलाएं
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
