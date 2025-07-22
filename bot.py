import os
import uuid
import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from pymongo import MongoClient
from flask import Flask # Flask को इम्पोर्ट करें
import threading # थ्रेडिंग को इम्पोर्ट करें

# --- Configuration ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
# Make sure this is your PUBLIC channel's username (without @)
PUBLIC_CHANNEL_USERNAME = os.getenv("PUBLIC_CHANNEL_USERNAME")
# PUBLIC_CHANNEL_ID को हमेशा int में बदलें
PUBLIC_CHANNEL_ID = int(os.getenv("PUBLIC_CHANNEL_ID")) 

# Your External API (Google Apps Script) base URL
EXTERNAL_API_BASE_URL = "https://script.google.com/macros/s/AKfycbwDqKLE1bZjwBcNT8wDA2SlKs821Gq7bhea8JOygiHfyPyGuATAKXWY_LtvOwlFwL9n6w/exec"

# MongoDB Configuration
MONGO_URI = os.getenv("MONGO_URI")
client = MongoClient(MONGO_URI)
db = client.file_bot # Your database name
files_collection = db.files # Your collection name

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
    args = context.args # Get parameters from the /start command

    if args:
        param = args[0]
        if param.startswith("download_"):
            # This is the callback from the external API
            original_token = param[len("download_"):]
            
            # Retrieve file info from MongoDB
            file_data = files_collection.find_one({"token": original_token})

            if file_data:
                # Check for 5-minute validity
                # Assuming 'upload_time' is stored as datetime object in MongoDB
                upload_time = file_data.get("upload_time")
                # 300 seconds = 5 minutes
                if upload_time and (datetime.datetime.now() - upload_time).total_seconds() > 300: 
                    await update.message.reply_text(
                        "This link has expired. Please upload the file again to get a new download link."
                    )
                    # Clean up expired token from DB
                    files_collection.delete_one({"token": original_token}) 
                    return

                # Send the actual file
                telegram_file_id = file_data["telegram_file_id"]
                original_filename = file_data["original_filename"]
                try:
                    # Determine file type to send accordingly
                    if file_data.get("file_type") == "video": 
                        await update.message.reply_video(
                            video=telegram_file_id,
                            caption=f"Here is your video: {original_filename}",
                            filename=original_filename
                        )
                    else: # Default to document for other types
                        await update.message.reply_document(
                            document=telegram_file_id,
                            caption=f"Here is your file: {original_filename}",
                            filename=original_filename
                        )
                    # Optionally delete the token after successful download to prevent re-downloads
                    # files_collection.delete_one({"token": original_token})
                except Exception as e:
                    await update.message.reply_text(f"Sorry, could not send the file. An error occurred: {e}")
            else:
                await update.message.reply_text("Invalid or expired download link. Please try again or upload a new file.")
        else:
            # This is the initial deep link (e.g., from a user sharing the link)
            unique_token = param
            
            # Build the external API link with return_to_bot parameter
            external_api_link = f"{EXTERNAL_API_BASE_URL}?return_to_bot={unique_token}"
            
            keyboard = [
                [InlineKeyboardButton("Download File", url=external_api_link)],
                [InlineKeyboardButton("How to Download File", url="https://your_help_page_link.com")] # Replace with your actual help page
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)

            await update.message.reply_text(
                "To download your file, please complete a short task:",
                reply_markup=reply_markup
            )
    else:
        # Standard /start command without deep link
        await update.message.reply_text("Hello! Send me a video or document to get a permanent link.")


async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    file = None
    file_type = ""
    if update.message.document:
        file = update.message.document
        file_type = "document"
    elif update.message.video:
        file = update.message.video
        file_type = "video"
    else:
        await update.message.reply_text("Please send a document or a video.")
        return

    original_filename = file.file_name if file.file_name else f"unnamed_{file_type}"
    user_chat_id = update.message.chat_id

    # Forward the file to your public channel
    try:
        sent_message = await context.bot.forward_message(
            chat_id=PUBLIC_CHANNEL_ID,
            from_chat_id=user_chat_id,
            message_id=update.message.message_id
        )
        # Get the actual file_id from the forwarded message in the channel
        permanent_telegram_file_id = None
        if sent_message.document:
            permanent_telegram_file_id = sent_message.document.file_id
        elif sent_message.video:
            permanent_telegram_file_id = sent_message.video.file_id
        
        if not permanent_telegram_file_id:
            await update.message.reply_text("Failed to get file ID from forwarded message.")
            return

    except Exception as e:
        await update.message.reply_text(f"Error forwarding file to storage channel: {e}")
        return

    # Generate a unique token
    unique_token = str(uuid.uuid4())

    # Store info in MongoDB
    file_info = {
        "token": unique_token,
        "telegram_file_id": permanent_telegram_file_id,
        "original_filename": original_filename,
        "user_chat_id": user_chat_id, # Store user's chat_id to send file later
        "upload_time": datetime.datetime.now(), # Store current time for validity check
        "file_type": file_type # Store file type for sending back correctly
    }
    files_collection.insert_one(file_info)

    # Construct the permanent deep link for the user
    permanent_deep_link = f"https://t.me/{PUBLIC_CHANNEL_USERNAME}?start={unique_token}"

    await update.message.reply_text(
        f"Your file has been saved! Here's your permanent link:\n\n👉 {permanent_deep_link}\n\n"
        "Click the link to get your file after completing a short task."
    )

def main() -> None:
    # Ensure environment variables are set for TOKEN, MONGO_URI, etc.
    if not TELEGRAM_BOT_TOKEN or not MONGO_URI or not PUBLIC_CHANNEL_USERNAME or not PUBLIC_CHANNEL_ID:
        print("Error: Missing environment variables. Please set TELEGRAM_BOT_TOKEN, MONGO_URI, PUBLIC_CHANNEL_USERNAME, PUBLIC_CHANNEL_ID.")
        # Exit if critical environment variables are missing
        exit(1) 

    # Flask ऐप को एक अलग थ्रेड में चलाएं
    # Koyeb पर बॉट की हेल्थ चेक के लिए यह आवश्यक है
    threading.Thread(target=run_flask_app).start()

    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Register handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.Document.ALL | filters.Video.ALL, handle_file))

    print("Bot is running...")
    # Telegram बॉट को पोलिंग मोड में चलाएं
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
