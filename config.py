import os

class Config:
    # Bot configuration
    BOT_TOKEN = os.environ.get("BOT_TOKEN", "YOUR_BOT_TOKEN")
    API_ID = int(os.environ.get("API_ID", "12345"))
    API_HASH = os.environ.get("API_HASH", "YOUR_API_HASH")
    
    # MongoDB configuration
    MONGODB_URI = os.environ.get("MONGODB_URI", "mongodb://localhost:27017")
    FILE_STORE_DB_NAME = os.environ.get("FILE_STORE_DB_NAME", "file_store_db")
    FILE_RENAME_DB_NAME = os.environ.get("FILE_RENAME_DB_NAME", "file_rename_db")
    
    # Channel configuration
    CHANNEL_ID = int(os.environ.get("CHANNEL_ID", "-1001234567890"))
    CHANNEL_URL = os.environ.get("CHANNEL_URL", "https://t.me/your_channel")
    SUPPORT_URL = os.environ.get("SUPPORT_URL", "https://t.me/your_support")
    
    # Bot settings
    MAX_FILE_SIZE = 2 * 1024 * 1024 * 1024  # 2GB
    BATCH_LIMIT = 10  # Max files in batch
    THUMBNAIL_SUPPORT = True
    
    # Webhook settings (for Koyeb)
    WEBHOOK = bool(os.environ.get("WEBHOOK", False))
    WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "")
    PORT = int(os.environ.get("PORT", 8080))
    
    # Logging
    LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")
