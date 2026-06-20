import os
import tempfile

import uvicorn
from fastapi import FastAPI, File, HTTPException, UploadFile
from faster_whisper import WhisperModel

app = FastAPI()

MODEL_SIZE   = os.getenv("WHISPER_MODEL", "base")
DEVICE       = os.getenv("WHISPER_DEVICE", "cpu")
COMPUTE_TYPE = os.getenv("WHISPER_COMPUTE_TYPE", "int8")

print(f"Loading Whisper model '{MODEL_SIZE}' on {DEVICE} ({COMPUTE_TYPE})…")
model = WhisperModel(MODEL_SIZE, device=DEVICE, compute_type=COMPUTE_TYPE)
print("STT service ready.")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/stt")
async def stt(audio: UploadFile = File(...)):
    """
    Accept a WAV or OGG file, return JSON:
      { "text": "...", "language": "en" }
    """
    content = await audio.read()
    if len(content) < 1000:
        raise HTTPException(status_code=400, detail="Audio file too small")

    suffix = ".ogg" if (audio.filename or "").endswith(".ogg") else ".wav"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    try:
        segments, info = model.transcribe(
            tmp_path,
            beam_size=5,
            vad_filter=True,   # strip leading/trailing silence
        )
        text = " ".join(seg.text for seg in segments).strip()
        return {"text": text, "language": info.language}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        os.unlink(tmp_path)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8001)
