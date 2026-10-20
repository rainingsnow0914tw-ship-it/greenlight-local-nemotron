# 🎬 Greenlight Local · NVIDIA Nemotron Edition

**Drop in a story. Get an anime you can learn a language from: directed by NVIDIA Nemotron, rendered
on your own computer, and checked shot by shot before anyone watches it.**

🌏 **The whole app works in English, 中文 and 日本語**: every screen, every check and diagnosis, and even what Nemotron writes follow the language you pick.

---

## 💡 Why I built this

I grew up watching anime. When I set out to learn Japanese, I hit the wall every learner knows: the
middle. Textbooks were too easy and real shows were too fast. Listening and speaking practice turned into
a chore, and I spent more time hunting for material than studying it. Videos with Japanese and my own
language side by side were rare, and even those never put readings over the kanji, so I kept pausing to
look words up and losing the scene.

So I started making my own. This year, on one laptop GPU, I finished a ten-episode anime series, then
began adapting my own novel into a Japanese anime for learners: four episodes so far, each in a
Japanese–Chinese and a Japanese–English version, with readings over every kanji.

Doing it by hand taught me what it really takes. Not better prompts, but a director's rulebook, the habit
of measuring every shot, and a growing list of what goes wrong and why. Greenlight Local packages all of
it into one app, with NVIDIA Nemotron as the director. A few years ago this needed a studio. Now it needs
a story and a laptop.

---

## 😩 The problem

- **Learners stall in the middle.** Past the beginner stage there is little to practise with: textbook
  dialogue is too easy, native shows are too fast, and bilingual material with readings over the kanji
  barely exists. Learners spend their time searching instead of listening.
- **Translation flattens how people talk.** Machine translation doesn't know who is speaking to whom, so
  a casual line to a friend and a careful one to a boss come out the same, and that is exactly what a
  learner needs to hear.
- **AI video makes clips, not films.** A great eight-second shot is easy now. Turning a story into
  dozens of consistent, well-directed shots still takes a director's judgment most people don't have.
- **When a shot fails, nobody tells you why.** Creators re-roll and hope. Every blind retry costs
  minutes of GPU or dollars of cloud time, and the same mistake comes back next scene.

---

## ✨ What Greenlight Local changes

| Before | With Greenlight Local |
|---|---|
| AI filmmaking tools speak English only | The whole app speaks English, Chinese and Japanese, including the AI's own notes and the diagnoses |
| Hunt for bilingual clips, pause to look up every kanji | Any story you love becomes your own study anime, with readings and two subtitles |
| Flat machine-translated subtitles | Dialogue rewritten for each character and listener, with a note on why it is said that way |
| Write prompts shot by shot | Drop in a novel chapter or screenplay; Nemotron directs the whole scene |
| Re-roll until it looks right | Every shot is measured; failures come with a cited cause and a fix |
| Pay per second in the cloud, upload your story | Render on your own GPU; nothing leaves your machine |
| One kind of video, one look | Drama, documentary, interview or explainer; five looks; landscape 16:9 or vertical 9:16 for Shorts and Reels |
| Stock characters | Cast anyone, or anything: an AI, an animal, a light, or yourself from your own photo |
| Know-how lives in one person's head | Rules, checks and the symptom library are files anyone can share and extend |

---

## 🙋 Who it helps

- **Language learners and teachers** who want study material built from stories they actually care
  about, instead of textbook dialogue.
- **Writers and small teams** who want their stories on screen without learning a production pipeline.
- **Studios and creators** who want consistent quality and a record of what works.

---

## ⚙️ How it works

![How Greenlight Local works](submission/screenshots/architecture.png)

What a learner sees, and the grammar notes behind it:

![Japanese with readings and English subtitles](submission/screenshots/03_my_study_anime_ep1_jasmine.png)
![Study notes on the most useful patterns](submission/screenshots/06_study_notes_patterns.png)

```
Story ─► Nemotron: read · stage · direct ─► code gates + producer review ─► shot list
                                                                               │
       ledger ◄── person decides ◄── fixes with measured costs ◄── diagnosis ◄─┤
         │                                    (cites evidence)                 ▼
         └──► success-rate prediction       local checks ◄── H3 render (your GPU, ComfyUI)
                                                  │
                    approved shots ──► trim · upscale · transitions · join · subtitles ──► film
```

### 🎬 1. A director with an enforceable rulebook

Nemotron writes the shot list against 31 directing rules distilled from real production. Twenty-five
are enforced by code: a line too long for its shot, a speaker who isn't in frame, a line split mid-sentence,
the same action repeated across three shots, shots that never change framing or move the camera, words
the render model takes literally (眼裡, a "drop" of tears), lines too short to say. Drafts loop back with
the exact rule and shot until they pass, and the director starts each scene with its own track record:
the rules its past first drafts broke most often. Long scenes are split into parts of ten lines, directed
in parallel and joined, so a whole episode is directed in about an hour. A larger Nemotron then reviews the cut as a
producer, against the scene's emotional arc. Every producer note must quote words that actually appear
in the shot it names; unsupported notes are discarded automatically.

The same gates run again **before every render**, so a shot that was edited, or that the director gave
up on, can't slip through. A blocked shot never stalls the batch: **Let Nemotron fix this shot** rewrites
only that shot (it may merge a too-short line into its neighbour, keeping every word) and is accepted only
if it adds no new error; **Generate anyway** is for when a person can see the rule doesn't apply (a cat's
meow is not a line of dialogue). Either way the decision is recorded.

Three built-in samples show the three ways to supply images: everything generated by the app, everything
brought by you, and character cards only (see **The samples** under *Using it*).

### 🖼️ Reference cards: the same people in the same place

Before rendering, the app prepares what the video model looks at. Each character gets a full-body card
and a plain-background face card, made once from the locked casting image and shared by every scene.
Each location gets four empty-set cards (the wall in front and the wall behind, each wide and medium), and
every shot is matched to the wall behind whoever faces the camera, so reverse shots stay consistent.
Nemotron's vision model checks each empty set for stray people and regenerates it, and reads each
character card to describe what they wear, so a two-person shot can say exactly who speaks. Every card
can be regenerated with a note or replaced with your own picture.

### 📏 2. Every shot is measured

After each render, the app measures the shot instead of asking a model whether it looks fine:

| Check | Measurement |
|---|---|
| Is the line spoken correctly? | Local speech recognition (faster-whisper, run on the CPU so it never competes with rendering for GPU memory) compared with the script |
| Extra speech before the line | Loudness before the first recognized word, which catches what transcripts silently drop |
| Room after the line | Seconds of silence after the last word, so cuts don't clip the speaker |
| End of the line | The last words were actually said, and nothing extra was tacked on after them (extra speech is trimmed at finishing) |
| Sounds slipped into the line | Syllables the script doesn't have, heard in the middle of an otherwise correct line |
| Late start | Time to the first word |
| Lip sync | A strip of the speaker's face during the line, for one-click human confirmation |

Thresholds come from the tooling of the production this app grew out of, where each was tuned on real
shots.

### 🩺 3. Diagnosis backed by evidence

When a check fails, Nemotron diagnoses the shot from a **symptom library**: proven causes, each with the
fix, a before-and-after prompt example and where it was verified. It also sees approved shots from the
same scene as a control group. Any diagnosis that doesn't cite a measurement, a person's observation or a
control shot is rejected.

The fixes are laid out side by side, and their costs are computed by the program from the measurements,
not estimated by the model. In one test shot the transcript matched the script perfectly, while the
loudness check found 4.3 seconds of extra, unscripted speech before the line. The app offered:

- **A.** Trim 4.2 seconds. No re-render; the shot goes from 7.3 to 3.1 seconds.
- **B.** Lengthen the shot by a second and re-render.
- **C.** Re-render with a new performance Nemotron wrote so the character speaks sooner.

The person chooses. The choice, the failed checks and the symptom codes are written to the generation
ledger, which drives the success-rate predictor for future shots.

### 👄 4. Control over who speaks

The prompt format names the speaker, the voice and the verb for every line, which drives lip sync.
Leaving the speaker out does the opposite on purpose: the voice plays and the mouth stays closed, three
renders out of three. Shots marked as narration or inner monologue use that form automatically, so inner
voices keep their audio without a silent-track workaround.

### 🈶 5. Study mode: any story becomes a language lesson

Pick the language the film is spoken in (Japanese, Chinese, English or Korean) and the subtitle languages
below it. A **line director** rewrites every line the way this character would say it, at this moment,
to this listener: casual between friends, formal toward a superior, more relaxed as a relationship grows.
Each line gets a register tag, a one-sentence explanation for the learner, subtitles that keep the same
tone, and readings above the kanji. Each part also gets study notes on the most useful patterns.

Code gates keep it correct:

- each rewritten line must quote the source line it replaces, so lines never drift to the wrong speaker
- speech must fit the character (pronouns and register by gender and relationship), and each character
  keeps the first-person pronoun written for them in the names list
- names stay names, with a per-project name list for readings, English spellings and personality
- no word of the source language is left untranslated inside the spoken line
- verb endings conjugate correctly, and kanji keep their okurigana
- private conversation uses natural spoken forms, not written ones
- kanji readings are complete and checked against a Japanese dictionary (fugashi with UniDic)
- every line must be speakable within its shot
- explanations must be in the learner's language

A second Nemotron reviews meaning against the source and flags drift with quotes. Before any gate is
allowed to block, it is run over Japanese lines that were already approved by hand in my own series
(658, 609 and 1,046 lines for the three newest gates) and must stay silent on every one of them.

Study subtitles show the spoken line with readings on top and the learner's language below, timed to
when each line is actually spoken, using word timings from speech recognition.

### 🎨 Any format, any look

The same pipeline makes more than anime drama. Choose how the story is told: **Drama** (characters act it
out), **Documentary** (third-person narration over observed scenes), **Interview** (someone talking to the
camera) or **Explainer** (narration that makes an idea concrete). Choose how it looks: **Photoreal**,
**Anime (2D cel)**, **3D animated**, **Stop-motion** or **Painterly / storybook**, which suits children's
lessons. Choose **Landscape 16:9** for YouTube and TV, or **Portrait 9:16** for Shorts, Reels and TikTok,
where short language lessons live. The format can change later; the look is fixed once characters are
cast in it.

### 🎭 Casting: anyone, or anything, including you

Each casting round offers three candidates, and the next round goes where you point it instead of
re-rolling blind: **Younger**, **Older**, **Softer**, **Sharper**, **More refined**, **New hair**, **New
wardrobe**. Characters that aren't people (an AI that is only a voice, an animal, a creature of light) get
directions that make sense for them instead: **More literal**, **More abstract**, **Warmer**, **Colder**.
In my own series that is how a robot AI and a little cat that mimics animal sounds were cast.

You can also put yourself in the story: upload a photo of your face (after confirming you have the right
to use it), and casting builds the character around it in the film's style. Learners can watch themselves
speak the language they are learning.

Nothing you generate is thrown away. Every candidate, card and take stays in the project's **Media
library**, so an earlier version is always there to go back to; you delete only what you choose to.

### 🎞️ 6. Finishing in one pass

Approved shots are trimmed where the checks found extra speech, upscaled to 1080p (RealESRGAN's anime
model for anime styles, fast resampling for photoreal), joined with hard cuts inside a scene and fades
between scenes that only fall on silence, and subtitled. A flash to white can be switched on before any
shot to mark a small jump in time. Shots with people but no dialogue are pinned to a silent audio track,
so the model doesn't invent murmuring. Every step reports progress on screen, and the
upscale step verifies that each shot keeps its exact length.

### 📚 7. Knowledge that grows outside the app

The symptom library and the director's rules are plain files in
`%LOCALAPPDATA%\GreenlightLocal\knowledge`. Teams edit them in any text editor and the next shot uses
the new version, with no restart or rebuild. Broken edits fall back to the default and are reported on the
settings page. The installer is the convenient way to run the system; what makes it better over time
lives in these files and in the ledger.

---

## 🧠 Where Nemotron is used

All reasoning runs on NVIDIA Nemotron through Nebius Token Factory, and each role gets the Nemotron that
fits it: Super for the long reasoning work, Ultra where judgment matters most, Nano for fast, high-volume
steps.

| Step | Default model | What it decides |
|---|---|---|
| Read the script, split into parts | Nemotron 3 Super | Scenes, characters, who speaks |
| Staging | Nemotron 3 Super | Positions, facing, props, which wall each shot sees |
| Director | Nemotron 3 Super | The shot list, revised until every gate passes |
| Producer review | Nemotron 3 Ultra | Notes against the emotional arc, each quoting its shot |
| Line director | Nemotron 3 Super | Spoken lines, readings, subtitles, study notes |
| Meaning review | Nemotron 3 Ultra | Lines whose meaning drifted from the source |
| Fix a blocked shot | Nemotron 3 Super | A rewrite of just that shot, checked again by the gates |
| Shot diagnosis | Nemotron 3 Super | Cause and fix for a failed shot, from the library only |
| Set and card checks | Nemotron 3.5 Super VL | Whether an empty set has a person in it; what each character wears |
| Character looks and voice | Nemotron 3 Super | Casting descriptions |
| Subtitle translation | Nemotron 3 Nano | Regular subtitles |
| Interface translation | Nemotron 3 Nano | Producer notes, diagnoses and explanations shown in the language you picked |

**Why it is built on Nemotron.** The director loop asks a lot of a model: reason through a whole scene
within a token budget, call tools in the middle of that reasoning (speech timing, the gates), and when a
gate says "rule 7, shot s061_10", fix exactly that shot and nothing else. Nemotron does all three
reliably, and the rules, gates and examples were tuned against how it thinks. Its open weights also mean
the whole crew can one day run on the same NVIDIA GPU that renders the film.

A cloud video engine is marked "coming soon". This edition runs on Nemotron and your own NVIDIA GPU.

---

## 🔋 Built for the hardware people actually have

Most people who make things don't have a lab. They have one GPU, the open models that fit on it, and a
budget. The strongest video models live in the cloud and charge by the second; the strongest of all stay
inside research labs. Greenlight Local is built for the ordinary case on purpose: the video model is open
and renders in 24 GB on a laptop GPU, and the director runs on Nemotron with a key anyone can get.

I have made films both ways. The cloud video model behind the original Greenlight is smarter: better
pictures, and far fewer things to correct. An open model on a laptop GPU needs more care for every shot.
Under that constraint a bigger model is not what fixes a bad shot. A rulebook the director must follow,
measurements it cannot argue with, and a library of proven causes are. That is what turns an open, local
model into consistent, checked films, and none of it is tied to one model. Give the same app a stronger
video model or a larger Nemotron and the same rules, gates and checks make that better too. It is a
framework that makes any AI director better, one shot at a time.

---

## 🛠️ Built during the hackathon

The original Greenlight was a cloud app released on 2026-08-20, before the hackathon began on 08-26
(archived in [docs/original-greenlight](docs/original-greenlight/README.md)). This edition was built
during the hackathon:

- **10-06 to 10-08** Nemotron director loop, code gates, producer review with quote validation
- **10-09 to 10-10** desktop app, local ComfyUI engine, user API keys, local ledger, staging, local
  casting and reference cards, installer
- **10-15** study mode and the line director, local shot checks, symptom library and diagnosis,
  side-by-side fixes, finishing pipeline, editable knowledge, end-to-end renders through the app
- **10-16 to 10-17** the sample re-shot to production standard; production checks brought into the app
  (pre-render gates, reference-card sets, silence seed, flash, fixing blocked shots); a whole episode of my
  novel made in the app, from casting to finished film
- **10-18 to 10-20** three built-in samples, each taking a different path for images (generated, your own
  art used as-is, character cards only); bring-your-own images; the app starts its own ComfyUI and reuses
  models already on the computer; finishing redoes only the shots that changed; new line gates checked
  against hand-approved lines; a full three-language pass, with the samples' AI-written notes translated in
  advance so they read correctly without an API key

---

## 🚀 Where this is going

I ship updates continuously. The plan:

- **Shared learning.** Opt-in sharing of check results and diagnoses, so a failure solved on one machine
  is prevented on every machine, and the symptom library grows into a community-built manual of how AI
  video behaves.
- **Deeper into the Nemotron family.** Every role matched to the right Nemotron, then the whole crew
  running locally from open weights on the same NVIDIA GPU that renders, so a film can be made fully
  offline. Cloud rendering returns as another engine behind the same checks.
- **Story to series.** Characters, sets and voices consistent across whole seasons, with a production
  plan, budget and schedule generated from the script.
- **The language classroom.** Every language pair, characters who teach the grammar of their own lines,
  and story libraries teachers can assign.
- **Beyond the desktop.** A viewer for phones and tablets, and a studio edition that runs the same
  pipeline on a shared GPU server for teams.

---

## 💻 Install

1. Download **`GreenlightLocal-Setup.exe`** from this repository's
   [Releases](../../releases) page and run it. Windows may show a SmartScreen warning because the
   installer is not code-signed; choose **More info → Run anyway**.
2. Open **Greenlight Local** from the Start menu or the desktop shortcut.
3. The project list already holds **three finished samples** you can open right away.
   No GPU, no API key and no setup are needed to look through them.

| To… | You need |
|---|---|
| Look through the samples | Windows 10/11 64-bit |
| Read, stage and direct your own script, and use the line director | a Nebius Token Factory API key |
| Render video and finish films | an NVIDIA GPU with 24 GB of memory (developed on an RTX 5090 laptop GPU), 32 GB of RAM, about 50 GB of free disk for ComfyUI and the models |

🌏 **The whole app speaks English, Chinese and Japanese**; switch at the top right. Not just the buttons: check results, diagnoses and fixes, and even what Nemotron writes (the producer's notes, the casting explanations, scene summaries) follow the language you pick, with character names kept consistent.

From source, for developers:

```bash
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
cd frontend && npm install && npx vite build --outDir ../backend/app/static --emptyOutDir && cd ..
.venv\Scripts\python desktop\launcher.py
```

---

## 🧭 Using it

**The samples (read only).** Each is one finished run: the parsed script, staging, the director's rounds
and what the gates caught, the producer's notes, each shot's checks and diagnosis, the study-mode line table
and notes, and the finished film with readings and two subtitles (Japanese–Chinese and Japanese–English).
They are read only, so you can click around without changing them. Each one takes a different path for the
pictures the video model looks at:

| Sample | Images | Look | Shots | What it shows |
|---|---|---|---|---|
| **1 — Fully automatic** (Chapter 10, Scene 61) | character cards and empty sets made by the app's local models | live action, 16:9 | 19 | just a script, nothing prepared |
| **2 — Bring your own images** (Behind the Scenes ep. 1) | your own character cards, empty sets and first frames, used as-is | anime, 9:16 | 23 | your own art style, only animated — the model does not redraw it |
| **3 — Character cards only** (Behind the Scenes ep. 2) | your own character cards; no empty sets, places described in text | anime, 9:16 | 23 | the characters stay consistent, but the place changes from shot to shot — what empty sets are for |

**Your own film.**

1. **Settings → ② Your keys.** Paste a Nebius Token Factory key and press **Test**.
2. **Settings → ① This computer.** Run the check. If you will render, let the one-click installer set up
   ComfyUI and the models (including the upscale models), or point it at a ComfyUI you already run. If the
   models are already somewhere on this computer, the installer finds them and does not download them again;
   an install can be cancelled and resumes where it stopped. After that, the app starts its ComfyUI by itself
   whenever you open it. MiniMax H3's license excludes the United States, the European Union, the United
   Kingdom and South Korea; the app shows it and asks for acceptance first.
3. **Home → Drop your script here.** A novel chapter, a screenplay or plain prose (txt, md, docx, pdf,
   fountain). Review what was read and continue.
4. **Look.** Pick how the story is told (drama, documentary, interview, explainer), the visual style (five
   looks) and landscape or vertical. To make a study film, turn on **Study mode** on the project page and
   choose the spoken language and your subtitle languages.
5. **Casting.** Lock in each character's look: steer each round of three candidates with the direction
   buttons, upload your own photo to play a character yourself, or upload a character card you already
   made and press **Use this image as is**.
6. **Open a part → Start directing.** Nemotron writes the shot list; the progress panel shows every round,
   what the gates caught and the producer's notes. Any shot that would be blocked at render time is listed
   right there with **Let Nemotron fix this shot**. In study mode, press **Direct the lines** next.
7. **Prepare reference cards** (the Sets panel). Look over the character cards and the empty sets;
   regenerate any with a note, or use your own picture. **Character cards only** skips the empty sets: places
   are then described in text, which works but varies from shot to shot (see Sample 3).
8. **Run test shot**, look at the checks and the diagnosis, then **Approve and unlock batch** and render
   the rest shot by shot, or press **Generate all remaining**. A shot blocked by the pre-render gates is
   skipped so the rest keep rendering; at the end the message box offers **Let Nemotron fix this shot** or
   **Generate anyway** for it.
9. **Finish this part.** Trim, upscale, transitions, join and subtitles in one pass, then download.
   Finishing again only redoes the shots that changed; **Re-burn subtitles only** skips the upscale entirely.

Rendering takes about 7.5 minutes per 8-second shot on the development GPU. The speech-recognition model
for the checks (about 3 GB) downloads the first time a shot is checked. The symptom library and the
director's rules are in **Settings → ⑥ Knowledge**; edit them in any text editor. Tested on Windows.

---

## 📄 Licenses

The code is MIT (see [LICENSE](LICENSE)). Models and libraries keep their own licenses:
MiniMax H3 ([license](https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/LICENSE), regional
restrictions above), Qwen-Image 2512 and Qwen-Image-Edit 2511 (Apache 2.0), Real-ESRGAN (BSD-3),
faster-whisper and CTranslate2 (MIT), fugashi (MIT) with unidic-lite (UniDic, used under BSD),
pypinyin (MIT), pywebview (BSD-3). NVIDIA Nemotron is used through Nebius Token Factory under its terms.
