# ScamSentinel

A local, two-person voice-call app that automatically transcribes the call,
scores it for scam indicators in real time, and flags three prepared
AI-voice demo recordings by matching their transcripts. It works with a
fast Groq Whisper STT.

## What's actually implemented (not a mockup)

- **Real two-way voice call** over WebRTC (browser mic → peer connection →
  browser speaker). Audio never touches the server — it's peer-to-peer.
- **Real speech-to-text**: each browser streams roughly eight-second audio
  chunks of its own mic to the server over the same WebSocket. When
  configured, Groq `whisper-large-v3-turbo` transcribes each chunk in a
  single request.
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
- **Prepared-recording detector**: the accumulated transcript is compared
  with the three configured demo scripts. It needs multiple distinctive
  matching ideas, making it tolerant of minor STT errors while avoiding a
  flag from one generic scam phrase.
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
- A Groq API key with access to `whisper-large-v3-turbo`.
- A normal laptop CPU is enough. STT runs through Groq; no local speech
  model, GPU, or ffmpeg installation is required.
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

```bash`
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

No ffmpeg installation is needed for the Groq-based STT path.

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

### Recommended: fast Groq transcription

Set the key only in the terminal that starts the server. Groq receives each
audio chunk for transcription; keep the key out of source control and do not
paste it into chats or logs.

```powershell
$env:GROQ_API_KEY="your-key"
uvicorn server:app --host 0.0.0.0 --port 8000
```

### Optional: Gemini LLM scam assessment

Create a key in [Google AI Studio](https://aistudio.google.com/), then set
it only in the terminal that starts the server. The separate Gemini
scam-text request is off by default so it cannot delay or consume quota
needed by STT; enable it explicitly if needed.

```powershell
$env:GEMINI_API_KEY="your-key"
$env:GEMINI_SCAM_ANALYSIS="true"
$env:GEMINI_MODEL="gemini-3.5-flash-lite" # optional
uvicorn server:app --host 0.0.0.0 --port 8000
```

Without a key, or when Gemini is unavailable, the local heuristic remains
active. The UI identifies whether the latest result came from Gemini or the
local heuristic.

## How it's wired together

- `server.py` — FastAPI app. One WebSocket per browser
  (`/ws/{room_id}/{username}`) carries: WebRTC signaling (offer/answer/ICE
  relay), binary audio chunks (for STT + voice analysis), and JSON events
  (transcript/risk/voice updates, mute status, demo trigger, call end).
- `detection/stt.py` — sends each browser WebM chunk to Groq Whisper Large
  V3 Turbo for transcription.
- `detection/scam_detector.py` — contextual heuristic scorer + optional
  Gemini backend.
- `detection/voice_detector.py` — transcript matcher for the three prepared
  AI-voice demo recordings.
- `static/` — plain HTML/CSS/JS frontend (no build step). `app.js` handles
  `getUserMedia`, `RTCPeerConnection`, the chunked `MediaRecorder` capture
  loop, and all live UI updates.

## Troubleshooting "my voice isn't being detected"

1. **Open the browser console** (F12 → Console tab) on the tab that's
   talking. You should see logs like `[ScamSentinel] sent audio chunk #1
   (18342 bytes)` every ~8 seconds while you speak. If you see nothing at
   all, the recorder never started — check the on-screen banner for a
   "Microphone requires HTTPS" message and see the LAN section above.
2. **Check the server terminal.** Every chunk that arrives is logged:
   `received audio chunk from Asha (18342 bytes)`, followed by either
   `Transcribed: 'hello there'` or `no speech detected`. If chunks arrive
   but never transcribe, confirm `GROQ_API_KEY` is set in the terminal that
   starts Uvicorn and check the server log for a Groq API error.
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

Groq Whisper can still be inaccurate when it receives near-silent or noisy
audio. The mute button already stops chunks from being *sent* while muted;
this happens on real, unmuted chunks that just don't contain much speech
(room tone, a pause, mic hiss).

If you still see an inaccurate transcript:
- Use headphones if two tabs share one laptop's mic+speakers — otherwise
  each mic picks up the other tab's playback and transcribes that as noise.
- Speak closer to the mic / reduce background noise, which raises the RMS
  and gives Groq a cleaner signal.

## Known limitations

- **AI-voice detection is a prepared-script matcher, not a general deepfake
  classifier.** It flags only the three configured recordings and should
  never be treated as proof that an arbitrary voice is synthetic.
- **Scam detection without Gemini is rule-based**, not a full LLM. It
  handles the patterns in this spec well but won't generalize to totally
  novel scam scripts as well as a real LLM would. Gemini improves that when
  its optional API key is configured.
- **Transcription runs on independent chunks** (~8s each), so
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
