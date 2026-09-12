"""Optional macOS playback example. Requires a running VOICEVOX Engine."""

from pathlib import Path
import subprocess
import tempfile

from voicevox_sentence_stream import VoicevoxClient, iter_audio


def text_deltas():
    # Replace this generator with the visible text deltas from your LLM.
    yield "うん。"
    yield "文章が全部できる前に、"
    yield "話し始められるのだ。"


with tempfile.TemporaryDirectory() as directory:
    for audio in iter_audio(text_deltas(), VoicevoxClient(speaker=3)):
        path = Path(directory) / "sentence.wav"
        path.write_bytes(audio.wav)
        subprocess.run(["afplay", str(path)], check=True)

# Playback is serial: the next sentence is synthesized after playback ends.
# For overlap, hand chunks to your app's existing bounded playback queue.
# Voice credit for a published recording: VOICEVOX:ずんだもん
