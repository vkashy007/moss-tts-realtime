#!/usr/bin/env python3
"""
MOSS-TTS Real-time Multi-User Streaming Server
Handles concurrent users with voice cloning and low-latency streaming.
"""
import asyncio
import io
import json
import logging
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Dict, List
import uuid

import numpy as np
import torch
import torchaudio
from scipy.io import wavfile
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ============================================================================
# Data Models
# ============================================================================

@dataclass
class VoiceSample:
    """Voice sample metadata."""
    voice_id: str
    name: str
    language: str
    file_path: Path
    duration: float
    cached: bool = False
    tokens: Optional[np.ndarray] = None


@dataclass
class Session:
    """TTS generation session."""
    session_id: str
    voice_sample: VoiceSample
    text_buffer: str = ""
    is_active: bool = True
    created_at: float = 0.0
    total_audio_generated: float = 0.0


# ============================================================================
# Request/Response Models
# ============================================================================

class StartSessionRequest(BaseModel):
    voice_id: str
    language: str = "en"
    temperature: float = 1.0
    top_p: float = 0.8
    top_k: int = 25


class StartSessionResponse(BaseModel):
    session_id: str
    voice_id: str
    ready: bool


class PushTextRequest(BaseModel):
    text: str


class PushTextResponse(BaseModel):
    session_id: str
    text_received: bool
    queued_tokens: int


class CloseSessionResponse(BaseModel):
    session_id: str
    closed: bool
    total_audio_duration: float


# ============================================================================
# Voice Sample Cache (LRU)
# ============================================================================

class VoiceCache:
    """LRU cache for voice samples in GPU memory."""

    def __init__(self, max_size: int = 16):
        self.cache: OrderedDict[str, np.ndarray] = OrderedDict()
        self.max_size = max_size
        self.hits = 0
        self.misses = 0

    def get(self, voice_id: str, loader_fn) -> np.ndarray:
        """Get voice tokens from cache or load from disk."""
        if voice_id in self.cache:
            self.cache.move_to_end(voice_id)  # Mark as recently used
            self.hits += 1
            logger.info(f"Cache hit for {voice_id} (hit rate: {self.hit_rate():.1%})")
            return self.cache[voice_id]

        # Load from disk
        logger.info(f"Cache miss for {voice_id}, loading from disk")
        self.misses += 1
        tokens = loader_fn(voice_id)

        # Evict LRU if cache full
        if len(self.cache) >= self.max_size:
            evicted, _ = self.cache.popitem(last=False)
            logger.info(f"Evicted {evicted} from cache (size={len(self.cache)})")

        self.cache[voice_id] = tokens
        return tokens

    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return self.hits / total if total > 0 else 0.0


# ============================================================================
# Request Queue
# ============================================================================

class RequestQueue:
    """FIFO queue with max size and timeout."""

    def __init__(self, max_size: int = 100, timeout: float = 5.0):
        self.queue = asyncio.Queue(maxsize=max_size)
        self.timeout = timeout
        self.total_requests = 0
        self.rejected_requests = 0

    async def enqueue(self, request) -> bool:
        """Enqueue request with timeout."""
        try:
            await asyncio.wait_for(
                self.queue.put(request),
                timeout=self.timeout
            )
            self.total_requests += 1
            logger.info(f"Request queued (queue size: {self.queue.qsize()})")
            return True
        except asyncio.TimeoutError:
            self.rejected_requests += 1
            logger.warning(f"Request rejected - queue full (rejected: {self.rejected_requests})")
            return False

    async def dequeue(self):
        """Dequeue next request."""
        return await self.queue.get()

    def size(self) -> int:
        return self.queue.qsize()

    def metrics(self) -> dict:
        return {
            "total_requests": self.total_requests,
            "rejected_requests": self.rejected_requests,
            "current_queue_size": self.size(),
            "rejection_rate": self.rejected_requests / max(1, self.total_requests)
        }


# ============================================================================
# Voice Sample Manager
# ============================================================================

class VoiceSampleManager:
    """Manages voice sample loading and caching."""

    def __init__(self, voice_dir: Path, manifest_file: Path):
        self.voice_dir = voice_dir
        self.manifest_file = manifest_file
        self.voices: Dict[str, VoiceSample] = {}
        self.load_manifest()

    def load_manifest(self):
        """Load voice manifest."""
        if not self.manifest_file.exists():
            logger.warning(f"Manifest not found: {self.manifest_file}")
            return

        with open(self.manifest_file) as f:
            data = json.load(f)

        for voice_data in data.get("voices", []):
            voice = VoiceSample(
                voice_id=voice_data["id"],
                name=voice_data["name"],
                language=voice_data["language"],
                file_path=self.voice_dir / voice_data["file"],
                duration=voice_data["duration"]
            )
            self.voices[voice.voice_id] = voice
            logger.info(f"Loaded voice: {voice.voice_id} ({voice.name})")

    def get_voice(self, voice_id: str) -> VoiceSample:
        """Get voice sample by ID."""
        if voice_id not in self.voices:
            raise ValueError(f"Voice not found: {voice_id}")
        return self.voices[voice_id]

    def list_voices(self) -> List[Dict]:
        """List all available voices."""
        return [
            {
                "id": v.voice_id,
                "name": v.name,
                "language": v.language,
                "duration": v.duration
            }
            for v in self.voices.values()
        ]


# ============================================================================
# Main Server
# ============================================================================

class RealtimeTTSServer:
    """Multi-user TTS streaming server with MOSS-TTS voice cloning."""

    def __init__(
        self,
        voice_dir: Path = Path("/server/voice_samples"),
        manifest_file: Path = Path("/server/voice_samples/manifest.json"),
        max_concurrent_sessions: int = 30,
        max_queue_size: int = 100,
        voice_cache_size: int = 16,
        enable_tts: bool = True
    ):
        self.voice_dir = voice_dir
        self.voice_manager = VoiceSampleManager(voice_dir, manifest_file)
        self.voice_cache = VoiceCache(max_size=voice_cache_size)
        self.request_queue = RequestQueue(max_size=max_queue_size)

        self.sessions: Dict[str, Session] = {}
        self.max_concurrent_sessions = max_concurrent_sessions

        # MOSS-TTS model components
        self.model = None
        self.processor = None
        self.codec = None
        self.device = None
        self.sample_rate = 24000

        if enable_tts:
            self._load_tts_models()

        logger.info("RealtimeTTSServer initialized")

    def _load_tts_models(self):
        """Load MOSS-TTS models (1.7B + codec) with proper inference pipeline."""
        try:
            logger.info("Loading MOSS-TTS models...")

            if not torch.cuda.is_available():
                logger.warning("CUDA not available, falling back to CPU (slow)")
                self.device = torch.device("cpu")
            else:
                self.device = torch.device("cuda:0")

            # Add MOSS-TTS to path
            import sys
            sys.path.insert(0, '/home/vk/aiapps/tts/moss/moss_tts_realtime')

            from transformers import AutoModel, AutoTokenizer
            from mossttsrealtime import MossTTSRealtime
            from inferencer import MossTTSRealtimeInference, MossTTSRealtimeProcessor

            model_path = "OpenMOSS-Team/MOSS-TTS-Realtime"
            logger.info(f"Downloading {model_path}...")

            # Load model
            dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float32
            self.model = MossTTSRealtime.from_pretrained(
                model_path,
                torch_dtype=dtype,
                device_map="auto"
            )
            self.model.eval()
            logger.info("✅ MOSS-TTS model loaded")

            # Load tokenizer and processor
            tokenizer = AutoTokenizer.from_pretrained(model_path)
            processor = MossTTSRealtimeProcessor(tokenizer=tokenizer)
            logger.info("✅ Processor loaded")

            # Load audio codec
            codec_path = "OpenMOSS-Team/MOSS-Audio-Tokenizer"
            logger.info("Loading audio codec...")
            codec = AutoModel.from_pretrained(codec_path, trust_remote_code=True).eval()
            codec = codec.to(self.device)
            logger.info("✅ Codec loaded")

            # Create inference pipeline
            self.inference = MossTTSRealtimeInference(
                model=self.model,
                tokenizer=tokenizer,
                processor=processor,
                codec=codec,
                codec_sample_rate=24000
            )

            logger.info("✅ MOSS-TTS inference pipeline ready")

        except Exception as e:
            logger.error(f"Failed to load MOSS-TTS models: {e}")
            import traceback
            traceback.print_exc()
            logger.warning("Falling back to silence generation (demo mode)")
            self.model = None
            self.inference = None

    async def start_session(self, request: StartSessionRequest) -> StartSessionResponse:
        """Start a new TTS session."""
        if len(self.sessions) >= self.max_concurrent_sessions:
            raise HTTPException(
                status_code=503,
                detail=f"Too many concurrent sessions ({len(self.sessions)})"
            )

        try:
            voice = self.voice_manager.get_voice(request.voice_id)
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))

        session_id = f"sess_{uuid.uuid4().hex[:8]}"
        session = Session(
            session_id=session_id,
            voice_sample=voice,
            created_at=asyncio.get_event_loop().time()
        )

        self.sessions[session_id] = session
        logger.info(f"Session started: {session_id} (voices: {request.voice_id})")

        return StartSessionResponse(
            session_id=session_id,
            voice_id=request.voice_id,
            ready=True
        )

    async def push_text(
        self,
        session_id: str,
        text: str
    ) -> PushTextResponse:
        """Push text for generation in a session."""
        if session_id not in self.sessions:
            raise HTTPException(status_code=404, detail=f"Session not found: {session_id}")

        session = self.sessions[session_id]
        session.text_buffer += text

        # Count approximate tokens (rough heuristic)
        token_count = len(session.text_buffer) // 4

        logger.info(f"Text pushed to {session_id} ({len(text)} chars, ~{token_count} tokens)")

        return PushTextResponse(
            session_id=session_id,
            text_received=True,
            queued_tokens=token_count
        )

    async def stream_audio_generator(self, session_id: str):
        """Generator for streaming audio chunks using MOSS-TTS voice cloning."""
        if session_id not in self.sessions:
            raise HTTPException(status_code=404, detail=f"Session not found: {session_id}")

        session = self.sessions[session_id]

        try:
            if self.model is None or self.inference is None:
                logger.warning("MOSS-TTS model not loaded, generating silence")
                # Fallback: generate silence
                chunk_duration = 0.1
                chunk_samples = int(self.sample_rate * chunk_duration)
                for _ in range(int(session.voice_sample.duration / chunk_duration)):
                    chunk = np.zeros(chunk_samples, dtype=np.int16)
                    buffer = io.BytesIO()
                    wavfile.write(buffer, self.sample_rate, chunk)
                    buffer.seek(0)
                    yield buffer.getvalue()
                    await asyncio.sleep(chunk_duration * 0.5)
                return

            voice_path = session.voice_sample.file_path
            text = session.text_buffer.strip()
            if not text:
                text = "Hello, this is a test."

            logger.info(f"🎤 Generating speech for: {session.voice_sample.voice_id}")
            logger.info(f"📝 Text: {text[:100]}...")

            # Use the inference object's generate method directly with voice cloning
            logger.info("🎯 Starting MOSS-TTS generation with voice cloning...")
            with torch.no_grad():
                generated_tokens = self.inference.generate(
                    text=text,
                    reference_audio_path=str(voice_path),
                    max_length=2048,
                    temperature=1.0,
                    top_p=0.8,
                    top_k=25,
                    do_sample=True
                )

            logger.info(f"✅ Audio tokens generated: {len(generated_tokens)} tokens")

            # Decode tokens to waveform
            logger.info("🔀 Decoding tokens to waveform...")
            with torch.no_grad():
                # generated_tokens should be a list of token arrays or a tensor
                if isinstance(generated_tokens, list) and len(generated_tokens) > 0:
                    audio_tokens = torch.tensor(generated_tokens[0], device=self.inference.device)
                else:
                    audio_tokens = generated_tokens

                audio_waveform = self.inference.codec.decode(
                    {"audio_codes": audio_tokens.unsqueeze(0).unsqueeze(0)}
                )

            audio_np = audio_waveform.cpu().numpy()
            if audio_np.ndim > 1:
                audio_np = audio_np[0]
            if audio_np.ndim > 1:
                audio_np = audio_np[0]

            # Stream in chunks
            chunk_duration = 0.1  # 100ms chunks
            chunk_samples = int(self.sample_rate * chunk_duration)
            total_samples = len(audio_np)
            total_duration = total_samples / self.sample_rate

            logger.info(f"▶️ Streaming {total_duration:.2f}s of audio in 100ms chunks")
            session.total_audio_generated = total_duration

            for i in range(0, total_samples, chunk_samples):
                chunk = audio_np[i:i + chunk_samples]
                if len(chunk) == 0:
                    break

                # Normalize and convert to int16
                chunk_max = np.max(np.abs(chunk)) + 1e-6
                chunk_normalized = chunk / chunk_max
                chunk_int16 = np.int16(chunk_normalized * 32767)

                # Write to buffer and yield
                buffer = io.BytesIO()
                wavfile.write(buffer, self.sample_rate, chunk_int16)
                buffer.seek(0)

                yield buffer.getvalue()

                # Stream at realtime speed or faster
                await asyncio.sleep(chunk_duration * 0.8)

            logger.info(f"✅ Audio generation complete: {total_duration:.2f}s")

        except Exception as e:
            logger.error(f"❌ Error generating audio: {e}")
            import traceback
            traceback.print_exc()
            # Generate fallback silence on error
            chunk_samples = int(self.sample_rate * 0.1)
            chunk = np.zeros(chunk_samples, dtype=np.int16)
            buffer = io.BytesIO()
            wavfile.write(buffer, self.sample_rate, chunk)
            buffer.seek(0)
            yield buffer.getvalue()

    async def close_session(self, session_id: str) -> CloseSessionResponse:
        """Close a session."""
        if session_id not in self.sessions:
            raise HTTPException(status_code=404, detail=f"Session not found: {session_id}")

        session = self.sessions[session_id]
        session.is_active = False

        del self.sessions[session_id]
        logger.info(f"Session closed: {session_id}")

        return CloseSessionResponse(
            session_id=session_id,
            closed=True,
            total_audio_duration=session.total_audio_generated
        )

    def get_metrics(self) -> dict:
        """Get server metrics."""
        return {
            "active_sessions": len(self.sessions),
            "max_concurrent_sessions": self.max_concurrent_sessions,
            "voice_cache_hit_rate": self.voice_cache.hit_rate(),
            "voice_cache_size": len(self.voice_cache.cache),
            "request_queue": self.request_queue.metrics()
        }


# ============================================================================
# FastAPI Application
# ============================================================================

app = FastAPI(title="MOSS-TTS Real-time Streaming Server")

# Enable CORS for all origins (necessary for web clients)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

server = RealtimeTTSServer()


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {
        "status": "ok",
        "active_sessions": len(server.sessions),
        "metrics": server.get_metrics()
    }


@app.get("/voices")
async def list_voices():
    """List available voices."""
    return {
        "voices": server.voice_manager.list_voices()
    }


@app.post("/tts/session/start")
async def start_session(request: StartSessionRequest):
    """Start a new TTS session."""
    return await server.start_session(request)


@app.post("/tts/session/{session_id}/push")
async def push_text(session_id: str, request: PushTextRequest):
    """Push text to generate."""
    return await server.push_text(session_id, request.text)


@app.get("/tts/session/{session_id}/audio")
async def stream_audio(session_id: str):
    """Stream generated audio."""
    return StreamingResponse(
        server.stream_audio_generator(session_id),
        media_type="audio/wav"
    )


@app.post("/tts/session/{session_id}/close")
async def close_session(session_id: str):
    """Close a session."""
    return await server.close_session(session_id)


@app.get("/metrics")
async def get_metrics():
    """Get server metrics."""
    return server.get_metrics()


@app.get("/web_interface.html")
async def serve_simple_interface():
    """Serve simple single-user web interface."""
    try:
        with open("web_interface.html", "r") as f:
            content = f.read()
        from fastapi.responses import HTMLResponse
        return HTMLResponse(content=content)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Web interface not found")


@app.get("/web_interface_multi.html")
async def serve_multi_interface():
    """Serve multi-user web interface."""
    try:
        with open("web_interface_multi.html", "r") as f:
            content = f.read()
        from fastapi.responses import HTMLResponse
        return HTMLResponse(content=content)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Web interface not found")


# ============================================================================
# Usage
# ============================================================================

if __name__ == "__main__":
    import uvicorn

    # Initialize server with voice samples
    server = RealtimeTTSServer(
        voice_dir=Path("/home/vk/voice_samples"),
        manifest_file=Path("/home/vk/voice_samples/manifest.json"),
        max_concurrent_sessions=30,
        max_queue_size=100,
        voice_cache_size=16
    )

    logger.info("Starting MOSS-TTS Real-time Streaming Server")
    logger.info(f"Voice samples: /home/vk/voice_samples/")
    logger.info(f"Available voices: {len(server.voice_manager.voices)}")
    logger.info("Available endpoints:")
    logger.info("  GET  /health - Health check")
    logger.info("  GET  /voices - List voices")
    logger.info("  POST /tts/session/start - Start session")
    logger.info("  POST /tts/session/{id}/push - Push text")
    logger.info("  GET  /tts/session/{id}/audio - Stream audio")
    logger.info("  POST /tts/session/{id}/close - Close session")
    logger.info("  GET  /metrics - Server metrics")

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8002,
        workers=1,
        access_log=True
    )
