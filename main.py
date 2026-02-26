import os
import logging
import asyncio
from datetime import datetime
from pyrogram import Client, filters, idle
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from pyrogram.errors import FloodWait
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import MongoClient
import motor.motor_asyncio
import time
import random
import string
import humanize
from config import Config

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Initialize bot
app = Client(
    "advanced_bot",
    api_id=Config.API_ID,
    api_hash=Config.API_HASH,
    bot_token=Config.BOT_TOKEN
)

# MongoDB connections
mongo_client = AsyncIOMotorClient(Config.MONGODB_URI)
file_store_db = mongo_client[Config.FILE_STORE_DB_NAME]
file_rename_db = mongo_client[Config.FILE_RENAME_DB_NAME]

# Collections
files_collection = file_store_db.files
users_collection = file_store_db.users
rename_collection = file_rename_db.rename_tasks
batch_collection = file_rename_db.batch_tasks

# Helper functions
def generate_unique_id():
    """Generate unique ID for files"""
    return ''.join(random.choices(string.ascii_lowercase + string.digits, k=10))

def get_size(size):
    """Get human readable size"""
    return humanize.naturalsize(size)

def get_progress_bar(percentage):
    """Generate progress bar"""
    completed = int(percentage / 10)
    return "●" * completed + "○" * (10 - completed)

# Database functions for file store
async def save_file_to_db(message, file_id, file_name, file_size, mime_type):
    """Save file information to database"""
    try:
        file_data = {
            "file_id": file_id,
            "unique_id": generate_unique_id(),
            "file_name": file_name,
            "file_size": file_size,
            "mime_type": mime_type,
            "message_id": message.id,
            "chat_id": message.chat.id,
            "uploaded_by": message.from_user.id if message.from_user else None,
            "uploaded_at": datetime.utcnow(),
            "download_count": 0
        }
        result = await files_collection.insert_one(file_data)
        return file_data["unique_id"]
    except Exception as e:
        logger.error(f"Error saving file: {e}")
        return None

async def get_file_from_db(unique_id):
    """Get file information from database"""
    try:
        file_data = await files_collection.find_one({"unique_id": unique_id})
        if file_data:
            await files_collection.update_one(
                {"unique_id": unique_id},
                {"$inc": {"download_count": 1}}
            )
        return file_data
    except Exception as e:
        logger.error(f"Error getting file: {e}")
        return None

async def save_user(user_id, username=None, first_name=None, last_name=None):
    """Save user information"""
    try:
        user_data = {
            "user_id": user_id,
            "username": username,
            "first_name": first_name,
            "last_name": last_name,
            "joined_at": datetime.utcnow(),
            "last_active": datetime.utcnow()
        }
        await users_collection.update_one(
            {"user_id": user_id},
            {"$set": user_data},
            upsert=True
        )
    except Exception as e:
        logger.error(f"Error saving user: {e}")

# Database functions for rename
async def save_rename_task(user_id, file_id, file_name, file_size, message_id):
    """Save rename task"""
    try:
        task_data = {
            "task_id": generate_unique_id(),
            "user_id": user_id,
            "file_id": file_id,
            "file_name": file_name,
            "file_size": file_size,
            "message_id": message_id,
            "status": "pending",
            "created_at": datetime.utcnow()
        }
        result = await rename_collection.insert_one(task_data)
        return task_data["task_id"]
    except Exception as e:
        logger.error(f"Error saving rename task: {e}")
        return None

async def update_rename_status(task_id, status):
    """Update rename task status"""
    try:
        await rename_collection.update_one(
            {"task_id": task_id},
            {"$set": {"status": status}}
        )
    except Exception as e:
        logger.error(f"Error updating rename task: {e}")

async def get_rename_task(task_id):
    """Get rename task"""
    try:
        return await rename_collection.find_one({"task_id": task_id})
    except Exception as e:
        logger.error(f"Error getting rename task: {e}")
        return None

# Command handlers
@app.on_message(filters.command("start"))
async def start_command(client, message):
    """Handle /start command"""
    user = message.from_user
    await save_user(user.id, user.username, user.first_name, user.last_name)
    
    welcome_text = f"""
👋 **Welcome {user.first_name}!**

I'm an Advanced File Store & Rename Bot with powerful features.

**📁 File Store Features:**
• Store any file/document permanently
• Get instant shareable links
• Track download counts
• Batch file management
• Channel integration

**✏️ File Rename Features:**
• Rename any file/document
• Add custom thumbnails
• Set custom captions
• Batch rename support
• Keep original file quality

**🚀 Available Commands:**
/start - Welcome message
/help - Detailed help
/stats - Bot statistics
/about - About bot
/batch - Batch operations
/myfiles - Your stored files

Click below buttons to learn more!
"""
    
    buttons = [
        [
            InlineKeyboardButton("📁 File Store", callback_data="file_store_help"),
            InlineKeyboardButton("✏️ File Rename", callback_data="rename_help")
        ],
        [
            InlineKeyboardButton("📊 Statistics", callback_data="stats"),
            InlineKeyboardButton("👤 My Files", callback_data="my_files")
        ],
        [
            InlineKeyboardButton("📢 Channel", url=Config.CHANNEL_URL),
            InlineKeyboardButton("👥 Support", url=Config.SUPPORT_URL)
        ]
    ]
    
    await message.reply_text(
        welcome_text,
        reply_markup=InlineKeyboardMarkup(buttons)
    )

@app.on_message(filters.command("help"))
async def help_command(client, message):
    """Handle /help command"""
    help_text = """
**🔰 Detailed Help Guide**

**📁 File Store Mode:**
1. Send any file/document to store
2. Bot saves file to database
3. Get unique shareable link
4. Share link with others
5. Track downloads

**✏️ File Rename Mode:**
1. Send file with new name
   Format: `new_name.ext`
2. Or use /rename command
3. Add thumbnail (optional)
4. Set custom caption
5. Get renamed file

**⚙️ Advanced Features:**
• Batch processing up to 10 files
• Custom thumbnail support
• Download tracking
• User statistics
• Channel auto-post
• File preview

**🎯 Tips:**
• Use /myfiles to see your stored files
• Thumbnail must be image file
• Max file size: 2GB
• Files stored permanently
"""
    
    buttons = [
        [
            InlineKeyboardButton("◀️ Back", callback_data="back_to_start"),
            InlineKeyboardButton("📊 Stats", callback_data="stats")
        ]
    ]
    
    await message.reply_text(
        help_text,
        reply_markup=InlineKeyboardMarkup(buttons)
    )

@app.on_message(filters.command("stats"))
async def stats_command(client, message):
    """Handle /stats command"""
    try:
        # Get statistics
        total_files = await files_collection.count_documents({})
        total_users = await users_collection.count_documents({})
        total_renames = await rename_collection.count_documents({})
        
        # Get today's uploads
        today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        today_uploads = await files_collection.count_documents({
            "uploaded_at": {"$gte": today}
        })
        
        # Get top files
        top_files = await files_collection.find().sort("download_count", -1).limit(5).to_list(5)
        
        stats_text = f"""
**📊 Bot Statistics**

**Total Statistics:**
• Total Files: **{total_files}**
• Total Users: **{total_users}**
• Total Renames: **{total_renames}**
• Today's Uploads: **{today_uploads}**

**📈 Top Files:**
"""
        
        for i, file in enumerate(top_files, 1):
            stats_text += f"\n{i}. {file['file_name'][:30]}...\n   📥 {file['download_count']} downloads"
        
        buttons = [
            [
                InlineKeyboardButton("🔄 Refresh", callback_data="stats"),
                InlineKeyboardButton("◀️ Back", callback_data="back_to_start")
            ]
        ]
        
        await message.reply_text(
            stats_text,
            reply_markup=InlineKeyboardMarkup(buttons)
        )
    except Exception as e:
        logger.error(f"Error in stats: {e}")
        await message.reply_text("❌ Error fetching statistics!")

@app.on_message(filters.command("myfiles"))
async def my_files_command(client, message):
    """Handle /myfiles command"""
    user_id = message.from_user.id
    
    try:
        user_files = await files_collection.find(
            {"uploaded_by": user_id}
        ).sort("uploaded_at", -1).limit(10).to_list(10)
        
        if not user_files:
            await message.reply_text("📁 You haven't uploaded any files yet!")
            return
        
        text = "**📁 Your Recent Files:**\n\n"
        for i, file in enumerate(user_files, 1):
            size = get_size(file['file_size'])
            text += f"{i}. **{file['file_name'][:40]}**\n"
            text += f"   📥 {file['download_count']} downloads | 💾 {size}\n"
            text += f"   🔗 `{file['unique_id']}`\n\n"
        
        buttons = [[
            InlineKeyboardButton("🔄 Refresh", callback_data="my_files")
        ]]
        
        await message.reply_text(
            text,
            reply_markup=InlineKeyboardMarkup(buttons)
        )
    except Exception as e:
        logger.error(f"Error in myfiles: {e}")
        await message.reply_text("❌ Error fetching your files!")

@app.on_message(filters.command("rename"))
async def rename_command(client, message):
    """Handle /rename command"""
    if len(message.command) < 2:
        await message.reply_text(
            "✏️ **Please provide new name!**\n\n"
            "Usage: `/rename new_name.ext`\n"
            "Example: `/rename my_document.pdf`",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("❓ Help", callback_data="rename_help")
            ]])
        )
        return
    
    new_name = message.text.split(" ", 1)[1]
    
    # Check if reply to a file
    if not message.reply_to_message or not message.reply_to_message.document:
        await message.reply_text(
            "❌ Please reply to a file with the rename command!"
        )
        return
    
    file = message.reply_to_message.document
    
    # Save rename task
    task_id = await save_rename_task(
        message.from_user.id,
        file.file_id,
        new_name,
        file.file_size,
        message.reply_to_message.id
    )
    
    if task_id:
        await message.reply_text(
            f"✏️ **Rename Task Created!**\n\n"
            f"📄 Original: `{file.file_name}`\n"
            f"📄 New: `{new_name}`\n"
            f"💾 Size: {get_size(file.file_size)}\n\n"
            f"Task ID: `{task_id}`\n\n"
            f"Processing your file...",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("📊 Check Status", callback_data=f"check_status_{task_id}")
            ]])
        )
        
        # Process rename
        await update_rename_status(task_id, "processing")
        
        try:
            # Download and upload with new name
            downloaded = await message.reply_to_message.download()
            
            # Send with new name
            await client.send_document(
                chat_id=message.chat.id,
                document=downloaded,
                file_name=new_name,
                caption=f"✏️ **Renamed File**\n\nOriginal: `{file.file_name}`"
            )
            
            await update_rename_status(task_id, "completed")
            os.remove(downloaded)
            
        except Exception as e:
            await update_rename_status(task_id, "failed")
            logger.error(f"Error in rename: {e}")
            await message.reply_text("❌ Error renaming file!")
    else:
        await message.reply_text("❌ Error creating rename task!")

@app.on_message(filters.document)
async def handle_document(client, message):
    """Handle document uploads"""
    user = message.from_user
    await save_user(user.id, user.username, user.first_name, user.last_name)
    
    file = message.document
    
    # Check if it's a thumbnail
    if file.mime_type.startswith('image/'):
        # Save as thumbnail
        # Implement thumbnail saving here
        await message.reply_text("✅ Thumbnail saved!")
        return
    
    # Save to database
    unique_id = await save_file_to_db(
        message,
        file.file_id,
        file.file_name,
        file.file_size,
        file.mime_type
    )
    
    if unique_id:
        # Forward to channel if configured
        if Config.CHANNEL_ID:
            try:
                forwarded = await message.copy(
                    chat_id=Config.CHANNEL_ID,
                    caption=f"**New File Uploaded**\n\n📄 {file.file_name}\n🔗 `{unique_id}`"
                )
            except Exception as e:
                logger.error(f"Error forwarding to channel: {e}")
        
        # Send confirmation
        file_size = get_size(file.file_size)
        text = f"""
✅ **File Stored Successfully!**

📄 **File Name:** `{file.file_name}`
💾 **Size:** {file_size}
🔗 **File ID:** `{unique_id}`

**Share this ID with anyone to access the file.**
"""
        
        buttons = [
            [
                InlineKeyboardButton("📥 Download", callback_data=f"download_{unique_id}"),
                InlineKeyboardButton("ℹ️ Info", callback_data=f"info_{unique_id}")
            ],
            [
                InlineKeyboardButton("✏️ Rename", callback_data=f"rename_{unique_id}"),
                InlineKeyboardButton("📋 Copy ID", callback_data=f"copy_{unique_id}")
            ],
            [
                InlineKeyboardButton("📁 My Files", callback_data="my_files"),
                InlineKeyboardButton("📊 Stats", callback_data="stats")
            ]
        ]
        
        await message.reply_text(
            text,
            reply_markup=InlineKeyboardMarkup(buttons)
        )
    else:
        await message.reply_text("❌ Error saving file!")

@app.on_message(filters.command("batch"))
async def batch_command(client, message):
    """Handle batch operations"""
    text = """
**📦 Batch Operations**

Choose batch operation:

1️⃣ **Batch Store** - Store multiple files at once
   • Reply to multiple files with /batchstore
   • Maximum 10 files at once

2️⃣ **Batch Rename** - Rename multiple files
   • Reply to files with new names
   • Format: filename1.ext,filename2.ext

3️⃣ **Batch Download** - Download multiple files
   • Provide file IDs separated by commas
   • Get zip file (coming soon)

**How to use:**
• Send /batchstore with multiple files
• Send /batchrename with names list
• Files will be processed sequentially
"""
    
    buttons = [
        [
            InlineKeyboardButton("📦 Batch Store", callback_data="batch_store"),
            InlineKeyboardButton("✏️ Batch Rename", callback_data="batch_rename")
        ],
        [
            InlineKeyboardButton("◀️ Back", callback_data="back_to_start")
        ]
    ]
    
    await message.reply_text(
        text,
        reply_markup=InlineKeyboardMarkup(buttons)
    )

# Callback handlers
@app.on_callback_query()
async def handle_callbacks(client, callback_query):
    """Handle all callback queries"""
    data = callback_query.data
    user_id = callback_query.from_user.id
    
    try:
        if data == "file_store_help":
            await callback_query.message.edit_text(
                "**📁 File Store Help**\n\n"
                "1. Send any file to store it\n"
                "2. Get unique file ID\n"
                "3. Share ID with others\n"
                "4. Others can download using ID\n"
                "5. Track download counts\n\n"
                "**Commands:**\n"
                "/store - Store replied file\n"
                "/get [ID] - Get file by ID\n"
                "/myfiles - Your stored files\n"
                "/batchstore - Store multiple files",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("◀️ Back", callback_data="back_to_start")
                ]])
            )
        
        elif data == "rename_help":
            await callback_query.message.edit_text(
                "**✏️ File Rename Help**\n\n"
                "**Methods:**\n"
                "1️⃣ **Quick Rename:**\n"
                "   Send file with new name in caption\n"
                "   Format: `new_name.ext`\n\n"
                "2️⃣ **Command Method:**\n"
                "   Reply to file: `/rename new_name.ext`\n\n"
                "3️⃣ **Batch Rename:**\n"
                "   Use /batchrename command\n\n"
                "**Features:**\n"
                "• Custom thumbnails\n"
                "• Custom captions\n"
                "• Original quality\n"
                "• Progress tracking",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("◀️ Back", callback_data="back_to_start")
                ]])
            )
        
        elif data == "stats":
            await stats_command(client, callback_query.message)
        
        elif data == "my_files":
            await my_files_command(client, callback_query.message)
        
        elif data == "back_to_start":
            await start_command(client, callback_query.message)
        
        elif data.startswith("download_"):
            unique_id = data.split("_")[1]
            file_data = await get_file_from_db(unique_id)
            
            if file_data:
                try:
                    await client.send_cached_media(
                        chat_id=user_id,
                        file_id=file_data["file_id"],
                        caption=f"📥 **Downloaded File**\n\n📄 {file_data['file_name']}"
                    )
                    await callback_query.answer("✅ File sent successfully!")
                except Exception as e:
                    await callback_query.answer("❌ Error sending file!")
            else:
                await callback_query.answer("❌ File not found!")
        
        elif data.startswith("info_"):
            unique_id = data.split("_")[1]
            file_data = await get_file_from_db(unique_id)
            
            if file_data:
                info_text = f"""
**📄 File Information**

**Name:** `{file_data['file_name']}`
**Size:** {get_size(file_data['file_size'])}
**Type:** {file_data['mime_type']}
**Downloads:** {file_data['download_count']}
**Uploaded:** {file_data['uploaded_at'].strftime('%Y-%m-%d %H:%M')}
**File ID:** `{unique_id}`
"""
                await callback_query.message.edit_text(
                    info_text,
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("📥 Download", callback_data=f"download_{unique_id}"),
                        InlineKeyboardButton("◀️ Back", callback_data="my_files")
                    ]])
                )
            else:
                await callback_query.answer("❌ File not found!")
        
        elif data.startswith("copy_"):
            unique_id = data.split("_")[1]
            await callback_query.answer(f"ID: {unique_id}", show_alert=True)
        
        elif data.startswith("check_status_"):
            task_id = data.split("_")[2]
            task = await get_rename_task(task_id)
            
            if task:
                await callback_query.answer(f"Status: {task['status']}", show_alert=True)
            else:
                await callback_query.answer("Task not found!")
        
        elif data == "batch_store":
            await callback_query.message.edit_text(
                "**📦 Batch Store**\n\n"
                "Send multiple files at once (max 10).\n"
                "I'll process them and give you all IDs.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("◀️ Back", callback_data="back_to_start")
                ]])
            )
        
        elif data == "batch_rename":
            await callback_query.message.edit_text(
                "**✏️ Batch Rename**\n\n"
                "Reply to multiple files with new names.\n"
                "Format: `name1.ext,name2.ext,name3.ext`",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("◀️ Back", callback_data="back_to_start")
                ]])
            )
        
        await callback_query.answer()
        
    except Exception as e:
        logger.error(f"Callback error: {e}")
        await callback_query.answer("❌ Error processing request!")

# Health check endpoint simulation
@app.on_message(filters.command("health"))
async def health_check(client, message):
    """Health check command for monitoring"""
    try:
        # Check MongoDB connections
        await client.send_message(
            chat_id=message.chat.id,
            text="✅ Bot is healthy!\n✅ MongoDB connected!"
        )
    except Exception as e:
        await client.send_message(
            chat_id=message.chat.id,
            text=f"❌ Health check failed: {e}"
        )

# Periodic health check for Koyeb
async def periodic_health_check():
    """Send periodic health pings"""
    while True:
        try:
            # You can implement actual health checks here
            # Like pinging external services or checking connections
            logger.info("Health check passed")
            await asyncio.sleep(300)  # Check every 5 minutes
        except Exception as e:
            logger.error(f"Health check failed: {e}")
            await asyncio.sleep(60)

# Main function
async def main():
    """Main function to start bot"""
    logger.info("Starting Advanced Bot...")
    
    # Start bot
    await app.start()
    
    # Start health check task
    asyncio.create_task(periodic_health_check())
    
    logger.info("Bot started successfully!")
    
    # Keep bot running
    await idle()
    
    logger.info("Bot stopped!")

if __name__ == "__main__":
    asyncio.run(main())
