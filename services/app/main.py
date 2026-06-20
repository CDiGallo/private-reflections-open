import os
import re
import time
import subprocess
from datetime import datetime
from pathlib import Path

import requests
from openai import OpenAI

# ── Config ─────────────────────────────────────────────────────────────────
LLM_PROVIDER    = os.getenv("LLM_PROVIDER", "ollama")
LLM_MODEL       = os.getenv("LLM_MODEL", "llama3.2")
OLLAMA_HOST     = os.getenv("OLLAMA_HOST", "http://ollama:11434")
VLLM_URL        = os.getenv("VLLM_URL", "http://vllm:8000/v1")
TTS_URL         = os.getenv("TTS_URL", "http://tts:8000/tts")
STT_URL         = os.getenv("STT_URL", "http://stt:8001/stt")
TTS_VOICE       = os.getenv("TTS_VOICE", "af_sarah")
MAX_HISTORY     = int(os.getenv("MAX_HISTORY", "20"))
MEMORY_SESSIONS = int(os.getenv("MEMORY_SESSIONS", "5"))
SAVE_KEYWORD    = os.getenv("SAVE_KEYWORD", "wrap up").strip().strip("\"'").strip().lower()

AUDIO_DIR    = Path("/audio")
SESSIONS_DIR = AUDIO_DIR / "sessions"
REPLY_WAV    = AUDIO_DIR / "reply.wav"
INPUT_WAV    = AUDIO_DIR / "input.wav"

# ── System prompt — loaded from file (single source, shared with Telegram) ──
_prompt_path = Path("/app/system_prompt.txt")
if _prompt_path.exists() and _prompt_path.stat().st_size > 0:
    SYSTEM_PROMPT = _prompt_path.read_text(encoding="utf-8").strip()
else:
    SYSTEM_PROMPT = ""  # no system prompt — model runs without instructions
    print("  ⚠  system_prompt.txt not found or empty — running without system prompt")

SUMMARY_PROMPT = (
    "Please summarise this diary session. "
    "Write 3-5 sentences capturing the main themes, feelings, and any insights. "
    "Write in first person, as if it's a diary entry summary."
)

# ── LLM client ─────────────────────────────────────────────────────────────
if LLM_PROVIDER == "ollama":
    llm = OpenAI(base_url=f"{OLLAMA_HOST}/v1", api_key="ollama")
    llm_health_url = OLLAMA_HOST
else:
    llm = OpenAI(base_url=VLLM_URL, api_key="token")
    llm_health_url = VLLM_URL.replace("/v1", "") + "/health"

# ── Helpers ─────────────────────────────────────────────────────────────────

def load_memories() -> str:
    """Load the last MEMORY_SESSIONS summaries and return them as a text block."""
    if MEMORY_SESSIONS == 0 or not SESSIONS_DIR.exists():
        return ""
    summaries = sorted(SESSIONS_DIR.glob("*_summary.md"), reverse=True)[:MEMORY_SESSIONS]
    if not summaries:
        return ""
    parts = []
    for f in reversed(summaries):  # oldest first
        body = "\n".join(
            f.read_text(encoding="utf-8").strip().split("\n")[2:]
        ).strip()
        date = f.name[:10]
        parts.append(f"[{date}]\n{body}")
    return "\n\n---\n\n".join(parts)


def build_initial_messages() -> list[dict]:
    """Build the starting message list, injecting past session memories."""
    base = SYSTEM_PROMPT
    memories = load_memories()
    if memories:
        base += f"\n\n---\n\nSummaries of recent past diary sessions — use these as background memory, not as something to announce:\n\n{memories}"
    return [{"role": "system", "content": base}] if base else []


def has_conversation(msgs) -> bool:
    return any(m["role"] != "system" for m in msgs)


def trim_history(msgs: list[dict]) -> list[dict]:
    system   = [m for m in msgs if m["role"] == "system"]
    dialogue = [m for m in msgs if m["role"] != "system"]
    return system + dialogue[-MAX_HISTORY:]


def wait_for_service(url: str, name: str, retries: int = 20, delay: int = 3) -> bool:
    for i in range(retries):
        try:
            requests.get(url, timeout=2)
            print(f"  ✓ {name} ready")
            return True
        except requests.exceptions.RequestException:
            print(f"  … waiting for {name} ({i + 1}/{retries})")
            time.sleep(delay)
    print(f"  ✗ {name} unreachable — continuing anyway")
    return False


def ensure_model():
    if LLM_PROVIDER == "ollama":
        print(f"  Pulling '{LLM_MODEL}' via Ollama…")
        try:
            with requests.post(
                f"{OLLAMA_HOST}/api/pull",
                json={"name": LLM_MODEL},
                stream=True,
                timeout=300,
            ) as r:
                for _ in r.iter_lines():
                    pass
            print(f"  ✓ {LLM_MODEL} ready")
        except Exception as exc:
            print(f"  ✗ Pull failed: {exc}")
    else:
        print(f"  ✓ vLLM serving '{LLM_MODEL}'")


def record_audio(path: Path) -> bool:
    print("🎙  Recording … press Enter to stop.")
    proc = subprocess.Popen(
        ["parecord", "--channels=1", "--rate=16000", "--file-format=wav", str(path)],
        stderr=subprocess.DEVNULL,
    )
    input()
    proc.terminate()
    proc.wait()
    return path.exists() and path.stat().st_size > 1000


def transcribe(path: Path) -> str:
    with open(path, "rb") as fh:
        resp = requests.post(
            STT_URL,
            files={"audio": ("input.wav", fh, "audio/wav")},
            timeout=60,
        )
    resp.raise_for_status()
    return resp.json().get("text", "").strip()


def speak(text: str):
    resp = requests.post(
        TTS_URL,
        json={"text": text, "voice": TTS_VOICE, "speed": 1.0},
        timeout=120,
    )
    resp.raise_for_status()
    REPLY_WAV.write_bytes(resp.content)
    subprocess.run(["paplay", str(REPLY_WAV)], check=True)


def chat(msgs: list[dict], user_input: str) -> str:
    msgs.append({"role": "user", "content": user_input})
    msgs[:] = trim_history(msgs)
    response = llm.chat.completions.create(model=LLM_MODEL, messages=msgs)
    reply = response.choices[0].message.content
    msgs.append({"role": "assistant", "content": reply})
    return reply


def save_session(msgs: list[dict]):
    if not has_conversation(msgs):
        print("Nothing to save yet.\n")
        return
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
    print("  Generating summary…")
    try:
        resp = llm.chat.completions.create(
            model=LLM_MODEL,
            messages=msgs + [{"role": "user", "content": SUMMARY_PROMPT}],
        )
        summary_text = resp.choices[0].message.content
    except Exception as exc:
        summary_text = f"(Summary failed: {exc})"
    lines = [
        f"**{'You' if m['role'] == 'user' else 'Lena'}:** {m['content']}\n"
        for m in msgs if m["role"] != "system"
    ]
    (SESSIONS_DIR / f"{timestamp}_summary.md").write_text(
        f"# Session Summary — {timestamp}\n\n{summary_text}\n", encoding="utf-8"
    )
    (SESSIONS_DIR / f"{timestamp}_full.md").write_text(
        f"# Full Session — {timestamp}\n\n" + "\n".join(lines), encoding="utf-8"
    )
    print(f"  ✓ Summary    → sessions/{timestamp}_summary.md")
    print(f"  ✓ Transcript → sessions/{timestamp}_full.md\n")


# ── Startup ─────────────────────────────────────────────────────────────────

print("\n🌿 Diary Companion starting up…")
SESSIONS_DIR.mkdir(parents=True, exist_ok=True)  # also creates /audio

wait_for_service(f"{TTS_URL.rsplit('/', 1)[0]}/health", "TTS")
wait_for_service(f"{STT_URL.rsplit('/', 1)[0]}/health", "STT")
wait_for_service(llm_health_url, f"LLM ({LLM_PROVIDER})",
                 retries=60 if LLM_PROVIDER == "huggingface" else 20,
                 delay=5  if LLM_PROVIDER == "huggingface" else 3)
ensure_model()

# Build initial messages with memories
messages = build_initial_messages()
n_memories = min(MEMORY_SESSIONS, len(list(SESSIONS_DIR.glob("*_summary.md"))))
if n_memories:
    print(f"  ✓ Loaded {n_memories} past session(s) into memory")

print(f"\nReady! Type your message and press Enter, or press Enter on an empty line to use your mic.")
print(f"Say or type \"{SAVE_KEYWORD}\" to save the session. Type 'exit' to quit.\n")

# ── Main loop ────────────────────────────────────────────────────────────────

while True:
    try:
        print("You (type + Enter to chat, empty Enter for mic, 'exit' to quit): ", end="", flush=True)
        user_input = input()

        if user_input.lower() == "exit":
            if has_conversation(messages):
                print("Saving session before exit…")
                save_session(messages)
            print("Goodbye! 🌙")
            break

        if user_input == "":
            if not record_audio(INPUT_WAV):
                print("No audio captured — try again.\n")
                continue
            print("Transcribing…")
            try:
                user_input = transcribe(INPUT_WAV)
            except Exception as exc:
                print(f"STT error: {exc} — try typing instead.\n")
                continue
            if not user_input:
                print("Didn't catch that — try again.\n")
                continue
            print(f"You said: {user_input}\n")

        cleaned = re.sub(r"[^\w\s]", "", user_input.lower().strip())
        if cleaned == SAVE_KEYWORD:
            print("📓 Saving session…")
            save_session(messages)
            try:
                speak("I've saved our session. Feel free to keep going, or say goodbye when you're ready.")
            except Exception:
                pass
            continue

        try:
            reply = chat(messages, user_input)
        except Exception as exc:
            print(f"LLM error: {exc} — try again.\n")
            if messages and messages[-1]["role"] == "user":
                messages.pop()
            continue

        print(f"Lena: {reply}\n")

        try:
            speak(reply)
        except Exception as exc:
            print(f"TTS error: {exc}\n")

    except KeyboardInterrupt:
        print("\nSaving session…")
        save_session(messages)
        print("Goodbye! 🌙")
        break
