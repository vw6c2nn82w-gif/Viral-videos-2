# How noise-cancelling headphones work — reel

A 38-second vertical (1080×1920) explainer reel, generated entirely in code.

```bash
python3 make_reel.py                  # -> noise_cancellation_reel.mp4
python3 make_reel.py --preview 26.5   # render a single still at t=26.5s
```

Requires Python 3 with `numpy` + `Pillow`, `ffmpeg`, and the Inter font (falls back to DejaVu).

## Script
| Time | Beat |
|---|---|
| 0–4s | Hook: "Noise-cancelling headphones fight sound with MORE sound." |
| 4–9s | Sound = air pressure waves (peaks squeezed, troughs stretched) |
| 9–15s | Step 1 – an outside mic listens to the noise |
| 15–21s | Step 2 – a chip builds the 180°-flipped "anti-noise" wave |
| 21–28s | Step 3 – the speaker plays it, peak + trough = zero (destructive interference). The ANC switch flips on and the rumble in the soundtrack actually drops out |
| 28–34s | Why voices still get through: slow hums are easy to cancel, fast random sounds are blocked by the ear cushions instead |
| 34–38s | "Your headphones aren't blocking noise. They're ERASING it." |

The soundtrack is synthesized as well: a loud engine rumble that is cancelled (about −27 dB) at the ANC moment, high-frequency hiss that stays (as with real ANC), a soft pad, and whooshes between scenes. There is no voiceover, so the captions carry the story.
