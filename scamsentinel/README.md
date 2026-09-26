# ScamSentinel

A local, two-person voice-call app that automatically transcribes the call,
scores it for scam indicators in real time, and runs an experimental
AI/synthetic-voice check — all with free, open-source, locally-run
components. No paid APIs required.

## What's actually implemented (not a mockup)

- **Real two-way voice call** over WebRTC (browser mic → peer connection →
  browser speaker). Audio never touches the server — it's peer-to-peer.
- **Real speech-to-text**: each browser also streams short audio chunks
  (~4s) of its own mic to the server over the same WebSocket. The server
  transcribes them with `faster-whisper` (an open-source, local
  CTranslate2-based Whisper implementation) and broadcasts the text to both
  clients as a live transcript.
- **Real contextual scam scoring**: the running transcript is re-scored
  after every new line. Two backends:
  - A weighted, pattern-based heuristic (always available, no setup) that
    combines *imperative* asks for OTP/PIN/CVV/passwords, impersonation
    language, urgency/threats, remote-access requests, payment/"unlock fee"
    asks, and investment-scheme language — with a co-occurrence bonus so
    multiple different red flags together score much higher than one
    repeated keyword. A bare mention of "OTP" or "bank" barely moves the
    score; "give me the OTP" combined with "I'm from your bank" and
    "immediately" pushes it to HIGH.
  - Optionally, a server-side Gemini API key enables LLM reasoning. The key
    never reaches the browser, and API errors or quota failures automatically
    fall back to the local heuristic.
- **Real AI-voice heuristic**: each ~4s chunk is also run through a
  signal-processing analysis (`librosa`) of pitch jitter, amplitude
  shimmer, spectral flatness, and inter-harmonic noise floor — properties
  that tend to differ between natural speech and TTS/vocoder output. This
  is **explicitly a heuristic, not a trained deepfake classifier** (a real
  classifier needs a GPU and a labeled dataset, which breaks the "free,
  runs on a laptop" requirement). The UI labels it "experimental" and
  states it may produce false positives/negatives, as required.
- **Live risk UI**: scam risk (LOW/MEDIUM/HIGH + confidence + reasons) and
  AI-voice probability update continuously without a page refresh, plus a
  prominent alert banner when risk goes HIGH.
- **Call summary**: on leaving, duration, peak risk, all suspicious events
  seen, and the full transcript are shown.
- **Demo mode**: two scripted scenarios (a scam call, a normal chat) you
  can run solo to see the whole pipeline work without needing a second
  person or microphone.
- The voice call itself works even if the server-side analysis is slow or
  fails — WebRTC audio doesn't depend on it.

## Requirements

- Python 3.10+
- `ffmpeg` on PATH (used to decode browser-recorded audio chunks)
  - Ubuntu/Debian: `sudo apt-get install ffmpeg`
  - macOS: `brew install ffmpeg`
- A normal laptop CPU is enough. No GPU required.
    - RAM: ~2–3 GB free is comfortable (Whisper "base.en" model + librosa).
  - The first call will download the Whisper model (~145 MB for `base.en`) from Hugging
    Face automatically; after that it runs fully offline.
- Two browser windows/tabs (or two devices) with microphone access, over
  `http://localhost` or HTTPS (browsers require a secure context for
  `getUserMedia` — localhost is exempt from that requirement).

## Two laptops on a LAN (read this before testing across machines)

**Browsers block microphone access on any page that isn't a "secure
context"** — that's `https://` or `http://localhost`. Plain
`http://192.168.x.x:8000` (what a second laptop has to use to reach you)
is NOT secure, so the browser silently refuses to record, even though the
page loads fine and the call UI looks normal. This is almost always the
cause of "it connects but nothing gets detected."

Fix: run the server over HTTPS with a self-signed certificate.

```bash
# On Laptop A (the one running the server)
pip install -r requirements.txt   # now includes `cryptography` for this step
ifconfig | grep "inet "           # find Laptop A's LAN IP, e.g. 192.168.1.100

python3 generate_cert.py 192.168.1.100      # writes cert.pem / key.pem
uvicorn server:app --host 0.0.0.0 --port 8000 --ssl-keyfile key.pem --ssl-certfile cert.pem
```

Then on **both** laptops, open:

```
https://192.168.1.100:8000
```

Both browsers will show a "Your connection is not private" warning
because the cert is self-signed, not from a trusted authority — that's
expected for local dev. Click **Advanced → Proceed to 192.168.1.100
(unsafe)** on each laptop, once. After that, mic access works normally and
the call/transcript/detection pipeline behaves exactly like the
`localhost` case.

If you only ever test with two tabs on the *same* laptop, `http://localhost:8000`
already works with no certificate needed — `localhost` is exempt from the
HTTPS requirement.

## Install

```bash
cd scamsentinel
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### Windows repair note

The bundled `.venv` may point at a Python installation that no longer exists.
If `python` or `.venv\\Scripts\\python.exe` fails, install Python 3.10+ from
[python.org](https://www.python.org/downloads/windows/), close and reopen
PowerShell, then create a fresh environment (this does not touch source code):

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
winget install Gyan.FFmpeg
```

Restart PowerShell after installing ffmpeg so it is on `PATH`. Confirm with
`ffmpeg -version` before testing STT.

## Run

```bash
uvicorn server:app --host 0.0.0.0 --port 8000
```

Open `http://localhost:8000` in two browser windows (or two devices on the
same network, using your machine's LAN IP instead of `localhost`).

1. Window A: enter a name, click **Create a call** → note the room code.
2. Window B: enter a name, type the same room code, click **Join**.
3. Allow microphone access in both windows.
4. Talk — you should hear each other immediately (this works even before
   any AI analysis kicks in).
5. Within a few seconds you'll see the transcript populate and the Scam
   Risk / AI Voice panels start updating on their own.
6. Try saying something like *"I'm calling from your bank, please give me
   the OTP immediately or your account will be blocked"* — you should see
   the risk climb to HIGH with an alert banner and reasons listed.
7. Click **Leave** to see the call summary.

No manual "start transcription" step is needed — it's automatic on join.

### Try it solo first (Demo mode)

From the lobby, click **Run demo: suspicious call** or **Run demo: normal
call** to watch the transcript and risk score populate automatically from a
scripted conversation — good for confirming the pipeline works before
testing with a second person.

### Optional: use Gemini for LLM scam assessment

Create a key in [Google AI Studio](https://aistudio.google.com/), then set
it only in the terminal that starts the server. The default model is
`gemini-2.5-flash-lite`; change `GEMINI_MODEL` only if your account offers a
different free-tier model. Requests are limited to one every eight seconds
per room for light usage. Google can change free-tier availability and
limits, so zero cost is not guaranteed permanently.

```powershell
$env:GEMINI_API_KEY="your-key"
$env:GEMINI_MODEL="gemini-2.5-flash-lite" # optional
uvicorn server:app --host 0.0.0.0 --port 8000
```

Without a key, or when Gemini is unavailable, the local heuristic remains
active. The UI identifies whether the latest result came from Gemini or the
local heuristic.

### Optional: better transcription accuracy

  Set an env var before starting the server to choose a Whisper model. The
  default is `base.en`; `small.en` is more accurate but slower, while `tiny.en`
  is faster but less accurate:

```bash
WHISPER_MODEL=base.en uvicorn server:app --host 0.0.0.0 --port 8000
```

## How it's wired together

- `server.py` — FastAPI app. One WebSocket per browser
  (`/ws/{room_id}/{username}`) carries: WebRTC signaling (offer/answer/ICE
  relay), binary audio chunks (for STT + voice analysis), and JSON events
  (transcript/risk/voice updates, mute status, demo trigger, call end).
- `detection/stt.py` — decodes each chunk (via `pydub`/`ffmpeg`) and
  transcribes it with `faster-whisper`.
- `detection/scam_detector.py` — contextual heuristic scorer + optional
  Gemini backend.
- `detection/voice_detector.py` — acoustic-feature heuristic for the
  AI-voice signal.
- `static/` — plain HTML/CSS/JS frontend (no build step). `app.js` handles
  `getUserMedia`, `RTCPeerConnection`, the chunked `MediaRecorder` capture
  loop, and all live UI updates.

## Troubleshooting "my voice isn't being detected"

1. **Open the browser console** (F12 → Console tab) on the tab that's
   talking. You should see logs like `[ScamSentinel] sent audio chunk #1
   (18342 bytes)` every ~4 seconds while you speak. If you see nothing at
   all, the recorder never started — check the on-screen banner for a
   "Microphone requires HTTPS" message and see the LAN section above.
2. **Check the server terminal.** Every chunk that arrives is logged:
   `received audio chunk from Asha (18342 bytes)`, followed by either
   `transcribed: 'hello there'` or `no speech detected`. If chunks are
   arriving but never transcribe anything, try speaking louder/closer to
   the mic, or check that `ffmpeg` is actually on PATH (`ffmpeg -version`).
   If you see `audio decode failed` in the log, `ffmpeg` isn't installed
   or isn't on PATH.
3. **On-screen diagnostics**: under the pulsing "AI analysis active"
   indicator, a small line shows a live `Chunks sent: N | Transcribed
   lines: M` counter — useful for confirming the pipeline is alive without
   opening devtools.
4. If chunks are sent but the server never even logs "received audio
   chunk", the WebSocket connection dropped or never fully opened — check
   for a firewall blocking the port on the server laptop (`sudo ufw allow
   8000` on Linux, or allow port 8000 in Windows Firewall), and confirm
   both laptops are on the same network (not an isolated "guest" Wi-Fi).

## Troubleshooting "transcript shows random/unrelated text"

This is Whisper hallucination, not a bug in the mute button — small Whisper
models (`tiny.en` especially) invent fluent-sounding phrases (e.g. "Thank
you for watching") when fed near-silent or noisy audio, because that's the
kind of caption text they saw during training on quiet video segments. The
mute button already stops chunks from being *sent* while muted; this
happens on real, unmuted chunks that just don't contain much speech (room
tone, a pause, mic hiss).

`detection/stt.py` now: (1) uses a stricter RMS energy gate before calling
Whisper at all, and (2) discards any segment Whisper itself flags as
low-confidence via `no_speech_prob` / `avg_logprob`. If you still see
hallucinated text:
- Use headphones if two tabs share one laptop's mic+speakers — otherwise
  each mic picks up the other tab's playback and transcribes that as noise.
- Try `WHISPER_MODEL=base.en` (or `small.en`) — hallucination drops a lot
  going up from `tiny.en`.
- Speak closer to the mic / reduce background noise, which raises the RMS
  above the silence gate cleanly instead of sitting right at the edge.

## Known limitations

- **AI-voice detection is a heuristic, not a trained classifier.** It looks
  at pitch/amplitude regularity and spectral cleanliness, which correlate
  with — but don't prove — synthetic speech. Expect false positives (very
  calm, monotone natural speakers) and false negatives (high-quality modern
  TTS). Treat it as one input signal, never as proof.
- **Scam detection without Gemini is rule-based**, not a full LLM. It
  handles the patterns in this spec well but won't generalize to totally
  novel scam scripts as well as a real LLM would. Gemini improves that when
  its optional API key is configured.
- **Transcription runs on short independent chunks** (~4s each, for
  low-latency updates), so occasional words can be cut at chunk boundaries
  and speaker turns that span a chunk boundary may be split oddly. Using a
  larger Whisper model improves accuracy but adds latency.
- **Two participants per room** by design (a 3rd joiner gets "room full").
- Works on one machine's two tabs or across a LAN; for use across the
  public internet you'd need to run this behind HTTPS and a TURN server
  (not included — WebRTC across restrictive NATs sometimes needs a TURN
  relay, not just the public STUN server used here).
- Transcript is kept in memory per room for the summary and cleared when
  the process restarts — nothing is written to disk.
