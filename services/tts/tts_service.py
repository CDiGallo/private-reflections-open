import io
import os

import numpy as np
import soundfile as sf
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from kokoro import KPipeline
from pydantic import BaseModel

app = FastAPI()

LANG_CODE   = os.getenv("TTS_LANG", "a")   # a = American, b = British
SAMPLE_RATE = 24000

print(f"Loading Kokoro TTS pipeline (lang={LANG_CODE})…")
pipeline = KPipeline(lang_code=LANG_CODE)
print("TTS service ready.")


class TTSRequest(BaseModel):
    text: str
    voice: str  = "af_sarah"
    speed: float = 1.0


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/tts")
def tts(req: TTSRequest):
    chunks = []
    for _, _, audio in pipeline(req.text, voice=req.voice, speed=req.speed):
        chunks.append(audio)

    if not chunks:
        raise HTTPException(status_code=400, detail="No audio generated")

    full = np.concatenate(chunks)
    buf  = io.BytesIO()
    sf.write(buf, full, SAMPLE_RATE, format="WAV")
    buf.seek(0)
    return StreamingResponse(buf, media_type="audio/wav")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
