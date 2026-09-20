# Try the Hermes integration

The reusable library is independent of Hermes. For the actual Desktop
integration shown in the demo, use the public
[Hermes feature branch](https://github.com/yukincom/hermes-agent/tree/codex/voicevox-sentence-stream).
[Upstream PR #109281](https://github.com/NousResearch/hermes-agent/pull/109281)
contains the proposed changes. The feature branch contains the integration directly; installing this library into Hermes is
not required. The branch is a community preview, pending upstream review.

## macOS / Linux source setup

Use a fresh checkout with the normal Hermes development prerequisites
(Python 3.11+, uv, Git and a Node version supported by the checkout).
See the branch's [Desktop development guide](https://github.com/yukincom/hermes-agent/blob/codex/voicevox-sentence-stream/apps/desktop/README.md).

```sh
git clone --branch codex/voicevox-sentence-stream https://github.com/yukincom/hermes-agent.git hermes-voicevox
cd hermes-voicevox
uv sync --locked --extra voice --extra edge-tts
npm ci
HERMES_HOME="$HOME/.hermes-voicevox-demo" .venv/bin/hermes setup
```

Choose your LLM in setup. Start VOICEVOX Engine separately, then merge these
keys into `~/.hermes-voicevox-demo/config.yaml` without replacing other settings:

```yaml
tts:
  streaming:
    provider: voicevox
  voicevox:
    base_url: http://127.0.0.1:50021
    speaker: 3
```

Keep your existing `tts.provider` for whole-file fallback and voice attachments.
The streaming override uses VOICEVOX; that fallback may use another voice.
The integration does not launch or stop the engine or download its voice data.

```sh
HERMES_HOME="$HOME/.hermes-voicevox-demo" npm run dev --workspace apps/desktop
```

Enable Desktop's reply read-aloud toggle or start a voice conversation. Ask for
a short Japanese acknowledgement followed by a longer explanation: speech can
start after the first `。` while the answer continues generating. Stop cancels
playback and remaining text, while a running engine request may still finish.
For Windows, use the upstream Desktop development instructions and set
`HERMES_HOME` using your shell's syntax.

## Updating an existing preview

The preview's Desktop playback fix prevents history formatting or merged message
rows from interrupting a reply or making it appear unread again. It also prevents
manual read-aloud completion from triggering an automatic replay. These are
Hermes playback-state issues, not VOICEVOX Engine or sentence-splitter changes;
updating this Python library alone will not apply the fix. Stop and new-input
interruptions remain available.

Quit the preview, then check your existing Hermes preview checkout:

```sh
cd hermes-voicevox
git branch --show-current
git status --short
```

Continue only if the branch is `codex/voicevox-sentence-stream` and the working
tree is clean. If you have local changes or use a different branch, keep them and
review how to integrate the update first. Do not overwrite your configuration.

```sh
git pull --ff-only origin codex/voicevox-sentence-stream
```

If the pull cannot fast-forward, stop and review the branch differences; do not
force the update. After a successful update, restart with the same `HERMES_HOME`
as before (this uses the setup example's home):

```sh
HERMES_HOME="$HOME/.hermes-voicevox-demo" npm run dev --workspace apps/desktop
```

No model, engine or configuration change is required for this fix. A packaged
app must be rebuilt from the updated source using the
[Desktop development guide](https://github.com/yukincom/hermes-agent/blob/codex/voicevox-sentence-stream/apps/desktop/README.md);
restarting an old build alone does not include source updates.

After restarting, try a longer reply with the read-aloud button: it should finish
once without stopping at a history refresh or restarting by itself. The fix is
in the community preview; upstream availability depends on PR #109281 being merged.

The source branch has integration tests and CI, but the demo recording was made
on the original local implementation before the latest upstream port. Model,
engine, hardware and sentence length affect latency. No fixed speed is promised.

Demo voice credit: **VOICEVOX:ずんだもん**.

## Optional microphone transcription fix

For repeated or missing text around pauses in longer recordings, see the separate
[segmented Whisper STT setup](transcription.md#hermes-command-provider-example).
This requires installing the optional command utility and configuring the STT
provider; updating the playback preview alone does not enable it. No F1 mapping
or other keyboard customization is included.
