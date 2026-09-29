from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("scamsentinel")

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool

from detection import scam_detector, stt, voice_detector

app = FastAPI(title="ScamSentinel")

MAX_PARTICIPANTS = 2
AUDIO_ANALYSIS_MIN_INTERVAL = 0.0  # chunks already arrive ~every few seconds from client
GEMINI_MIN_INTERVAL_SECONDS = 8.0  # keeps the optional free-tier API usage modest
AI_VOICE_USERNAME = os.environ.get("AI_VOICE_USERNAME", "scammer").strip().lower()


@dataclass
class Participant:
    username: str
    ws: WebSocket
    joined_at: float = field(default_factory=time.time)
    muted: bool = False


@dataclass
class Room:
    room_id: str
    participants: dict[str, Participant] = field(default_factory=dict)
    transcript: list[dict] = field(default_factory=list)  # {speaker, text, ts}
    max_risk_confidence: int = 0
    max_risk_level: str = "LOW"
    all_reasons: set[str] = field(default_factory=set)
    call_start: float = field(default_factory=time.time)
    voice_results: dict[str, dict] = field(default_factory=dict)  # last result per user
    last_gemini_request_at: float = 0.0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    ended: bool = False


rooms: dict[str, Room] = {}


def get_or_create_room(room_id: str) -> Room:
    if room_id not in rooms:
        rooms[room_id] = Room(room_id=room_id)
    return rooms[room_id]


async def broadcast(room: Room, message: dict, exclude: str | None = None):
    dead = []
    for uname, p in room.participants.items():
        if uname == exclude:
            continue
        try:
            await p.ws.send_text(json.dumps(message))
        except Exception:
            dead.append(uname)
    for uname in dead:
        room.participants.pop(uname, None)


async def send_to(room: Room, username: str, message: dict):
    p = room.participants.get(username)
    if p:
        try:
            await p.ws.send_text(json.dumps(message))
        except Exception:
            pass


def other_participant(room: Room, username: str) -> str | None:
    for uname in room.participants:
        if uname != username:
            return uname
    return None


async def _warmup_model(fn, name: str):
    try:
        await run_in_threadpool(fn)
    except Exception as exc:
        log.warning("%s warm-up failed; it will retry on first audio: %s", name, exc)


async def _warmup_models():
    await asyncio.gather(
        _warmup_model(stt.warm_up, "STT"),
        _warmup_model(voice_detector.warm_up, "voice classifier"),
    )


async def handle_audio_chunk(room: Room, username: str, raw_bytes: bytes):
    """Runs STT + voice-detection on one recorded chunk from `username`,
    then re-scores the whole-room transcript for scam risk, and broadcasts
    updates to both participants."""

    async with room.lock:
        if room.ended:
            return

        log.info("[%s] received audio chunk from %s (%d bytes)", room.room_id, username, len(raw_bytes))

        # The physical microphone is treated as human for the jury demo. Run
        # the classifier only for the explicitly named MP3/virtual-cable user.
        stt_task = run_in_threadpool(stt.transcribe_chunk, raw_bytes)
        voice_task = (run_in_threadpool(voice_detector.analyze_chunk, raw_bytes, username)
                      if username.strip().lower() == AI_VOICE_USERNAME else asyncio.sleep(0, result=None))
        text, voice_result = await asyncio.gather(stt_task, voice_task)

        if not text:
            log.info("[%s] chunk from %s: no speech detected (silence, or below VAD threshold)", room.room_id, username)
        else:
            log.info("[%s] chunk from %s transcribed: %r", room.room_id, username, text)

        if voice_result is None:
            log.info("[%s] voice analysis produced no inference result for %s; see [AI VOICE] diagnostics above", room.room_id, username)

        if voice_result is not None:
            room.voice_results[username] = voice_result
            await broadcast(
                room,
                {
                    "type": "voice-update",
                    "speaker": username,
                    "result": voice_result,
                },
            )

        if text:
            entry = {"speaker": username, "text": text, "ts": time.time()}
            room.transcript.append(entry)
            await broadcast(room, {"type": "transcript-update", "entry": entry})

            lines = [f"USER {e['speaker']}: {e['text']}" for e in room.transcript]
            use_gemini = time.monotonic() - room.last_gemini_request_at >= GEMINI_MIN_INTERVAL_SECONDS
            if use_gemini:
                room.last_gemini_request_at = time.monotonic()
            result = await run_in_threadpool(scam_detector.analyze_transcript, lines, use_gemini)

            if result.confidence > room.max_risk_confidence:
                room.max_risk_confidence = result.confidence
                room.max_risk_level = result.risk_level
            room.all_reasons.update(result.reasons)

            await broadcast(
                room,
                {
                    "type": "risk-update",
                    "risk_level": result.risk_level,
                    "confidence": result.confidence,
                    "reasons": result.reasons,
                    "backend": result.backend,
                },
            )


DEMO_SCRIPTS = {
    "scam": [
        ("Caller", "Hello sir, I'm calling from your bank's customer care department."),
        ("Victim", "Oh okay, why are you calling?"),
        ("Caller", "Your account has been flagged for suspicious activity and will be blocked immediately."),
        ("Victim", "What? What do I need to do?"),
        ("Caller", "Don't worry, just give me the OTP you just received so we can verify your identity right now."),
        ("Victim", "Um, okay, one second."),
        ("Caller", "Please hurry, this is urgent, your account will be suspended within minutes."),
    ],
    "normal": [
        ("User A", "Hey, are we still meeting at 5?"),
        ("User B", "Yeah, I'll be there. Want me to grab coffee on the way?"),
        ("User A", "Sure, that'd be great, thanks!"),
        ("User B", "No problem, see you soon."),
    ],
}


async def run_demo(room: Room, scenario: str):
    script = DEMO_SCRIPTS.get(scenario, DEMO_SCRIPTS["scam"])
    async with room.lock:
        for speaker, text in script:
            entry = {"speaker": speaker, "text": text, "ts": time.time()}
            room.transcript.append(entry)
            await broadcast(room, {"type": "transcript-update", "entry": entry})

            lines = [f"{e['speaker']}: {e['text']}" for e in room.transcript]
            result = await run_in_threadpool(scam_detector.analyze_transcript, lines, False)
            if result.confidence > room.max_risk_confidence:
                room.max_risk_confidence = result.confidence
                room.max_risk_level = result.risk_level
            room.all_reasons.update(result.reasons)

            await broadcast(
                room,
                {
                    "type": "risk-update",
                    "risk_level": result.risk_level,
                    "confidence": result.confidence,
                    "reasons": result.reasons,
                    "backend": result.backend,
                },
            )
            await asyncio.sleep(1.4)


@app.websocket("/ws/{room_id}/{username}")
async def ws_endpoint(websocket: WebSocket, room_id: str, username: str):
    await websocket.accept()
    room = get_or_create_room(room_id)

    if len(room.participants) >= MAX_PARTICIPANTS and username not in room.participants:
        await websocket.send_text(json.dumps({"type": "room-full"}))
        await websocket.close()
        return

    room.participants[username] = Participant(username=username, ws=websocket)
    room.ended = False

    # Warm the models on entry without delaying the room's joined message.
    asyncio.create_task(_warmup_models())

    await broadcast(
        room,
        {
            "type": "participants-update",
            "participants": list(room.participants.keys()),
        },
    )
    # tell the newly joined client who else is here (so it knows whether to
    # initiate the WebRTC offer)
    other = other_participant(room, username)
    await websocket.send_text(json.dumps({
        "type": "joined",
        "you": username,
        "participants": list(room.participants.keys()),
        "should_initiate": other is not None,
        "peer": other,
    }))
    if other:
        await send_to(room, other, {"type": "peer-joined", "peer": username})

    try:
        while True:
            message = await websocket.receive()

            if message["type"] == "websocket.disconnect":
                break

            if "text" in message and message["text"] is not None:
                try:
                    data = json.loads(message["text"])
                except json.JSONDecodeError:
                    continue

                mtype = data.get("type")

                if mtype in ("webrtc-offer", "webrtc-answer", "webrtc-ice"):
                    target = data.get("target") or other_participant(room, username)
                    if target:
                        payload = dict(data)
                        payload["from"] = username
                        await send_to(room, target, payload)

                elif mtype == "mute-status":
                    p = room.participants.get(username)
                    if p:
                        p.muted = bool(data.get("muted"))
                    await broadcast(
                        room,
                        {"type": "peer-mute-status", "username": username, "muted": p.muted if p else False},
                        exclude=username,
                    )

                elif mtype == "start-demo":
                    scenario = data.get("scenario", "scam")
                    asyncio.create_task(run_demo(room, scenario))

                elif mtype == "end-call":
                    room.ended = True
                    await broadcast(room, {
                        "type": "call-ended",
                        "summary": build_summary(room),
                    })

            elif "bytes" in message and message["bytes"] is not None:
                asyncio.create_task(handle_audio_chunk(room, username, message["bytes"]))

    except WebSocketDisconnect:
        pass
    finally:
        room.participants.pop(username, None)
        await broadcast(room, {
            "type": "participants-update",
            "participants": list(room.participants.keys()),
        })
        await broadcast(room, {"type": "peer-left", "peer": username})
        if not room.participants:
            # keep transcript around briefly in case of summary fetch, but
            # stop counting the call as active
            room.ended = True


def build_summary(room: Room) -> dict:
    duration = int(time.time() - room.call_start)
    return {
        "duration_seconds": duration,
        "max_risk_level": room.max_risk_level,
        "max_risk_confidence": room.max_risk_confidence,
        "suspicious_events": sorted(room.all_reasons),
        "transcript": room.transcript,
        "voice_results": room.voice_results,
    }


@app.get("/api/summary/{room_id}")
async def api_summary(room_id: str):
    room = rooms.get(room_id)
    if not room:
        return {"error": "not found"}
    return build_summary(room)


@app.get("/api/health")
async def health():
    return {"status": "ok"}


app.mount("/", StaticFiles(directory="static", html=True), name="static")
