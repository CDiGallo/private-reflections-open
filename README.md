# 🌿 Diary Companion

A fully **local**, voice-enabled AI diary that runs in Docker on Windows via WSL2.
You talk or type; it listens, replies, and (optionally) speaks back. Nothing leaves
your machine — the language model, speech-to-text, and text-to-speech all run in
containers on your own computer.

There are two ways to use it:

- **Terminal** — a chat client in your WSL console, with optional microphone input
  and spoken replies through your Windows speakers.
- **Telegram bot** (optional) — journal from your phone with text or voice notes.

Both share the same persona and the same long-term memory.

---

## What it does

- Runs a local LLM (via **Ollama**, or **vLLM** for HuggingFace models on a GPU).
- Transcribes your voice with **faster-whisper** (speech-to-text).
- Speaks replies with **Kokoro** (text-to-speech).
- Saves each session as a Markdown transcript **and** a short summary.
- Feeds recent summaries back into the prompt so it remembers previous days.
- The persona is a plain text file (`services/app/system_prompt.txt`) you can edit
  freely.

### Services

| Container       | Role                            | Profile       | Needs GPU |
|-----------------|---------------------------------|---------------|-----------|
| diary-ollama    | LLM backend (Ollama)            | `ollama`      | optional  |
| diary-vllm      | LLM backend (vLLM / HF models)  | `huggingface` | **yes**   |
| diary-tts       | Text-to-Speech (Kokoro)         | always        | no        |
| diary-stt       | Speech-to-Text (faster-whisper) | always        | optional  |
| diary-app       | Terminal chat client            | always        | no        |
| diary-telegram  | Telegram bot                    | `telegram`    | no        |

> **Profiles matter.** A bare `docker compose up` starts only `tts`, `stt`, and
> `app` — it will **not** start Ollama or the Telegram bot. Always start with the
> right profiles (the `start.sh` helper does this for you).

---

## Requirements

Everything below can be installed on a fresh **Windows 11** machine. You do **not**
need a GPU — CPU works for Ollama, Whisper, and Kokoro. (A GPU only matters if you
want the vLLM/HuggingFace backend, or much faster transcription.)

- Windows 11 (or a recent Windows 10 build with WSLg)
- WSL2 + a Linux distro (Ubuntu, installed below)
- Docker Desktop with the WSL2 backend
- **Optional:** an NVIDIA GPU + current driver (for vLLM or CUDA Whisper)
- **Optional:** a Telegram account (for the phone bot)
- **Optional:** WSLg audio (built into Windows 11) — only for terminal mic/speaker

---

## From a bare Windows 11 install → running

### 1. Install WSL2 + Ubuntu

Open **PowerShell as Administrator** and run:

```powershell
wsl --install
```

This installs WSL2, Ubuntu, and **WSLg** (which provides audio + GUI support).
Reboot when prompted, then launch "Ubuntu" from the Start menu and create your Linux
username/password.

Verify you are on WSL **2**:

```powershell
wsl --status
wsl -l -v        # the VERSION column should say 2
```

### 2. Install Docker Desktop

Download Docker Desktop for Windows and install it. Then:

1. Start Docker Desktop.
2. **Settings → General →** enable *Use the WSL 2 based engine*.
3. **Settings → Resources → WSL Integration →** enable integration with your
   **Ubuntu** distro.
4. Apply & Restart.

Confirm it works from inside Ubuntu:

```bash
docker version
docker run --rm hello-world
```

### 3. (Optional) Enable your NVIDIA GPU

Only needed for the vLLM backend or `WHISPER_DEVICE=cuda`.

1. Install the latest **NVIDIA driver for Windows** (the normal Game Ready / Studio
   driver — it includes WSL GPU support). You do **not** install CUDA inside WSL.
2. Test from Ubuntu:

```bash
docker run --rm --gpus all nvidia/cuda:12.4.0-base-ubuntu22.04 nvidia-smi
```

If you have no GPU, skip this and keep `LLM_PROVIDER=ollama` with `WHISPER_DEVICE=cpu`.

### 4. Get the code

Inside Ubuntu:

```bash
cd ~
git clone <THIS_REPO_URL> private-reflections
cd private-reflections
chmod +x start.sh stop.sh
```

> Keep the project in your **Linux** home (`~/...`), not under `/mnt/c/...`.
> Bind-mounting build context from the Windows filesystem is slow and can break
> file permissions.

### 5. Create the Windows ↔ container data folder (the "mount")

This is the one piece of Windows/Linux plumbing you must set up. Here is what's
going on:

```
Windows                         WSL (Ubuntu)                  Container
C:\Users\<you>\tts-out    ==    /mnt/c/Users/<you>/tts-out  ==  /audio
```

- WSL automatically mounts your Windows `C:` drive at **`/mnt/c`**. So the Windows
  folder `C:\Users\<you>\tts-out` is the same place as `/mnt/c/Users/<you>/tts-out`
  inside Ubuntu.
- `docker-compose.yml` **bind-mounts** that folder into the containers as `/audio`.
- Result: your diary transcripts and summaries are written to
  `C:\Users\<you>\tts-out\sessions\` — readable directly in Windows File Explorer,
  and they persist even if you delete every container.

Create the folder (replace `<you>` with your Windows username):

```bash
mkdir -p /mnt/c/Users/<you>/tts-out
```

Not sure of your exact Windows username? From Ubuntu:

```bash
cmd.exe /c "echo %USERNAME%"
```

> **Alternative (Linux-only storage):** if you'd rather not touch the Windows
> filesystem, change the two `/mnt/c/Users/${WINDOWS_USER}/tts-out:/audio` lines in
> `docker-compose.yml` to a named volume (e.g. `diary_data:/audio`) and add
> `diary_data:` under `volumes:`. Your data then lives inside WSL and is reachable
> from Explorer at `\\wsl$\Ubuntu\...`. The Windows-folder approach above is the
> simplest and is what the defaults assume.

### 6. Configure `.env`

```bash
cp .env.example .env
nano .env
```

At minimum set **`WINDOWS_USER`** to your Windows account name (the folder under
`C:\Users\`). Leave everything else at defaults for a first run. Save and exit.

### 7. (Optional) Create the Telegram bot

Skip this if you only want the terminal.

1. In Telegram, message **@BotFather** → `/newbot`, pick a name, copy the **token**
   it gives you (looks like `123456789:AA...`).
2. Message **@userinfobot** → it replies with your numeric **user ID**.
3. In `.env`:
   ```
   TELEGRAM_ENABLED=true
   TELEGRAM_BOT_TOKEN=123456789:AA...     # paste raw, no quotes, no spaces
   TELEGRAM_ALLOWED_USER_ID=987654321     # your numeric id (or 0 to allow anyone)
   ```

### 8. Start it

```bash
./start.sh
```

`start.sh` reads `.env`, picks the correct profiles (Ollama vs vLLM, and Telegram if
enabled), builds the images, and attaches you to the terminal client. First start is
slow — it downloads the LLM, Whisper, and Kokoro models (cached afterwards).

To stop everything:

```bash
./stop.sh
```

---

## Networking & ports — please read

Docker Desktop's WSL2 backend **automatically publishes** the ports below to
`localhost` on Windows, and Windows Defender Firewall may pop up a prompt the first
time — **allow it**. You usually do not configure forwarding by hand, but you should
know what's listening and why things conflict:

| Port  | Service | Purpose                                                |
|-------|---------|--------------------------------------------------------|
| 11434 | Ollama  | LLM API                                                |
| 8000  | TTS     | Kokoro speech synthesis                                |
| 8001  | STT     | Whisper transcription                                  |
| 8080  | vLLM    | HuggingFace model API (only with the `huggingface` profile) |

Important points:

- **Inside the stack, none of these published ports are required.** Containers talk
  to each other over Docker's private network using service names
  (`http://ollama:11434`, `http://tts:8000`, …). The published ports only matter if
  you want to reach a service from Windows (e.g. a browser or another tool).
- **Port conflicts are the most common networking failure.** If you already run
  **Ollama natively on Windows**, it owns `11434` and the container will fail to
  bind it. Same for anything on `8000`/`8001`. Either stop the other program, or
  change the left-hand (host) number in `docker-compose.yml`, e.g. `"11500:11434"`.
- **WSL mirrored networking:** on recent Windows 11 you can enable mirrored mode in
  `C:\Users\<you>\.wslconfig` (`[wsl2]` → `networkingMode=mirrored`). It changes how
  `localhost` and ports behave between Windows and WSL. If `localhost:PORT` doesn't
  reach a container after a Docker restart, toggling this (and `wsl --shutdown`) is
  worth trying.
- If you only use the **Telegram bot**, none of this matters — the bot reaches
  Telegram outbound over HTTPS and needs no inbound ports at all.

---

## Audio (PulseAudio via WSLg) — only for the terminal

Spoken replies and microphone input in the **terminal** client route through WSLg's
PulseAudio server:

```
Container  --(/audio + paplay/parecord)-->  /mnt/wslg/PulseServer  -->  Windows speakers/mic
```

WSLg ships with Windows 11, so the socket at `/mnt/wslg/PulseServer` is already
there. The compose file mounts `/mnt/wslg` into the containers and sets
`PULSE_SERVER`, so it works out of the box on most setups.

**You can ignore audio entirely if you want:**

- The **Telegram** path does **not** use WSLg at all — TTS and STT happen inside the
  containers and are delivered as Telegram voice notes. Phone audio "just works"
  regardless of your WSL audio setup.
- The **terminal** still works as a text chat without audio; you simply won't hear
  replies and can't use the mic. (If `paplay`/`parecord` fail, the app prints a
  small error and carries on.)

---

## Usage

### Terminal (`diary-app`)

- **Type + Enter** → chat by text.
- **Empty line + Enter** → record from mic; Enter again to stop → auto-transcribed.
- **`wrap up`** → save the session.
- **`exit`** → save and quit.
- Detach without stopping: `Ctrl+P` then `Ctrl+Q`. Reattach: `docker attach diary-app`.

### Telegram

Send text or a voice note. Commands:

| Command   | Action                                          |
|-----------|-------------------------------------------------|
| `/start`  | Greeting + how many sessions are remembered     |
| `/memory` | Show which past sessions are loaded into memory |
| `/clear`  | Start a fresh conversation (reloads memory)     |
| `/save`   | Save and summarise the current session          |
| `/stop`   | Save, then restart the bot                       |
| `wrap up` | (plain message) save the session                |

---

## Choosing a model

Default: **Ollama** running **`llama3.2`**.

- **Another Ollama model:** set `LLM_MODEL` in `.env` to any Ollama tag, e.g.
  `qwen3:8b`, `gemma3:12b`, `mistral`. It's pulled automatically on first start.
- **HuggingFace / vLLM (GPU):** set `LLM_PROVIDER=huggingface` and point `LLM_MODEL`
  at an AWQ model such as `Qwen/Qwen2.5-7B-Instruct-AWQ`. Add `HF_TOKEN` for gated
  models.

Restart after changing `.env`: `./stop.sh && ./start.sh`.

---

## Memory

When a session is saved, two files are written to `tts-out/sessions/`: a full
transcript and a short first-person summary. On the next start (and on `/clear`), the
most recent **`MEMORY_SESSIONS`** summaries are loaded back into the system prompt as
background memory, so the companion recalls earlier days without you repeating
yourself. The terminal and Telegram share this folder, so memory carries across both.
Set `MEMORY_SESSIONS=0` to turn it off.

---

## Configuration reference (`.env`)

| Variable                   | Default      | Notes                                       |
|----------------------------|--------------|---------------------------------------------|
| `WINDOWS_USER`             | —            | **Required.** Your `C:\Users\` folder name. |
| `LLM_PROVIDER`             | `ollama`     | `ollama` or `huggingface`                   |
| `LLM_MODEL`                | `llama3.2`   | Ollama tag, or AWQ HF model                 |
| `HF_TOKEN`                 | empty        | Only for gated HuggingFace models           |
| `MAX_HISTORY`              | `20`         | Recent messages kept in context             |
| `MEMORY_SESSIONS`          | `5`          | Past sessions recalled (`0` = off)          |
| `SAVE_KEYWORD`             | `wrap up`    | Phrase that saves a session                 |
| `TTS_LANG`                 | `a`          | `a` American, `b` British                   |
| `TTS_VOICE`                | `af_sarah`   | Kokoro voice id                             |
| `WHISPER_MODEL`            | `base`       | `tiny`…`large-v3`                           |
| `WHISPER_DEVICE`           | `cpu`        | `cpu` or `cuda`                             |
| `TELEGRAM_ENABLED`         | `false`      | Start the bot alongside the diary           |
| `TELEGRAM_BOT_TOKEN`       | empty        | From @BotFather                             |
| `TELEGRAM_ALLOWED_USER_ID` | `0`          | Your id; `0` allows anyone                  |
| `TELEGRAM_VOICE_REPLIES`   | `true`       | Also send spoken replies                    |

---

## Troubleshooting

- **Bare `docker compose up` started, but no model / no bot.** Profiles weren't
  active. Use `./start.sh`, or add `--profile ollama --profile telegram`.
- **`mkdir /mnt/c/Users/.../tts-out` error on start.** `WINDOWS_USER` is wrong or the
  folder doesn't exist. Fix the value and run `mkdir -p /mnt/c/Users/<you>/tts-out`.
- **Telegram bot never replies.** Check `docker logs diary-telegram`. An
  `InvalidToken` crash means a bad/revoked token (get a fresh one from @BotFather).
  An `⚠ Ignored message from unauthorized user id=NNN` line means your real id is
  `NNN` — put it in `TELEGRAM_ALLOWED_USER_ID`. Verify a token independently with
  `curl "https://api.telegram.org/bot<TOKEN>/getMe"`.
- **Port already in use (e.g. 11434).** Another program (often a native Ollama)
  owns it. Stop it or remap the host port in `docker-compose.yml`.
- **Code changes don't take effect.** Rebuild: `docker compose ... up --build -d`.
- **First start is very slow.** It's downloading models; they're cached afterwards.
  Use `WHISPER_MODEL=tiny` to speed transcription, or a GPU for everything.

### Reset

```bash
./stop.sh
docker compose --profile ollama --profile huggingface --profile telegram \
    down --volumes --rmi all
./start.sh
```

> `down --volumes` deletes the cached models (Ollama/Whisper/Kokoro); they
> re-download on next start. Your diary files in `tts-out/` are untouched.

---

## Privacy

Everything runs locally: the LLM, speech-to-text, and text-to-speech are all
containers on your machine, and your diary is stored on your own disk. The two
network exceptions are model downloads on first run, and — if you enable it — the
Telegram bot, which sends your messages through Telegram's servers like any normal
chat. For a fully offline diary, use the terminal only.

---

## License

MIT — see [LICENSE](LICENSE). Replace `<Your Name>` in that file before publishing.
