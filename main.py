
import os
import re
from fastapi import FastAPI, Request, Body, Header, HTTPException
from fastapi.responses import JSONResponse, Response
from dotenv import load_dotenv
import tos

load_dotenv()

app = FastAPI()

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
        response = JSONResponse(content={"deleted": key})
        response.headers["Access-Control-Allow-Origin"] = "*"
        return response
    except tos.exceptions.TosClientError as e:
        raise HTTPException(status_code=400, detail=f"TOS client error: {e.message}")
    except tos.exceptions.TosServerError as e:
        raise HTTPException(status_code=500, detail=f"TOS server error: {e.message}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Unknown error: {str(e)}")
