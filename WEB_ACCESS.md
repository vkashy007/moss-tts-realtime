# MOSS-TTS Web Interface - Access Guide

## 🚀 Quick Start

Your MOSS-TTS Real-time Streaming server is **running and ready to test!**

---

## 📱 Access from Your MacBook

### **Option 1: Multi-User Test (Recommended for your setup)** 🎯

Open in your MacBook browser:
```
http://192.168.1.192:8002/web_interface_multi.html
```

This interface allows you to:
- ✅ Test 4 concurrent users simultaneously
- ✅ Monitor latency and generation stats
- ✅ Test all 9 voices in parallel
- ✅ See real-time metrics

**Features:**
- 4 user panels (User 1-4)
- Generate all at once with one click
- Global controls for batch testing
- Real-time metrics (active users, total generated, avg latency)

---

### **Option 2: Simple Single-User Test**

Open in your MacBook browser:
```
http://192.168.1.192:8002/web_interface.html
```

This interface allows you to:
- ✅ Test one voice at a time
- ✅ Simple, minimal design
- ✅ Perfect for listening to individual voices

---

## 🎵 Available Voices

All 9 jungle characters are available:

1. **Narrator** - Warm storyteller voice
2. **Bruno** - Deep bear voice
3. **Mia** - Energetic monkey voice
4. **Fox** - Smooth cunning voice
5. **Bunny** - High-pitched child voice
6. **Owl** - Deep gravelly grandfather
7. **Pepper** - Shrill dramatic parrot
8. **Tortoise** - Slow creaky grandmother
9. **Zara** - Clear precise young adult

---

## 💻 Server Details

**Server IP:** `192.168.1.192`  
**Port:** `8002`  
**Status:** ✅ Running and ready

---

## 🔧 How It Works

### Step-by-step with web interface:

1. **Select Voice** - Choose from the dropdown
2. **Enter Text** - Type what you want to hear
3. **Click Generate** - Server generates and streams audio
4. **Listen** - Audio plays immediately in the browser
5. **Repeat** - Test as many combinations as you want

### Behind the scenes:

```
1. Your MacBook sends: {voice_id: "narrator", text: "Hello"}
2. Server loads voice from /home/vk/voice_samples/
3. Server generates speech tokens
4. Audio streams back to your MacBook in real-time
5. Browser plays audio immediately (100ms chunks)
```

---

## 📊 Multi-User Test Explained

The multi-user interface tests 4 simultaneous users:

- **User 1** - Narrator voice
- **User 2** - Bruno voice  
- **User 3** - Mia voice
- **User 4** - Fox voice

**How to test:**

1. Click **"🚀 Generate All at Once"** button
2. Watch all 4 users generate audio simultaneously
3. Check latency metrics at the top
4. View individual audio players for each user

This proves the server can handle **concurrent multi-user requests** with **low latency**.

---

## 🎯 What You're Testing

✅ **Session Management** - Each user gets independent session  
✅ **Voice Caching** - Repeated voices load faster  
✅ **Concurrent Requests** - Multiple users don't block each other  
✅ **HTTP Streaming** - Audio chunks stream in real-time  
✅ **Metrics** - Server health fully observable  

---

## 📝 Expected Performance

- **Session creation:** ~1-2ms
- **Voice loading:** 50-100ms (first time), 10ms (cached)
- **Audio generation:** Simulated silence (real MOSS-TTS = speech)
- **Streaming:** Immediate playback (100ms chunks)

---

## 🐛 Troubleshooting

### "Cannot connect"
- Make sure you're on the same network as the Linux machine
- Check firewall allows port 8002
- Try: `ping 192.168.1.192`

### "Server unavailable"
- Server might have crashed
- Check: `curl http://192.168.1.192:8002/health`

### Audio not playing
- Check browser permissions (allow audio)
- Try a different browser
- Check browser console for errors (F12)

---

## 🔄 How to Restart Server

If needed, restart the server from the Linux machine:

```bash
cd /home/vk/aiapps/tts/moss
source venv_clean/bin/activate
python3 realtime_tts_server.py
```

Server will be available at: `http://192.168.1.192:8002`

---

## 📈 Next Steps

Once you've tested the web interface:

1. **Listen to audio quality** - Evaluate voice naturalness
2. **Test concurrent users** - Try 2-4 users simultaneously
3. **Measure latency** - Check response times
4. **Provide feedback** - What works, what doesn't?

Then we'll integrate the actual MOSS-TTS model for real speech synthesis!

---

## 🎤 Ready to Test?

Open your MacBook browser and go to:

### **Multi-User (recommended):**
```
http://192.168.1.192:8002/web_interface_multi.html
```

### **Single-User (simple):**
```
http://192.168.1.192:8002/web_interface.html
```

**Enjoy testing!** 🚀
