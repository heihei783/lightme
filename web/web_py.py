from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.responses import StreamingResponse
from pydantic import BaseModel
from fastapi import Request
import asyncio
import uvicorn
from utils import db_handler as db
from app.llm.llm_chain import chat_loop

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  
    allow_credentials=True,
    allow_methods=["*"],  
    allow_headers=["*"],
    expose_headers=["X-Session-Id"]  # 🌟 必须添加这行，前端 JS 才能拿到新 ID！
)

app.mount("/web", StaticFiles(directory="web/"), name="web")

@app.get("/sessions")
async def get_sessions():
    return {"status": "success", "sessions": db.get_all_chats()}

@app.get("/history/{session_id}")
async def get_chat_history(session_id: str):
    history = db.get_history_obj(session_id)
    messages = []
    for msg in history.messages:
        role = "user-msg" if msg.type == "human" else "ai-msg"
        messages.append({
            "role": role,
            "content": msg.content
        })
    return {"status": "success", "history": messages}

@app.post("/chat")
async def chat(request: Request):
    data = await request.json()
    message = data.get("message")
    sid = data.get("session_id")

    if not sid or sid == "new":
        sid = db.create_new_chat(message)

    async def response_stream():
        full_reply = ""
        for chunk in chat_loop(sid, message):
            full_reply += chunk
            yield chunk
        
        db.save_message_and_update(sid, "user-msg", message)
        db.save_message_and_update(sid, "ai-msg", full_reply)

    # 🌟 在返回体里带上 Header，把新 ID 扔给前端
    return StreamingResponse(
        response_stream(), 
        media_type="text/plain", 
        headers={"X-Session-Id": sid}
    )

@app.delete("/session/{session_id}")
async def delete_session_api(session_id: str):
    db.delete_chat_list(session_id)
    db.clear_session(session_id)
    return {"status": "success"}



if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)