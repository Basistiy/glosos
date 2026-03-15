import asyncio
import math
import sys

from livekit import rtc


async def emit_ready_sound(room: rtc.Room) -> None:
    sample_rate = 48000
    num_channels = 1
    frequency_hz = 200.0
    duration_seconds = 0.5
    amplitude = 0.22
    samples_per_chunk = 960  # 20ms at 48kHz

    source = rtc.AudioSource(sample_rate=sample_rate, num_channels=num_channels)
    track = rtc.LocalAudioTrack.create_audio_track("ready-tone", source)
    publication = None

    try:
        publication = await room.local_participant.publish_track(track)
        total_samples = int(sample_rate * duration_seconds)
        two_pi_f = 2.0 * math.pi * frequency_hz

        for offset in range(0, total_samples, samples_per_chunk):
            sample_count = min(samples_per_chunk, total_samples - offset)
            pcm = bytearray()
            for index in range(sample_count):
                t = (offset + index) / sample_rate
                sample = int(32767 * amplitude * math.sin(two_pi_f * t))
                pcm.extend(sample.to_bytes(2, byteorder="little", signed=True))

            frame = rtc.AudioFrame(
                data=bytes(pcm),
                sample_rate=sample_rate,
                num_channels=num_channels,
                samples_per_channel=sample_count,
            )
            await source.capture_frame(frame)

        await asyncio.sleep(0.05)
    except Exception as exc:
        print(f"[token-agent] failed to play ready sound: {exc}")
        # Last-resort local bell if audio publishing path fails.
        sys.stdout.write("\a")
        sys.stdout.flush()
    finally:
        try:
            if publication is not None and getattr(publication, "sid", ""):
                room.local_participant.unpublish_track(publication.sid)
        except Exception:
            pass
