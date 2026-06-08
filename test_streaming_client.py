#!/usr/bin/env python3
"""
Test client for MOSS-TTS Real-time Streaming Server
Tests multi-user concurrent sessions with voice cloning.
"""
import requests
import json
import time
import asyncio
from pathlib import Path

BASE_URL = "http://localhost:8001"

def test_voices_available():
    """Test 1: Get available voices."""
    print("\n" + "="*80)
    print("TEST 1: List Available Voices")
    print("="*80)

    resp = requests.get(f"{BASE_URL}/voices")
    voices = resp.json()["voices"]

    print(f"\n✓ Found {len(voices)} voices:")
    for voice in voices:
        print(f"  - {voice['id']:12} ({voice['name']:10}) — {voice['duration']}s")

    return voices

def test_single_session(voice_id: str, text: str):
    """Test 2: Single session with streaming."""
    print(f"\n" + "="*80)
    print(f"TEST 2: Single Session — Voice: {voice_id}, Text: '{text}'")
    print("="*80)

    # Start session
    print(f"\n1. Starting session with voice '{voice_id}'...")
    resp = requests.post(f"{BASE_URL}/tts/session/start", json={
        "voice_id": voice_id,
        "language": "en"
    })
    session_data = resp.json()
    session_id = session_data["session_id"]
    print(f"   ✓ Session created: {session_id}")

    # Push text
    print(f"\n2. Pushing text: '{text}'")
    resp = requests.post(f"{BASE_URL}/tts/session/{session_id}/push", json={
        "text": text
    })
    push_data = resp.json()
    print(f"   ✓ Text queued ({push_data['queued_tokens']} tokens)")

    # Stream audio
    print(f"\n3. Streaming audio...")
    start_time = time.time()
    resp = requests.get(f"{BASE_URL}/tts/session/{session_id}/audio", stream=True)

    chunks_received = 0
    audio_data = b""
    for chunk in resp.iter_content(chunk_size=4096):
        if chunk:
            chunks_received += 1
            audio_data += chunk

    elapsed = time.time() - start_time
    print(f"   ✓ Received {chunks_received} chunks ({len(audio_data)} bytes) in {elapsed:.2f}s")

    # Save audio
    output_path = f"/tmp/tts_test_{voice_id}.wav"
    with open(output_path, "wb") as f:
        f.write(audio_data)
    print(f"   ✓ Saved to {output_path}")

    # Close session
    print(f"\n4. Closing session...")
    resp = requests.post(f"{BASE_URL}/tts/session/{session_id}/close")
    close_data = resp.json()
    print(f"   ✓ Session closed (duration: {close_data['total_audio_duration']:.2f}s)")

    return session_id

def test_concurrent_sessions():
    """Test 3: Concurrent sessions (multi-user)."""
    print(f"\n" + "="*80)
    print("TEST 3: Concurrent Sessions (3 users simultaneously)")
    print("="*80)

    test_cases = [
        ("narrator", "Once upon a time in a magical forest."),
        ("bruno", "I love honey so much!"),
        ("mia", "This is so fun and exciting!"),
    ]

    # Start all sessions
    print(f"\n1. Starting {len(test_cases)} sessions...")
    sessions = []
    for voice_id, text in test_cases:
        resp = requests.post(f"{BASE_URL}/tts/session/start", json={
            "voice_id": voice_id,
            "language": "en"
        })
        session_id = resp.json()["session_id"]
        sessions.append((session_id, voice_id, text))
        print(f"   ✓ {voice_id:12} → {session_id}")

    # Push text to all
    print(f"\n2. Pushing text to all sessions...")
    for session_id, voice_id, text in sessions:
        requests.post(f"{BASE_URL}/tts/session/{session_id}/push", json={
            "text": text
        })
        print(f"   ✓ {voice_id:12} → queued")

    # Stream audio from all (sequentially for simplicity)
    print(f"\n3. Streaming audio from all sessions...")
    total_audio = 0
    for session_id, voice_id, text in sessions:
        resp = requests.get(f"{BASE_URL}/tts/session/{session_id}/audio", stream=True)
        audio_data = b""
        for chunk in resp.iter_content(chunk_size=4096):
            if chunk:
                audio_data += chunk
        total_audio += len(audio_data)
        print(f"   ✓ {voice_id:12} → {len(audio_data)} bytes")

    # Close all
    print(f"\n4. Closing all sessions...")
    for session_id, voice_id, text in sessions:
        requests.post(f"{BASE_URL}/tts/session/{session_id}/close")
        print(f"   ✓ {voice_id:12} closed")

    print(f"\nTotal audio generated: {total_audio} bytes")

def test_voice_cache():
    """Test 4: Voice sample caching (repeated voice loads faster)."""
    print(f"\n" + "="*80)
    print("TEST 4: Voice Cache Performance")
    print("="*80)

    voice_id = "narrator"
    text = "Testing voice cache performance."

    # First session (cache miss)
    print(f"\n1. First session (cache miss)...")
    start = time.time()
    resp = requests.post(f"{BASE_URL}/tts/session/start", json={
        "voice_id": voice_id,
        "language": "en"
    })
    session1 = resp.json()["session_id"]
    time1 = time.time() - start
    print(f"   ✓ Session started in {time1*1000:.2f}ms")

    requests.post(f"{BASE_URL}/tts/session/{session1}/push", json={"text": text})
    list(requests.get(f"{BASE_URL}/tts/session/{session1}/audio", stream=True).iter_content(4096))
    requests.post(f"{BASE_URL}/tts/session/{session1}/close")

    # Second session (cache hit)
    print(f"\n2. Second session (cache hit)...")
    start = time.time()
    resp = requests.post(f"{BASE_URL}/tts/session/start", json={
        "voice_id": voice_id,
        "language": "en"
    })
    session2 = resp.json()["session_id"]
    time2 = time.time() - start
    print(f"   ✓ Session started in {time2*1000:.2f}ms")

    requests.post(f"{BASE_URL}/tts/session/{session2}/push", json={"text": text})
    list(requests.get(f"{BASE_URL}/tts/session/{session2}/audio", stream=True).iter_content(4096))
    requests.post(f"{BASE_URL}/tts/session/{session2}/close")

    speedup = time1 / time2
    print(f"\n✓ Cache speedup: {speedup:.2f}x faster")

def test_metrics():
    """Test 5: Server metrics."""
    print(f"\n" + "="*80)
    print("TEST 5: Server Metrics")
    print("="*80)

    resp = requests.get(f"{BASE_URL}/metrics")
    metrics = resp.json()

    print("\nServer Metrics:")
    print(f"  Active sessions: {metrics['active_sessions']}")
    print(f"  Max concurrent: {metrics['max_concurrent_sessions']}")
    print(f"  Voice cache hit rate: {metrics['voice_cache_hit_rate']:.1%}")
    print(f"  Voice cache size: {metrics['voice_cache_size']}")

    queue = metrics['request_queue']
    print(f"\nRequest Queue:")
    print(f"  Total requests: {queue['total_requests']}")
    print(f"  Rejected: {queue['rejected_requests']}")
    print(f"  Current queue size: {queue['current_queue_size']}")
    print(f"  Rejection rate: {queue['rejection_rate']:.1%}")

def main():
    """Run all tests."""
    print("\n" + "🎵"*40)
    print("MOSS-TTS Real-time Streaming Server Tests".center(80))
    print("🎵"*40)

    try:
        # Test 1: Available voices
        voices = test_voices_available()

        # Test 2: Single session
        test_single_session("narrator", "Hello, this is a test of real-time voice cloning!")

        # Test 3: Concurrent sessions
        test_concurrent_sessions()

        # Test 4: Voice cache
        test_voice_cache()

        # Test 5: Metrics
        test_metrics()

        print(f"\n" + "="*80)
        print("✅ ALL TESTS COMPLETED SUCCESSFULLY".center(80))
        print("="*80 + "\n")

    except Exception as e:
        print(f"\n❌ ERROR: {e}\n")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
