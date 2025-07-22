import os
import uuid
import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters, ContextTypes,
    ConversationHandler
)
from pymongo import MongoClient
from flask import Flask
import threading

# --- Configuration ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
PUBLIC_CHANNEL_USERNAME = os.getenv("PUBLIC_CHANNEL_USERNAME")
PUBLIC_CHANNEL_ID = int(os.getenv("PUBLIC_CHANNEL_ID")) 

# Your External API (Google Apps Script) base URL
EXTERNAL_API_BASE_URL = "https://script.google.com/macros/s/AKfycbwDqKLE1bZjwBcNT8wDA2SlKs821Gq7bhea8JOygiHfyPyGuATAKXWY_LtvOwlFwL9n6w/exec"

# Your Updates Channel Link
UPDATES_CHANNEL_LINK = "https://t.me/your_updates_channel_link" # <-- यहाँ अपना अपडेट चैनल लिंक डालें!

# MongoDB Configuration
MONGO_URI = os.getenv("MONGO_URI")
client = MongoClient(MONGO_URI)
db = client.file_bot
files_collection = db.files
# Temporary storage for batch files per user
# Key: user_id, Value: list of tokens
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

# --- Bot Handlers ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args

    if args:
        param = args[0]
        if param.startswith("download_"):
            original_token = param[len("download_"):]
            
            file_data = files_collection.find_one({"token": original_token})

            if file_data:
                upload_time = file_data.get("upload_time")
                if upload_time and (datetime.datetime.now() - upload_time).total_seconds() > 300: 
                    await update.message.reply_text(
                        "This download link has expired. Please upload the file again to get a new one."
                    )
                    files_collection.delete_one({"token": original_token}) 
                    return

                # Ensure the file is sent to the correct user (who initiated the download)
                if update.effective_chat.id != file_data.get("user_chat_id"):
                    await update.message.reply_text("This file is not for you, or the link is invalid.")
                    return

                telegram_file_id = file_data["telegram_file_id"]
                original_filename = file_data["original_filename"]
                try:
                    if file_data.get("file_type") == "video": 
                        await update.message.reply_video(
                            video=telegram_file_id,
                            caption=f"Here is your video: {original_filename}",
                            filename=original_filename
                        )
                    else:
                        await update.message.reply_document(
                            document=telegram_file_id,
                            caption=f"Here is your file: {original_filename}",
                            filename=original_filename
                        )
                    # Optionally delete the token after successful download
                    # files_collection.delete_one({"token": original_token})
                except Exception as e:
                    await update.message.reply_text(f"Sorry, could not send the file. An error occurred: {e}")
            else:
                await update.message.reply_text("Invalid or expired download request. Please try again or upload a new file.")
        else:
            # If a /start command is issued with a token that is NOT for direct download
            # This is your welcome message for sharing deep links or other use-cases
            await send_welcome_message(update, context) # Call welcome message
    else:
        # Standard /start command without deep link
        await send_welcome_message(update, context)

async def send_welcome_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    keyboard = [
        [InlineKeyboardButton("Updates Channel", url=UPDATES_CHANNEL_LINK)],
        [InlineKeyboardButton("Help", callback_data="help_command")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "👋 Hello! I'm your file sharing bot. I can help you generate shareable links for your files.",
        reply_markup=reply_markup
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # Check if it's from a callback query or a direct command
    if update.callback_query:
        await update.callback_query.answer() # Acknowledge the callback
        chat_id = update.callback_query.message.chat_id
        message_id = update.callback_query.message.message_id
        send_func = context.bot.send_message
    else:
        chat_id = update.message.chat_id
        message_id = update.message.message_id
        send_func = update.message.reply_text

    help_text = (
        "Here are the commands you can use:\n\n"
        "➡️ /start - Get the welcome message.\n"
        "➡️ /link - Get a shareable link for a single file.\n"
        "➡️ /batch - Generate links for multiple files at once.\n\n"
        "Send me any document or video after using /link or /batch command."
    )
    if update.callback_query:
        # Edit the original message if it's a callback, otherwise send new
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=help_text,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back", callback_data="back_to_welcome")]]) # Add a back button
        )
    else:
        await send_func(help_text)

async def back_to_welcome(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()
    await send_welcome_message(update.callback_query, context) # Re-send welcome message

# --- Single File Link Generation ---
async def link_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data['current_mode'] = 'single_file' # Set mode for handle_file
    await update.message.reply_text("Please send me the file (document or video) to generate its link.")

# --- Batch File Link Generation ---
async def batch_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    batch_files_in_progress[user_id] = [] # Initialize list for batch files
    context.user_data['current_mode'] = 'batch_file' # Set mode for handle_file

    keyboard = [[InlineKeyboardButton("Generate Links", callback_data="generate_batch_links")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "Okay, send me the files (documents or videos) one by one. "
        "When you're done, click the 'Generate Links' button.",
        reply_markup=reply_markup
    )
    return SENDING_BATCH_FILES # Enter the conversation state

async def handle_batch_file_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if user_id not in batch_files_in_progress:
        # If user sends file without starting batch, treat as single file
        return await handle_file(update, context) # Fallback to single file

    file = None
    file_type = ""
    if update.message.document:
        file = update.message.document
        file_type = "document"
    elif update.message.video:
        file = update.message.video
        file_type = "video"
    else:
        await update.message.reply_text("Please send a document or a video. Other file types are not supported for batch.")
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
            await update.message.reply_text("Failed to get file ID from forwarded message.")
            return

    except Exception as e:
        await update.message.reply_text(f"Error forwarding file to storage channel: {e}")
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

    # Store token in user's batch list
    batch_files_in_progress[user_id].append(unique_token)

    keyboard = [[InlineKeyboardButton("Generate Links", callback_data="generate_batch_links")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "File received! Send more files or click 'Generate Links' to finish.",
        reply_markup=reply_markup
    )
    return SENDING_BATCH_FILES # Stay in the same state


async def generate_batch_links(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer("Generating links...")
    user_id = update.effective_user.id
    
    if user_id not in batch_files_in_progress or not batch_files_in_progress[user_id]:
        await update.callback_query.message.reply_text("No files found to generate links for. Please send files first.")
        return ConversationHandler.END # End the conversation
    
    links_text = "Here are your download links:\n\n"
    for token in batch_files_in_progress[user_id]:
        external_api_link = f"{EXTERNAL_API_BASE_URL}?return_to_bot={token}"
        links_text += f"👉 [Download {token[:8]}...](<{external_api_link}>)\n" # Shorten token for display
    
    await update.callback_query.message.reply_text(
        links_text, 
        parse_mode='MarkdownV2', 
        disable_web_page_preview=True
    )
    
    del batch_files_in_progress[user_id] # Clear batch
    context.user_data.pop('current_mode', None) # Clear mode
    return ConversationHandler.END # End the conversation

async def cancel_batch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    if user_id in batch_files_in_progress:
        del batch_files_in_progress[user_id]
    context.user_data.pop('current_mode', None)
    await update.message.reply_text(
        "Batch file generation cancelled.", 
        reply_markup=ReplyKeyboardRemove()
    )
    return ConversationHandler.END


# --- General File Handler (for /link command) ---
async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # If the user is in batch mode, defer to batch handler
    if context.user_data.get('current_mode') == 'batch_file':
        return await handle_batch_file_received(update, context)

    # Otherwise, process as a single file
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
            await update.message.reply_text("Failed to get file ID from forwarded message.")
            return

    except Exception as e:
        await update.message.reply_text(f"Error forwarding file to storage channel: {e}")
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

    # --- Single file: Provide direct API link ---
    external_api_link = f"{EXTERNAL_API_BASE_URL}?return_to_bot={unique_token}"
    
    keyboard = [
        [InlineKeyboardButton("Download File", url=external_api_link)],
        [InlineKeyboardButton("How to Download File", url="https://your_help_page_link.com")] # Replace with your actual help page
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "Your file has been saved! Click 'Download File' to proceed and complete a short task:",
        reply_markup=reply_markup
    )
    context.user_data.pop('current_mode', None) # Clear mode after single file


def main() -> None:
    if not TELEGRAM_BOT_TOKEN or not MONGO_URI or not PUBLIC_CHANNEL_USERNAME or not PUBLIC_CHANNEL_ID:
        print("Error: Missing environment variables. Please set TELEGRAM_BOT_TOKEN, MONGO_URI, PUBLIC_CHANNEL_USERNAME, PUBLIC_CHANNEL_ID.")
        exit(1) 

    threading.Thread(target=run_flask_app).start()

    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # --- Handlers ---
    # Start command handler
    application.add_handler(CommandHandler("start", start))
    
    # Callback query handler for Help and Back button
    application.add_handler(CallbackQueryHandler(help_command, pattern="^help_command$"))
    application.add_handler(CallbackQueryHandler(back_to_welcome, pattern="^back_to_welcome$"))

    # Single link generation command
    application.add_handler(CommandHandler("link", link_command))

    # Conversation Handler for Batch processing
    batch_conv_handler = ConversationHandler(
        entry_points=[CommandHandler("batch", batch_start)],
        states={
            SENDING_BATCH_FILES: [
                MessageHandler(filters.ATTACHMENT, handle_batch_file_received),
                CallbackQueryHandler(generate_batch_links, pattern="^generate_batch_links$"),
                CommandHandler("cancel", cancel_batch) # Allow user to cancel batch
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel_batch)],
    )
    application.add_handler(batch_conv_handler)

    # General message handler for files (will be used by /link implicitly)
    # This handler will only be triggered if not in a ConversationHandler state
    application.add_handler(MessageHandler(filters.ATTACHMENT, handle_file))

    print("Bot is running...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
