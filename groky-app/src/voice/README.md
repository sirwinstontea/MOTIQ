# voice

Realtime conversation with Grok Voice.

| File | Purpose |
|---|---|
| `session.ts` | `VoiceSession`: connection, audio, tool calls and transcripts |
| `prompt.ts` | System prompt and app tools: `propose_plan`, look, skill and full access tools |
| `token.ts` | Gets a session token from the token server, or from the xAI key if none is set |
| `transcription.ts` | Input transcription config, which lets the UI and approval see what was said |
| `pcm.ts` | PCM helpers: base64, WAV, level, resampling |
| `player.ts` | Native playback: cuts streamed PCM into WAV segments, because Expo Go has no raw PCM output |
| `player.web.ts` | Browser playback, gapless on a Web Audio clock |
| `webMic.ts` | Browser microphone as 16-bit mono PCM |
| `types.ts` | Shared types |
