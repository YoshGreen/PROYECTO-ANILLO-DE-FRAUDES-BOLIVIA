import os
from pymongo import MongoClient
import redis

# Configuración de conexiones locales (100% offline y seguro para laptop)
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB_NAME = os.getenv("MONGO_DB_NAME", "yape_fraude")

REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
REDIS_DB = int(os.getenv("REDIS_DB", 0))

def get_mongo_db():
    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=2000)
    return client[MONGO_DB_NAME]

def get_redis_client():
    return redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=REDIS_DB, decode_responses=True, socket_timeout=2, protocol=2)

def check_services():
    mongo_status = False
    redis_status = False
    mongo_err = None
    redis_err = None
    
    try:
        m = MongoClient(MONGO_URI, serverSelectionTimeoutMS=1500)
        m.admin.command('ping')
        mongo_status = True
    except Exception as e:
        mongo_err = str(e)
        
    try:
        r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=REDIS_DB, socket_timeout=1.5, protocol=2)
        r.ping()
        redis_status = True
    except Exception as e:
        redis_err = str(e)
        
    return {
        "mongo": {"ok": mongo_status, "error": mongo_err, "uri": MONGO_URI, "db": MONGO_DB_NAME},
        "redis": {"ok": redis_status, "error": redis_err, "host": REDIS_HOST, "port": REDIS_PORT}
    }
