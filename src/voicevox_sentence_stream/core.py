"""Sentence-level synthesis; VOICEVOX still produces a full WAV per request."""

from __future__ import annotations

import io
import json
from dataclasses import dataclass
from threading import Event
from typing import Iterable, Iterator
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen
import wave


class SentenceSplitter:
    """Keep every character, emitting Japanese punctuation immediately.

    Latin .!? boundaries require following whitespace (so decimal points do
    not split). Newlines also end a chunk. Unpunctuated text is split at
    max_chars, never truncated. Consume each feed iterator before feeding more.
    Only user-visible text should be fed: no reasoning, markup filtering, or
    Markdown interpretation is performed here.
    """

    def __init__(self, max_chars: int = 180):
        if max_chars < 1:
            raise ValueError("max_chars must be positive")
        self.max_chars = max_chars
        self._buffer = ""

    def feed(self, delta: str) -> Iterator[str]:
        for char in delta:
            previous = self._buffer[-1:] if self._buffer else ""
            self._buffer += char
            if (char in "。！？\n" or
                    (char.isspace() and previous in (".", "!", "?")) or
                    len(self._buffer) >= self.max_chars):
                text, self._buffer = self._buffer, ""
                yield text

    def flush(self) -> Iterator[str]:
        if self._buffer:
            text, self._buffer = self._buffer, ""
            yield text


@dataclass(frozen=True)
class AudioChunk:
    """A complete sentence WAV plus decoded little-endian signed 16-bit PCM."""

    text: str
    wav: bytes
    pcm: bytes
    sample_rate: int
    channels: int
    sample_width: int


class VoicevoxClient:
    """Use an already running VOICEVOX Engine; never launch or stop it.

    timeout is a per-socket-operation timeout, not a total turn deadline.
    HTTP/network errors propagate without automatic retries. Response byte
    caps bound allocations and can be increased for an unusual engine setup.
    """

    def __init__(self, base_url: str = "http://127.0.0.1:50021", speaker: int = 3,
                 sample_rate: int = 24000, timeout: float = 30,
                 max_response_bytes: int = 16 * 1024 * 1024):
        parsed = urlsplit(base_url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc or parsed.query or parsed.fragment:
            raise ValueError("base_url must be an HTTP(S) base URL without query or fragment")
        if speaker < 0 or sample_rate < 1 or timeout <= 0 or max_response_bytes < 1:
            raise ValueError("invalid speaker, sample rate, timeout or response size")
        self.base_url = base_url.rstrip("/")
        self.speaker = speaker
        self.sample_rate = sample_rate
        self.timeout = timeout
        self.max_response_bytes = max_response_bytes

    def _post(self, path: str, params: dict, body: bytes = b"") -> bytes:
        request = Request(self.base_url + path + "?" + urlencode(params), data=body,
                          headers={"Content-Type": "application/json"}, method="POST")
        with urlopen(request, timeout=self.timeout) as response:
            data = response.read(self.max_response_bytes + 1)
        if len(data) > self.max_response_bytes:
            raise ValueError("VOICEVOX response exceeds max_response_bytes")
        return data

    def synthesize(self, text: str, *, cancel: Event | None = None) -> AudioChunk | None:
        """Cancel skips further requests/results; it cannot stop engine computation."""
        if cancel is not None and cancel.is_set():
            return None
        query = json.loads(self._post("/audio_query", {"text": text, "speaker": self.speaker}))
        if not isinstance(query, dict):
            raise ValueError("VOICEVOX audio_query response must be an object")
        if cancel is not None and cancel.is_set():
            return None
        query["outputSamplingRate"] = self.sample_rate
        query["outputStereo"] = False
        wav = self._post("/synthesis", {"speaker": self.speaker},
                         json.dumps(query, ensure_ascii=False).encode("utf-8"))
        if cancel is not None and cancel.is_set():
            return None
        with wave.open(io.BytesIO(wav), "rb") as audio:
            sample_rate, channels, sample_width = (audio.getframerate(), audio.getnchannels(),
                                                   audio.getsampwidth())
            if (sample_rate, channels, sample_width, audio.getcomptype()) != (self.sample_rate, 1, 2, "NONE"):
                raise ValueError("VOICEVOX returned an unexpected PCM format")
            frames = audio.getnframes()
            pcm = audio.readframes(frames)
            if len(pcm) != frames * channels * sample_width:
                raise ValueError("VOICEVOX returned truncated PCM audio")
        return AudioChunk(text, wav, pcm, sample_rate, channels, sample_width)


def iter_audio(deltas: Iterable[str], client: VoicevoxClient, *, max_chars: int = 180,
               cancel: Event | None = None) -> Iterator[AudioChunk]:
    """Pull text only as needed and yield audio before the input is exhausted.

    There are no background workers or unbounded queues. A slow consumer
    backpressures text intake. Cancellation is checked before consuming the
    next delta and between HTTP requests. A blocking input iterator / network
    call cannot be interrupted by this function; late audio is discarded.
    """
    cancel = cancel if cancel is not None else Event()
    splitter = SentenceSplitter(max_chars)
    source = iter(deltas)
    while not cancel.is_set():
        try:
            delta = next(source)
        except StopIteration:
            chunks = splitter.flush()
            finished = True
        else:
            chunks = splitter.feed(delta)
            finished = False
        for text in chunks:
            if cancel.is_set():
                return
            if text.strip():
                audio = client.synthesize(text, cancel=cancel)
                if audio is not None and not cancel.is_set():
                    yield audio
        if finished:
            return
