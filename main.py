
import os
import re
from datetime import datetime, timezone
from fastapi import FastAPI, Request, Body, Header, HTTPException
from fastapi.responses import JSONResponse, Response
from dotenv import load_dotenv
import tos
from pymongo import MongoClient, ASCENDING, DESCENDING, IndexModel

load_dotenv()

app = FastAPI()

# MongoDB setup
mongodb_uri = os.getenv("MONGODB_URI")
mongodb_database = os.getenv("MONGODB_DATABASE")

mongodb_client = None
db = None
files_collection = None

if mongodb_uri and mongodb_database:
    try:
        mongodb_client = MongoClient(mongodb_uri)
        db = mongodb_client[mongodb_database]
        files_collection = db.files
        
        # Create indexes for MongoDB 4.4+
        # Note: In MongoDB 4.4+, the 'background' option is deprecated and ignored
        # Index builds use a hybrid approach by default
        indexes = [
            IndexModel([("key", ASCENDING)]),
            IndexModel([("created_at", DESCENDING)]),
            IndexModel([("operation", ASCENDING)]),
            IndexModel([("key", ASCENDING), ("operation", ASCENDING)])
        ]
        files_collection.create_indexes(indexes)
    except Exception as e:
        print(f"MongoDB connection warning: {e}")
        # Continue without MongoDB if connection fails
        mongodb_client = None

client = tos.TosClientV2(
    ak=os.getenv("BYTEPLUS_ACCESS_KEY"),
    sk=os.getenv("BYTEPLUS_SECRET_KEY"),
    endpoint=os.getenv("BYTEPLUS_ENDPOINT"),
    region=os.getenv("BYTEPLUS_REGION")
)

BUCKET = os.getenv("BYTEPLUS_BUCKET")
EXPECTED_TOKEN = os.getenv("BEARER_TOKEN")  # ví dụ: "my-secret-token"

IMAGE_SIZES = {
    "pico": [16, 16],
    "icon": [32, 32],
    "thumb": [50, 50],
    "small": [100, 100],
    "compact": [160, 160],
    "medium": [240, 240],
    "large": [480, 480],
    "grande": [600, 600],
    "1024x1024": [1024, 1024],
    "2048x2048": [2048, 2048],
    "master": [2048, 2048],
    "fbsbanner": [808, 200],
    "sqcrop": [],
    "sqbox": []
}

SIZE_REGEX = re.compile(
    r"^(?P<path>[\.a-zA-Z0-9/_-]*?)(?P<name>[^/_]+)_(?P<size>pico|icon|thumb|small|compact|medium|large|grande|1024x1024|2048x2048|master|fbsbanner|sqcrop|sqbox)\.(?P<ext>jpg|jpeg|png|gif)$"
)

@app.post("/{path:path}")
async def upload_binary(
    path: str,
    request: Request,
    authorization: str = Header(None),
    body: bytes = Body(...)
):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")

    token = authorization.removeprefix("Bearer ").strip()
    if token != EXPECTED_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid token")

    key = path.strip("/")
    try:
        client.put_object(bucket=BUCKET, key=key, content=body)
        scheme = request.url.scheme
        host = request.headers.get("host", "localhost")
        url = f"{scheme}://{host}/{key}"
        
        # Log to MongoDB if available
        if files_collection is not None:
            try:
                files_collection.insert_one({
                    "key": key,
                    "operation": "upload",
                    "size": len(body),
                    "created_at": datetime.now(timezone.utc),
                    "url": url
                })
            except Exception as mongo_error:
                print(f"MongoDB logging error: {mongo_error}")
        
        response = JSONResponse(content={"url": url})
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Cache-Control"] = "public, max-age=31536000"
        return response
    except tos.exceptions.TosClientError as e:
        return {"error": "client", "message": e.message}
    except tos.exceptions.TosServerError as e:
        return {"error": "server", "message": e.message}
    except Exception as e:
        return {"error": "unknown", "message": str(e)}

@app.get("/{path:path}")
def get_image(path: str, request: Request):
    key = path.strip("/")
    m = SIZE_REGEX.match(key)
    tos_key = key
    process = None

    if m:
        # original key
        base_key = f"{m.group('path')}{m.group('name')}.{m.group('ext')}"
        size = m.group("size")
        if size in IMAGE_SIZES and IMAGE_SIZES[size]:
            width, height = IMAGE_SIZES[size]
            process = f"image/resize,m_lfit,w_{width},h_{height}"
        else:
            process = "image/resize,m_lfit,w_2048,h_2048"
        tos_key = base_key

    try:
        obj = client.get_object(bucket=BUCKET, key=tos_key)
        content = obj.read()
        
        # Log to MongoDB if available
        if files_collection is not None:
            try:
                files_collection.insert_one({
                    "key": tos_key,
                    "requested_key": key,
                    "operation": "download",
                    "size": len(content),
                    "created_at": datetime.now(timezone.utc),
                    "content_type": obj.content_type
                })
            except Exception as mongo_error:
                print(f"MongoDB logging error: {mongo_error}")
        
        response = Response(content=content, media_type=obj.content_type)
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Cache-Control"] = "public, max-age=31536000"
        response.headers["x-tos-process"] = "true"
        return response
    except Exception as e:
        raise HTTPException(status_code=404, detail=str(e))

@app.delete("/{path:path}")
async def delete_object(
    path: str,
    request: Request,
    authorization: str = Header(None)
):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")

    token = authorization.removeprefix("Bearer ").strip()
    if token != EXPECTED_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid token")

    key = path.strip("/")
    try:
        client.delete_object(bucket=BUCKET, key=key)
        
        # Log to MongoDB if available
        if files_collection is not None:
            try:
                files_collection.insert_one({
                    "key": key,
                    "operation": "delete",
                    "created_at": datetime.now(timezone.utc)
                })
            except Exception as mongo_error:
                print(f"MongoDB logging error: {mongo_error}")
        
        response = JSONResponse(content={"deleted": key})
        response.headers["Access-Control-Allow-Origin"] = "*"
        return response
    except tos.exceptions.TosClientError as e:
        raise HTTPException(status_code=400, detail=f"TOS client error: {e.message}")
    except tos.exceptions.TosServerError as e:
        raise HTTPException(status_code=500, detail=f"TOS server error: {e.message}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Unknown error: {str(e)}")
