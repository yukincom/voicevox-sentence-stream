"""Incremental text → complete VOICEVOX sentence audio, without runtime dependencies."""

from .core import AudioChunk, SentenceSplitter, VoicevoxClient, iter_audio

__all__ = ["AudioChunk", "SentenceSplitter", "VoicevoxClient", "iter_audio"]
