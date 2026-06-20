import asyncio
import io
import os
import re
import subprocess
from datetime import datetime
from pathlib import Path

import httpx
from openai import AsyncOpenAI
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# ── Config ──────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN   = os.environ["TELEGRAM_BOT_TOKEN"]
ALLOWED_USER_ID  = int(os.getenv("TELEGRAM_ALLOWED_USER_ID", "0"))
LLM_PROVIDER     = os.getenv("LLM_PROVIDER", "ollama")
LLM_MODEL        = os.getenv("LLM_MODEL", "llama3.2")
OLLAMA_HOST      = os.getenv("OLLAMA_HOST", "http://ollama:11434")
VLLM_URL         = os.getenv("VLLM_URL", "http://vllm:8000/v1")
TTS_URL          = os.getenv("TTS_URL", "http://tts:8000/tts")
STT_URL          = os.getenv("STT_URL", "http://stt:8001/stt")
TTS_VOICE        = os.getenv("TTS_VOICE", "af_sarah")
VOICE_REPLIES    = os.getenv("TELEGRAM_VOICE_REPLIES", "true").lower() == "true"
SAVE_KEYWORD     = os.getenv("SAVE_KEYWORD", "wrap up").strip().strip("\"'").strip().lower()
MAX_HISTORY      = int(os.getenv("MAX_HISTORY", "20"))
MEMORY_SESSIONS  = int(os.getenv("MEMORY_SESSIONS", "5"))

SESSIONS_DIR = Path("/audio/sessions")
SESSIONS_DIR.mkdir(parents=True, exist_ok=True)

# ── System prompt — loaded from shared file ─────────────────────────────────
_prompt_path = Path("/app/system_prompt.txt")
if _prompt_path.exists() and _prompt_path.stat().st_size > 0:
    SYSTEM_PROMPT = _prompt_path.read_text(encoding="utf-8").strip()
    print(f"  ✓ System prompt loaded ({len(SYSTEM_PROMPT)} chars)")
else:
    SYSTEM_PROMPT = ""
    print("  ⚠  system_prompt.txt not found — running without system prompt")

SUMMARY_PROMPT = (
    "Please summarise this diary session in 3-5 sentences. "
    "Write in first person as a diary entry summary."
)

# ── LLM client ──────────────────────────────────────────────────────────────
if LLM_PROVIDER == "ollama":
    llm = AsyncOpenAI(base_url=f"{OLLAMA_HOST}/v1", api_key="ollama")
else:
    llm = AsyncOpenAI(base_url=VLLM_URL, api_key="token")

# ── Per-user state ───────────────────────────────────────────────────────────
histories: dict[int, list[dict]] = {}


# ── Memory ────────────────────────────────────────────────────────────────────

def list_summaries() -> list[Path]:
    """All saved session summaries, newest first."""
    return sorted(SESSIONS_DIR.glob("*_summary.md"), reverse=True)


def load_memories() -> str:
    """Load the last MEMORY_SESSIONS summaries as a single background-memory block."""
    if MEMORY_SESSIONS == 0:
        return ""
    summaries = list_summaries()[:MEMORY_SESSIONS]
    if not summaries:
        return ""
    parts = []
    for f in reversed(summaries):  # oldest first so the model reads them chronologically
        # Strip the markdown title (first two lines) and keep the body.
        body = "\n".join(f.read_text(encoding="utf-8").strip().split("\n")[2:]).strip()
        date = f.name[:10]
        parts.append(f"[{date}]\n{body}")
    return "\n\n---\n\n".join(parts)


def build_system_message() -> str:
    base = SYSTEM_PROMPT
    memories = load_memories()
    if memories:
        base += (
            "\n\n---\n\nSummaries of recent past diary sessions — use these as "
            "background memory, not as something to announce:\n\n" + memories
        )
    return base


def get_history(user_id: int) -> list[dict]:
    """Return this user's running conversation. Memories are (re)loaded fresh
    whenever a new conversation starts — so anything saved on previous days is
    picked up the next time the user starts talking or runs /clear."""
    if user_id not in histories:
        msgs = []
        system = build_system_message()
        if system:
            msgs.append({"role": "system", "content": system})
        histories[user_id] = msgs
    return histories[user_id]


def trim_history(msgs: list[dict]) -> list[dict]:
    system   = [m for m in msgs if m["role"] == "system"]
    dialogue = [m for m in msgs if m["role"] != "system"]
    return system + dialogue[-MAX_HISTORY:]


def allowed(update: Update) -> bool:
    """Allow everyone if ALLOWED_USER_ID is 0, otherwise only the configured ID.
    When a message is rejected we LOG the rejected id — that line in the logs is
    exactly the number you need to paste into TELEGRAM_ALLOWED_USER_ID."""
    if ALLOWED_USER_ID == 0:
        return True
    uid = update.effective_user.id if update.effective_user else None
    if uid == ALLOWED_USER_ID:
        return True
    print(
        f"  ⚠  Ignored message from unauthorized user id={uid} "
        f"(TELEGRAM_ALLOWED_USER_ID is set to {ALLOWED_USER_ID}). "
        f"If this is you, set TELEGRAM_ALLOWED_USER_ID={uid} in .env and restart."
    )
    return False


# ── Audio helpers ─────────────────────────────────────────────────────────────

def wav_to_ogg(wav_bytes: bytes) -> bytes:
    result = subprocess.run(
        ["ffmpeg", "-i", "pipe:0", "-c:a", "libopus", "-b:a", "32k", "-f", "ogg", "pipe:1"],
        input=wav_bytes, capture_output=True, check=True,
    )
    return result.stdout


async def transcribe(audio_bytes: bytes) -> str:
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            STT_URL,
            files={"audio": ("input.ogg", audio_bytes, "audio/ogg")},
        )
        resp.raise_for_status()
        return resp.json().get("text", "").strip()


async def speak(text: str) -> bytes:
    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(
            TTS_URL,
            json={"text": text, "voice": TTS_VOICE, "speed": 1.0},
        )
        resp.raise_for_status()
        return wav_to_ogg(resp.content)


# ── LLM ──────────────────────────────────────────────────────────────────────

async def chat(user_id: int, user_text: str) -> str:
    history = get_history(user_id)
    history.append({"role": "user", "content": user_text})
    histories[user_id] = trim_history(history)
    response = await llm.chat.completions.create(
        model=LLM_MODEL,
        messages=histories[user_id],
    )
    reply = response.choices[0].message.content
    histories[user_id].append({"role": "assistant", "content": reply})
    return reply


async def save_session(user_id: int) -> str:
    history = get_history(user_id)
    if not any(m["role"] != "system" for m in history):
        return "Nothing to save yet."
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
    try:
        resp = await llm.chat.completions.create(
            model=LLM_MODEL,
            messages=history + [{"role": "user", "content": SUMMARY_PROMPT}],
        )
        summary = resp.choices[0].message.content
    except Exception as e:
        summary = f"(Summary failed: {e})"
    lines = [
        f"**{'You' if m['role'] == 'user' else 'Lena'}:** {m['content']}\n"
        for m in history if m["role"] != "system"
    ]
    tag = f"_telegram_{user_id}"
    (SESSIONS_DIR / f"{timestamp}{tag}_summary.md").write_text(
        f"# Telegram Session Summary — {timestamp}\n\n{summary}\n", encoding="utf-8"
    )
    (SESSIONS_DIR / f"{timestamp}{tag}_full.md").write_text(
        f"# Telegram Session — {timestamp}\n\n" + "\n".join(lines), encoding="utf-8"
    )
    return f"Session saved ✓  ({timestamp})"


# ── Command handlers ──────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    n = len(list_summaries())
    memory_note = f"I have {n} past session(s) in memory." if n else "No past sessions yet."
    await update.message.reply_text(
        f"Hey. I'm Lena — your private diary companion.\n\n"
        f"{memory_note}\n\n"
        f"Send me a message or voice note whenever you're ready.\n\n"
        f"/memory — show what I remember\n"
        f"/clear  — start a fresh conversation (reloads memory)\n"
        f"/save   — save and summarise this session\n"
        f"/stop   — save session and restart the bot"
    )


async def cmd_clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    histories.pop(update.effective_user.id, None)  # rebuilt fresh (with latest memory) on next message
    await update.message.reply_text("Conversation cleared. Fresh start — past sessions reloaded into memory.")


async def cmd_memory(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    summaries = list_summaries()
    if not summaries:
        await update.message.reply_text("I don't have any saved sessions yet.")
        return
    remembered = summaries[:MEMORY_SESSIONS]
    dates = ", ".join(f.name[:10] for f in remembered)
    await update.message.reply_text(
        f"I keep the {MEMORY_SESSIONS} most recent sessions in mind.\n"
        f"Total saved: {len(summaries)}\n"
        f"Currently remembering: {dates}"
    )


async def cmd_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    await update.message.reply_text("Saving…")
    status = await save_session(update.effective_user.id)
    await update.message.reply_text(status)


async def cmd_stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    user_id = update.effective_user.id
    await update.message.reply_text("Saving session before stopping…")
    status = await save_session(user_id)
    await update.message.reply_text(
        f"{status}\n\nGoodbye. 🌙\n\n"
        f"The bot will restart automatically — message me when you want to talk again."
    )
    asyncio.create_task(context.application.stop())


# ── Message handlers ──────────────────────────────────────────────────────────

async def _respond(update, context, user_id, user_text):
    """Shared reply path for both text and voice."""
    cleaned = re.sub(r"[^\w\s]", "", user_text.lower())
    if cleaned.strip() == SAVE_KEYWORD:
        await update.message.reply_text(await save_session(user_id))
        return

    try:
        reply = await chat(user_id, user_text)
    except Exception as e:
        print(f"  ✗ LLM error: {e}")
        await update.message.reply_text(
            "I couldn't reach the language model just now. "
            "If you're on the Ollama backend, make sure the diary-ollama container is running."
        )
        return

    await update.message.reply_text(reply)

    if VOICE_REPLIES:
        await context.bot.send_chat_action(update.effective_chat.id, ChatAction.UPLOAD_VOICE)
        try:
            ogg = await speak(reply)
            await context.bot.send_voice(update.effective_chat.id, voice=io.BytesIO(ogg))
        except Exception as e:
            print(f"  ✗ TTS error: {e}")


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
    await _respond(update, context, update.effective_user.id, update.message.text.strip())


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
    try:
        voice_file = await context.bot.get_file(update.message.voice.file_id)
        buf = io.BytesIO()
        await voice_file.download_to_memory(buf)
        buf.seek(0)
        user_text = await transcribe(buf.read())
    except Exception as e:
        await update.message.reply_text(f"Couldn't process audio: {e}")
        return

    if not user_text:
        await update.message.reply_text("Didn't catch that — try again.")
        return

    await update.message.reply_text(f"_{user_text}_", parse_mode="Markdown")
    await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
    await _respond(update, context, update.effective_user.id, user_text)


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    n_mem = min(MEMORY_SESSIONS, len(list_summaries()))
    print("\n🌿 Telegram bot starting…")
    print(f"  LLM: {LLM_PROVIDER} / {LLM_MODEL}")
    print(f"  Voice: {TTS_VOICE} | Replies: {'text+voice' if VOICE_REPLIES else 'text only'}")
    if n_mem:
        print(f"  ✓ {n_mem} past session(s) loaded into memory")
    print(f"  Allowed user: {ALLOWED_USER_ID or 'anyone'}")

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start",  cmd_start))
    app.add_handler(CommandHandler("clear",  cmd_clear))
    app.add_handler(CommandHandler("memory", cmd_memory))
    app.add_handler(CommandHandler("save",   cmd_save))
    app.add_handler(CommandHandler("stop",   cmd_stop))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))

    print("✅ Polling for messages…\n")
    # drop_pending_updates clears any backlog that piled up while the bot was
    # offline, which also resolves the "stuck / no replies" state after a crash.
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
