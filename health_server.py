from fastapi import FastAPI
import uvicorn
import asyncio
import logging
from motor.motor_asyncio import AsyncIOMotorClient
from config import Config

app = FastAPI()
logger = logging.getLogger(__name__)

# MongoDB connection
mongo_client = None

@app.on_event("startup")
async def startup_event():
    global mongo_client
    try:
        mongo_client = AsyncIOMotorClient(Config.MONGODB_URI)
        # Test connection
        await mongo_client.admin.command('ping')
        logger.info("MongoDB connected successfully")
    except Exception as e:
        logger.error(f"MongoDB connection failed: {e}")

@app.on_event("shutdown")
async def shutdown_event():
    if mongo_client:
        mongo_client.close()

@app.get("/health")
async def health_check():
    """Health check endpoint for Koyeb"""
    try:
        # Check MongoDB connection
        if mongo_client:
            await mongo_client.admin.command('ping')
            return {
                "status": "healthy",
                "mongodb": "connected",
                "timestamp": asyncio.get_event_loop().time()
            }
        else:
            return {
                "status": "unhealthy",
                "mongodb": "disconnected",
                "timestamp": asyncio.get_event_loop().time()
            }
    except Exception as e:
        return {
            "status": "unhealthy",
            "error": str(e),
            "timestamp": asyncio.get_event_loop().time()
        }

@app.get("/")
async def root():
    return {"message": "Bot health check server running"}

if __name__ == "__main__":
    uvicorn.run(
        "health_server:app",
        host="0.0.0.0",
        port=Config.PORT,
        log_level="info"
    )
