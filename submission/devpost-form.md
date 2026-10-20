# Devpost submission form: testing instructions and feedback

## Track

Best Apps and Agents

## Testing instructions

**Quick look (about one minute, no GPU and no API key needed)**

1. Download `GreenlightLocal-Setup.exe` from the latest release:
   https://github.com/rainingsnow0914tw-ship-it/greenlight-local-nemotron/releases/latest
   It runs on Windows 10/11 64-bit and installs for the current user, with no administrator prompt. Windows may
   show a SmartScreen warning because the installer is not code-signed; choose **More info → Run anyway**.
2. Open **Greenlight Local**. The project list already holds three finished, read-only samples.
3. Open **Sample 1 — Fully automatic**, then its scene. Scroll from top to bottom to see the whole run:
   the director's rounds and what the code gates caught, the producer's notes, the reference cards, each shot's
   measured checks and the diagnosis with fixes side by side, the study-mode line table and notes, and the
   finished film with readings and two subtitles.
4. Switch the interface between **EN / 中文 / 日本語** at the top right. Every screen, check and diagnosis follows,
   including what Nemotron wrote. The samples' notes are translated in advance, so this works without a key.
5. Samples 2 and 3 show the other two ways to supply images: your own art used as-is, and character cards only.

**Directing your own script** (needs a Nebius Token Factory API key)

1. **Settings → ② Your keys**: paste a Token Factory key and press **Test**.
2. **Home → Drop your script here**: any novel chapter, screenplay or prose (txt, md, docx, pdf, fountain).
3. Choose a look and press **Start directing** on a part. Nemotron reads, stages and directs the scene; the
   gates and the producer review run on Token Factory. In study mode, **Direct the lines** runs the line director.

**Rendering video** (needs an NVIDIA GPU with 24 GB of memory, 32 GB of RAM and about 50 GB of disk)

1. **Settings → ① This computer**: run the check, then let the one-click installer set up ComfyUI and the models.
2. Prepare reference cards, run a test shot, then render the rest.

Note on the video model: MiniMax H3's license excludes the United States, the European Union, the United Kingdom
and South Korea. The app shows the license and asks for acceptance before installing it. In those regions the
samples and every Nemotron step still work; only local rendering is affected.

The README has the full install and usage guide.

## What was significantly updated during the Submission Period

The original Greenlight was a cloud app I released on 2026-08-20, before the hackathon opened (archived in
`docs/original-greenlight`). During the hackathon I rebuilt it as a desktop app around NVIDIA Nemotron on Nebius
Token Factory and a local video engine, and added: the director loop with 25 code gates and a producer review,
pre-render gates with one-click fixes, staging and reference cards, measured shot checks, the symptom library and
evidence-backed diagnosis, study mode with the line director and its gates, the finishing pipeline, editable
knowledge files, the three-language interface, three built-in samples, and the Windows installer.

## Feedback on Nebius Token Factory and NVIDIA Nemotron

**What worked well**

- **One API, three sizes.** Nemotron 3 Super, Ultra and Nano behind the same OpenAI-compatible endpoint made it easy
  to give each role the right model: Super for directing, Ultra for the producer review and meaning checks, Nano
  for high-volume translation of interface text.
- **Tool calling on all three sizes.** Super, Ultra and Nano all accepted tool definitions, which let us try
  in-reasoning tools (speech timing, gate checks) without switching providers.
- **Reasoning control.** `chat_template_kwargs` with `enable_thinking` and `reasoning_budget` is accepted by
  all three models and was the single most important setting for a stable director loop.
- **Vision.** Nemotron 3.5 Super VL reliably answered narrow questions about our generated images (is there a
  person in this empty set; what is this character wearing).

**What was hard, with suggestions**

1. **Reasoning tokens count against `max_tokens`, and running out returns an empty answer.** In our first
   stability run, 8 of 24 director rounds came back empty because thinking passed 30,000 tokens and hit a
   32,000 cap. Suggestions: a distinct `finish_reason` when the limit is reached during reasoning, and the
   `reasoning_budget` parameter documented next to `max_tokens` in the quick-start.
2. **`reasoning_budget` is a soft limit.** Set to 16,000, real thinking reached 27,000–28,000 tokens; we
   settled on a budget of 12,000 with `max_tokens` of 48,000. Documenting the typical overshoot would save
   others the measuring.
3. **Long non-streaming responses are cut by proxies.** A long director turn with no bytes for 60 seconds was
   dropped by the hosting proxy in front of our first cloud demo. Streaming fixed it. A note in the docs that
   long reasoning calls should stream would help.
4. **Structured output from Nano needs guards.** With a JSON schema, Nano occasionally wrapped the answer in an
   extra object, returned mixed-language text unchanged when it contained a few Japanese words, or slipped a
   word from another language into a Japanese translation (a Spanish word in one case). We added checks for all
   three. Stricter schema adherence, or a documented recommendation for multilingual translation, would help.
5. **Letting the model call tools cost more than doing the work for it.** In an A/B test on the same scene
   (three runs each), letting Nemotron call our tools during reasoning averaged about 170,000 tokens with one
   pass in three; precomputing the same information and checking afterwards averaged about 80,000 tokens with
   three passes in three. A short guide on when in-reasoning tool use pays off would be valuable.
6. **Credits.** Promo-code credits are not applied automatically and billing setup is required even when using
   credits; a clearer first-run flow for hackathon credits would remove friction.
7. **A video model on Token Factory.** None of the public models generates video, so rendering had to stay on a
   local GPU. An open video model endpoint (or a serverless template for ComfyUI) would let projects like this run
   end to end on Nebius.

## Additional info answers (as saved in the Devpost draft, 2026-10-20)

Project name: Greenlight Local · NVIDIA Nemotron Edition
Elevator pitch: Drop in a story, get an anime you can learn a language from: directed by NVIDIA Nemotron, rendered on your own GPU, and checked shot by shot before anyone watches it.
Built with: nvidia-nemotron, nebius-token-factory, python, fastapi, react, typescript, comfyui, minimax-h3, qwen-image, faster-whisper, real-esrgan, ffmpeg, pywebview, fugashi, pyinstaller
Try it out: the repository and its latest release
Gallery: 11 images from `screenshots/` (03, 01, 02, 05, 06, 07, 08, 09, 10, architecture, 04), each with an English caption
Thumbnail: `screenshots/thumbnail_devpost_3x2.png`

**Which models, and why that size.** Nemotron 3 Super (nvidia/nemotron-3-super-120b-a12b) for the long reasoning work: reading the script, staging, the director loop, the line director, fixing blocked shots and shot diagnosis. It reasons through a whole scene within a token budget and fixes exactly the shot a gate names. Nemotron 3 Ultra (nvidia/Nemotron-3-Ultra-550b-a55b) where judgment matters most: the producer review against the scene's emotional arc and the meaning review of rewritten lines. Nemotron 3 Nano (NVIDIA-Nemotron-3-Nano-30B-A3B) for fast, high-volume calls: subtitle translation and translating the interface's AI-written notes. Nemotron 3.5 Super VL for narrow image questions: is there a person in this empty set, what is this character wearing. Matching size to role keeps the app responsive and the cost per scene low.

**Fine-tune, prompt-engineer or out of the box.** No fine-tuning. Nemotron is used through the Token Factory API with prompt engineering inside a framework of code. The director writes against a rulebook; Python gates check every draft and send it back with the exact rule and shot; drafts that get worse are rejected. The program measures (speech timing, the director's track record of most-broken rules) and hands the results to the model instead of letting it guess. Every diagnosis must cite a measurement, a person's observation or an approved control shot, and every producer note must quote the shot it names, or it is discarded. Reasoning is controlled with chat_template_kwargs (enable_thinking, reasoning_budget 12,000) and max_tokens 48,000. Each line-director gate was validated against hundreds of hand-approved Japanese lines and must stay silent on all of them before it may block.

**Compared with other models.** The original cloud Greenlight used Gemini models for every role. Rebuilding around Nemotron, the biggest difference was how well Nemotron works inside a loop of explicit checks: when a gate says "rule 7, shot s061_10", it fixes that shot and leaves the rest alone, and directing converges in a few rounds. Splitting roles by size worked better than one model for everything: Super for directing, Ultra for the judgment-heavy review, Nano for volume. Where it needed more help than the cloud model we used before: long reasoning has to be budgeted explicitly, and Nano's multilingual output needs guards (occasionally an unchanged source line or a stray word from another language), which we added as code checks.

**Most valuable Nebius capabilities.** Token Factory's OpenAI-compatible API gave one endpoint for Nemotron 3 Super, Ultra, Nano and the VL model, so each role could use the right size without changing providers or code paths. API access with streaming made long director turns reliable, and the reasoning controls (enable_thinking, reasoning_budget) were essential for a stable director loop. No GPU instances were needed for reasoning: all Nemotron inference runs on Token Factory, while the user's own GPU is left free for video rendering. During development a Cloud Run demo streamed director rounds live from Token Factory; the shipped app calls Token Factory directly with the user's own key.

**Improvements that would have helped.** See items 1–7 in the feedback section above.

**Hopes for the Nemotron team.** Open weights of the Nemotron 3 family sized to run alongside a video model on a single consumer GPU, so the whole crew (director, producer, line director) can run fully offline on the same machine that renders. Also: stronger multilingual output, especially Japanese and Chinese, and vision models that can watch short video clips, so a shot could be checked for lip sync and continuity directly.

**Left for the entrant to fill in:** submitter type, organization, country, Canada province, the three 1–10 ratings, Tavily (No — this edition does not call Tavily), IRL city, the two eligibility checkboxes, AI tools used, learning level, career value. Demo video URL after the YouTube upload.
