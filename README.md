# voicevox-sentence-stream

**Start speaking while your LLM is still writing.**

A small, synchronous Python library that turns visible text deltas into
VOICEVOX audio, one sentence at a time. Japanese `。！？` ends a chunk immediately,
including short replies such as `うん。`. There are no Hermes dependencies,
model downloads, servers, or runtime Python dependencies.

This is **sentence-level streaming**: VOICEVOX synthesizes a complete sentence
WAV before it is yielded. It is not incremental waveform generation inside the
engine. First-audio latency depends on your LLM, punctuation, engine and hardware;
the package does not guarantee a fixed latency.

Want the Hermes Desktop integration from the demo? See the
[Hermes preview setup](examples/hermes.md). Other applications can use the
small library below directly.

Already using the Hermes preview? See the
[update and restart instructions](examples/hermes.md#updating-an-existing-preview)
for the Desktop fix for interrupted or unexpectedly repeated read-aloud.
This is a Hermes playback-state fix, not a change to this library or VOICEVOX Engine.

[Watch the Japanese voice demo on X](https://x.com/yukin_co/status/2098814603065282636)
— recorded in the original local Hermes implementation; timings depend on
the model, engine and hardware. Voice: **VOICEVOX:ずんだもん**.

## Install

Requires Python 3.10+ and an already running
[VOICEVOX Engine](https://github.com/VOICEVOX/voicevox_engine) (normally
`http://127.0.0.1:50021`). Install and start the engine separately.

```sh
python -m pip install git+https://github.com/yukincom/voicevox-sentence-stream.git
```

Or clone this repository and run `python -m pip install .`. No PyPI release is
required. Check your engine's `/speakers` endpoint for available style IDs;
the examples use style ID `3` (ずんだもん・ノーマル on standard VOICEVOX).

## Embed in an application

```python
from voicevox_sentence_stream import VoicevoxClient, iter_audio

client = VoicevoxClient(base_url="http://127.0.0.1:50021", speaker=3)

def visible_text_deltas():
    # Replace with your LLM's text-delta iterator.
    yield "うん。"
    yield "文章が全部できる前に、"
    yield "話し始められるのだ。"

for audio in iter_audio(visible_text_deltas(), client):
    # The first chunk is available before the later deltas are consumed.
    # Send audio.wav to a WAV consumer, or audio.pcm to your PCM player.
    print(audio.text, len(audio.pcm), audio.sample_rate)
```

For an OpenAI-compatible SDK stream, adapt **visible content** only:

```python
def visible_text_deltas(llm_stream):
    for event in llm_stream:
        if event.choices:
            text = event.choices[0].delta.content
            if text:
                yield text

# llm_stream comes from your app; this package does not require an LLM SDK.
for audio in iter_audio(visible_text_deltas(llm_stream), client):
    your_audio_sink(audio)  # Your application supplies this function.
```

`AudioChunk` carries the exact chunk `text`, complete `wav`, raw signed
little-endian 16-bit `pcm`, `sample_rate`, `channels` and `sample_width` (bytes).
The client requests and validates mono 24 kHz audio by default; `sample_rate`
is configurable. Every query and synthesis request includes the chosen speaker.

Your app owns recording, playback, interruption and its existing bounded audio
queue. Iteration backpressures text intake; there is no background prefetch.
For simultaneous synthesis and playback, consume the iterator in a worker and
hand audio to your existing bounded playback queue. If the LLM transport buffers
upstream data, your app also owns that buffer. The library uses bounded sentence and response storage with no background
queue; callers decide how many returned audio chunks to retain.

See [examples/play_macos.py](examples/play_macos.py) for optional `afplay`
playback with only the standard library. That example is deliberately serial:
it waits for playback before synthesizing the next sentence and can have gaps.
Other platforms can consume the same WAV/PCM through their own audio player.

## CLI: stdin → sentence WAV files

```sh
printf 'うん。文章ができたところから音声になるのだ。' | \
  voicevox-sentence-stream --speaker 3 --output-dir demo-audio
```

The CLI reads characters incrementally, writes `0001.wav`, `0002.wav`, … and
prints each completed path immediately. It does not wait for a newline or EOF
before synthesizing a completed sentence. Pipe an unbuffered text producer
(for Python, `python -u`) to demonstrate this with a live stream. Terminal line
buffering may still require Enter when typing directly. Existing output files
are never overwritten; use a fresh output directory per run. The CLI saves
audio and does not play it automatically.

## Chunking, cancellation and errors

- `SentenceSplitter.feed(delta)` and `.flush()` are iterators. Fully consume a
  feed before feeding more. Concatenating their outputs reproduces the input
  exactly, including whitespace. Japanese punctuation emits immediately;
  Latin `.?!` requires following whitespace. Newlines also end chunks.
- `max_chars=180` splits long unpunctuated text without dropping its tail.
  Change it with `iter_audio(..., max_chars=...)` or the CLI's `--max-chars`.
  Hard splits may cut a word; this small splitter is not a linguistic parser.
  Closing quotation marks after a punctuation mark may begin the next chunk.
- Whitespace-only chunks produce no HTTP requests. End of input flushes the
  final sentence. There is no idle timer: the caller must close its input stream
  or use the splitter directly if it wants a timed flush.
- Feed only speech-ready content. Reasoning tags, Markdown and code blocks are
  not filtered; use your application's existing content filtering.
- Supply a `threading.Event` via `iter_audio(..., cancel=stop)` and call
  `stop.set()` to discard pending chunks and late audio. Cancellation cannot
  interrupt a blocking input iterator, HTTP operation, engine computation or
  audio already handed to your player. Stop playback separately in your app.
- HTTP, JSON and audio-format errors propagate. There are no automatic retries
  or fallback engines. The configurable `timeout=30` is per socket operation,
  not an absolute turn deadline. Responses are capped at 16 MiB by default
  (`max_response_bytes`); oversized or malformed audio raises an error.

## 日本語

LLMの出力を文字列の差分として `iter_audio()` に渡すだけで、文が確定した順に
VOICEVOXの音声を受け取れます。「うん。」など短い相づちもすぐ合成します。
全文の完成を待ちません。VOICEVOXエンジン自体は事前に別途起動してください。

標準の接続先は `http://127.0.0.1:50021`、話者はずんだもん・ノーマル
（speaker=3）です。話者や接続先はアプリごとに指定できます。
Hermes以外のチャット、ゲーム、ロボットにも組み込めます。録音・再生・割り込みは
利用側のアプリが担当します。長文は分割し、切り捨てません。

音声波形そのものを生成途中から受信する方式ではなく、**文ごとに完成した音声を
順に受け取る方式**です。デモの速度はモデルや環境によって変わります。

## Tests

```sh
python -m pip install .
python -m unittest discover -s tests -v
```

Tests use a loopback fake HTTP server; they do not contact VOICEVOX or load voice
models. They verify request fields, PCM format, early yield, CLI output before
stdin EOF, long-text preservation, cancellation and error propagation.

## License and voice credits

Code is MIT licensed; see [LICENSE](LICENSE) and [NOTICE](NOTICE) for the Hermes
origin and attribution. VOICEVOX Engine, voice models, character assets and
generated audio have their own terms. This code license does not license those
assets. Follow the applicable [VOICEVOX terms](https://voicevox.hiroshiba.jp/term/)
and each character's terms when publishing audio or video.

For the standard speaker=3 example, include this voice credit:

**VOICEVOX:ずんだもん**

This is a community project, not an official VOICEVOX or Nous Research release.
