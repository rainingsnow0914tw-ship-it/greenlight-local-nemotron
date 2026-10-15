> **Archived.** This is the README of the original cloud version of Greenlight (released 2026-08-20, before this
> hackathon began). It is kept for reference and history. Links inside it point to the old layout.
> The current project is described in the [main README](../../README.md).

# Greenlight

**A nine-agent production crew for screenwriters who don't speak AI.**

Screenplay in. Storyboard, casting, prompts, footage, narration, cut, subtitles out —
with the cost and the odds of success quoted *before* anything is spent.

Built for [All Things Agentic](https://allthingsagentichackathon.devpost.com/) and
[Agentic Cinema: The Blockbuster Hackathon](https://agentic-cinema.devpost.com/) (ClickHouse track).

🌐 **Live demo:** https://greenlight-demo-309793842076.us-central1.run.app

> The demo runs on real data — every number, every clip, every ClickHouse row you can
> open there was produced by an actual run. Generation buttons are disabled so visitors
> cannot spend the author's budget; everything already generated plays and downloads
> normally, and the learning-loop panel queries the live database.

---

## Start here: what this actually feels like

A screenwriter has a 45-minute script on her desk. She has never written a prompt in
her life. She drags the file onto a page.

Ninety seconds later she is looking at 37 scenes, 6 characters, and a sentence she has
never been told by any tool before: *this chapter is 12 shots, it will cost $4.50, and
about half of those shots will come out right on the first try.*

She picks a visual style. The buttons themselves tell her that photoreal is **ten points
harder** than the anime option next to it — so she picks anime, and the estimate updates
while she watches. She casts her characters from generated candidates, three at a time,
two cents each. Nothing has been generated yet. She has spent forty cents.

Then she presses one button and watches twelve shots render, one at a time. After each
one the system looks at the footage before she does, tells her what it found, and waits
for her to say *keep* or *again*. When she's done: one file, narration mixed in,
subtitles burned in, in the language she chose.

She never typed anything except the script.

**That middle layer — the part she doesn't know how to do — is the entire product.**

---

## The problem

There are far more good scripts in the world than there are people who know how to
turn one into video. Text-to-video models are extraordinary — and almost unusable
if you don't already know how to write a prompt, which quality tier to pick, or how
much a three-minute film is going to cost you before you press the button.

The writers we care about are not going to learn prompt engineering. They shouldn't
have to.

**Greenlight takes a screenplay and returns a production plan you can actually decide
on**: a shot list, the prompts, quality options with real costs, and — the part
nobody else shows you — an honest estimate of how likely each shot is to come out
right the first time. Then it generates the film, watches its own footage, and hands
you the file.

> **Design rule #7, non-negotiable:** the user never types anything except the
> script itself. If a text input appears anywhere in this product (other than file
> upload), the design is wrong. Missing information is resolved by generating 2–4
> candidates and letting the user pick — never by asking them to describe what they want.

---

## The crew: nine agents, one producer

Every agent is a Gemini model on Vertex AI, assigned by the shape of its job. Pro takes
work that needs the whole screenplay in its head; Flash takes work with clear rules and
formats; Flash-Lite takes high-volume batch work. Each one is a separate file with a
Pydantic schema on both ends — what comes back is validated, not trusted.

| Agent | Model | What it does |
|---|---|---|
| `script_parser` | 3.1 Pro | Reads the whole screenplay: scenes, characters, dialogue, runtime. Recognises that "Luna" and "月音" are the same person. |
| `segmenter` | 3.1 Pro | Cuts 37 scenes into filmable chapters, each priced individually. |
| `shot_planner` | 3.1 Pro | Storyboards a chapter: shots, durations, camera, physical constraints, frame links, voice role — carrying the memory block distilled from your own history. |
| `character_designer` | 3.1 Flash Image | Interprets each character from the script (labelling every inference), generates 3 candidates per round, takes reroll directions. Given a face — uploaded, or the locked card — it keeps the person and re-renders only the look. |
| `content_screener` | 3.1 Pro | Pre-scans dialogue against 8 content-policy categories before a cent is spent, in all three languages. |
| `prompt_writer` | 3.6 Flash | Turns each shot into a model-ready prompt in the prompt language of your choice, with the reasoning behind every constraint. |
| `frame_auditor` | 3.6 Flash | **Watches the finished clip** — counts the people in frame against the plan, catches stray on-screen text — and writes its findings into the retry. |
| `voice_profiler` | 3.6 Flash | Listens to a character's first generated voice and writes the timbre back as a fixed attribute, so the same character sounds the same in every shot. |
| `subtitle_translator` | 3.6 Flash | Trilingual subtitles with measured timing offsets across segments. |

Plus three engines that aren't agents: **video_gen** (Gemini Omni Flash), **tts**
(Gemini 3.1 Flash TTS) and the ffmpeg **assembler / audio_mix**. And one memory:
**ClickHouse**, reached through the official MCP server, which every generation writes
to and every plan reads from.

Nine agents do the work. A handful of design decisions are what make it feel like a
producer instead of a pipeline — those are the rest of this README.

---

## The learning loop closes on the retry, not at the end of the project

This is the part we'd ask you to look at first. Greenlight doesn't learn *after* you
finish a project and go back over the results. It learns **between one take and the
next**, and the loop is closed by the time you've pressed a button.

**1. It watches the clip it just made.** The moment a shot finishes rendering,
`frame_auditor` — a Gemini model — is handed the actual video and asked for observable
facts: how many human figures are in frame versus how many the shot called for
(non-human characters excluded), and whether text has appeared on screen that the shot
never asked for — the two failure modes video models produce most. It reads the whole
clip, not a thumbnail. Its findings land in three places at once: on the shot record
(permanent, per shot), on the shot card as a warning you see before you decide, and in
the project's decision log with a timestamp.

**2. When you press *again*, the previous take is judged and remembered *first*.** Before
a single new frame is generated, the rejected take is written to ClickHouse — with the
auditor's findings attached as the rejection reason. What kind of shot gets sent back,
and why, is exactly what this system most needs to learn, and it is captured at the
moment the human decides, not reconstructed later.

**3. Then the retry carries the evidence.** The auditor's structured findings — not free
text, the fields — are compiled into an English correction block and added to the retry
prompt: *the previous take showed two people; this shot has one*; *the previous take
contained on-screen text; none is wanted*. Only when there is something to correct,
never as boilerplate. A reshoot is not the same dice rolled twice; it is the same shot
with the last failure named.

**4. And the number you're shown moves.** Every row written in step 2 feeds the success
estimate immediately, so the odds quoted for the next shot already reflect the one that
just failed. Once a style has 20+ observations, the refinery distils what worked into a
memory block that goes into the *next storyboard* — so the plan itself, not just the
prompt, gets better.

The auditor observes; it never adjudicates. Passing or failing a take is always a human
button, because a model watching a clip can be wrong about a reflection or a statue, and
auto-rejecting would turn a misread into money spent for nothing. That's also why the
ledger's pass rate is worth trusting: every verdict in it was a person's.

---

## Continuity: every shot starts where the last one ended

Adjacent shots in the same scene are **frame-chained**: the last frame of the previous
shot becomes the first frame of the next. Without this, every shot re-imagines the room
from scratch and the colour temperature, angle and set dressing drift across the scene.

The chain is deliberate about where it breaks. Scene boundaries are the primary break
point — a new location should start fresh. Inside a scene the chain runs up to ten shots
(a config value, per quality tier); when it has to break early, the prompt is told
*same location, same set dressing, same light* so the model re-anchors instead of
re-inventing.

What chaining buys is continuity, not a higher pass rate — and the ledger is honest
about that: with 39 first takes recorded, chained shots pass 78% of the time and
unchained ones 75%. The system's own distilled note says so, in so many words, and tells
the planner to choose chaining by what the cut needs rather than by the odds.

---

## Narration: say it first, then shoot

Films aren't only people talking on camera. A documentary has an off-screen narrator; a
drama has a voice-over; a tour has a guide. Greenlight models this in three layers, each
one switchable in config:

1. **Voice role per shot** — `character` (on-camera, lip-sync), `offscreen`, or
   `narrator`. Only on-camera speech is phrased to trigger lip movement; narration is
   phrased so mouths stay closed and still. The shot planner sets this; you never do.
2. **Narrative format per segment** — drama, documentary, interview, explainer
   ([`config/formats.yaml`](config/formats.yaml)). Chapter one can be narrated and chapter two can be acted.
   This lives at the *segment* level on purpose: visual style must stay constant across
   the whole film (the character stills are generated in it), but the storytelling mode
   can change from chapter to chapter.
3. **Narration through Gemini TTS, recorded before the picture.** The line is
   synthesised first, its real duration measured, and *that* decides how many seconds of
   video to generate (3–10 s, adjustable). The video model only produces picture and
   ambience; ffmpeg mixes the voice in afterwards.

That third layer solves three problems with one design: lip-sync can't go wrong because
nobody on screen is speaking; the narrator's voice can't drift because it's one TTS voice
with a directed delivery ([`casting.yaml`](config/casting.yaml) holds the director's note to the voice actor —
pacing, tone, where to breathe); and the narration is not subject to the video model's
content filter, so the script's words reach the soundtrack intact.

For on-camera characters, `voice_profiler` handles consistency the other way round: it
listens to the first generated performance and writes the timbre back into the character
sheet, so every later shot describes the same voice.

---

## When the safety filter says no

Dark dialogue is normal in drama, and content filters don't know that.

`content_screener` pre-scans the screenplay against eight policy categories **before any
generation**, in whichever of the three languages the script is in, and shows an action
card per flagged line. When a line would trip the video model's filter at generation
time, Greenlight doesn't ask you to rewrite it: it generates the shot without the spoken
line and places the line in the subtitle layer instead. **Not one word of the screenplay
changes.**

---

## Your face, your character, your wardrobe

Three people, same product.

A screenwriter wants to be in her own film. She drags a photo of herself onto the
character card. Not a headshot — the one she has, taken from the side, half her face
behind a laptop screen. The designer looks at it and comes back with three versions of
her in the film's style: a black turtleneck, a pale blue shirt, an olive jacket. Same
face in all three. She picks one, and from that moment every shot she appears in is her.

An animator has been drawing the same character for two years. He drops his own
character sheet. The designer doesn't redraw who she is — it takes her face as given and
proposes how she looks in *this* film's visual style. His character, our production.

A director gets to chapter seven, where the lead has moved out of the city and dresses
differently now. She doesn't recast. She opens the character, chooses *chapter 7*, and
asks for a new wardrobe. The face is locked; only the clothes and hair are up for
discussion — three candidates, two cents each. She can also just point chapter seven at
the outfit chapter three already used. From then on, chapter seven's shots pull that
look and every other chapter keeps its own.

Underneath, this is one idea: **the face is an input, the look is an output, and
wardrobe belongs to a chapter, not to the whole film.** An uploaded photo is never used
as the character card itself; it is what the designer is handed. A character locked from
generated candidates works the same way — the chosen card becomes the face for every
later reroll, so "new wardrobe" no longer quietly means "new person". Segment looks are
resolved at generation time: the shot asks which look chapter *N* uses and gets that
image as its reference.

There is exactly one thing to fill in on the way, and it's a checkbox: before an upload
is accepted, the user confirms they have the right to use that image and that any
likeness or copyright liability is theirs. It is stored with a timestamp on the project.
People will upload other people's photos, so the product says out loud whose
responsibility that is.

We tested it the unkind way. Left: the photo we uploaded — a side profile, half hidden
behind a monitor. Second: the character card that had been locked before. Right: the
three cards the designer produced from the photo.

![Uploaded photo → three character cards, same face, three wardrobes](docs/images/identity_upload_compare.jpg)

Unmistakably the same man in all three, none of them wearing what he wore in the photo.
And the wardrobe variant, on a character already locked — one face, four outfits:

![Locked card → three wardrobe variants for one chapter](docs/images/wardrobe_variants_compare.jpg)

---

## Two findings worth the whole project

**1. Casting is the cheapest 20 points you will ever buy.**

The same shots — dialogue, night exterior, single-character close-up — differ only in
whether a character reference exists:

| | First-pass success | Expected retry cost |
|---|---|---|
| Before casting | 29% | $1.86 |
| After casting | **49%** | $1.17 |

**+20 points, 37% less retry spend** — and a casting round costs $0.06.
That is the entire argument for the cost ladder this product is built around:
a candidate image is $0.02, a ten-second shot is $1, a finished segment is about $20.
**Put the hesitation where it's cheap.** Casting is therefore a hard gate: no shot
generates until every character in it has an approved reference image.

**2. Photoreal is the hardest style, and animation is far more forgiving.**

This is the opposite of what most people assume, and it changes budgets:

| Style | First-pass success | Cost for the same scene |
|---|---|---|
| Photoreal | 35% | $13.97 |
| Anime (2D cel) | **50%** | **$12.34** |
| Watercolour storybook | 49% | $12.44 |

Uncanny valley, quantified: audiences catch flaws in real humans instantly, while the
same flaw in an animated frame reads as style. So style isn't a prefix we bolt onto a
prompt — it changes **which physical constraints even apply**. Hand anatomy gets
weighted ×1.3 for photoreal and ×0.5 for 2D animation, because over-constraining hands
in a cel-shaded frame actively fights the art style.

We put this difference **on the style buttons themselves** (−10pt / +12pt / +15pt).
It's the most counter-intuitive and most valuable thing we know, and hiding it in the
backend would be a waste of it.

---

## Why ClickHouse is load-bearing, not decoration

The single most useful number this product produces is **"how likely is this shot to
work on the first try."** Without history, that number is a guess.

Every generation writes one row to ClickHouse — shot characteristics, parameters,
quality tier, which attempt this was, whether the take was used, what it cost, how long
it took, which model, and what the auditor found. The next time you plan a similar shot,
we query that history and compute a real pass rate instead of a prior — and label which
of the two you're looking at, with the sample size.

**Accessed through the official `mcp-clickhouse` MCP server**, not a direct driver.
[`services/clickhouse_mcp.py`](backend/app/services/clickhouse_mcp.py) runs the MCP server as a child process over stdio and
exposes a client whose interface matches a direct driver exactly, so the dozen query
sites in [`ledger.py`](backend/app/services/ledger.py) are ordinary SQL. **Self-hosted on Compute Engine, reachable only
inside the VPC** — Cloud Run talks to it over Direct VPC egress; port 8123 is never on
the public internet.

Two materialised views do the work that makes this honest:

| View | What it answers |
|---|---|
| `constraint_stats` | For each physical constraint, what pass rate do we actually observe? |
| `calibration_stats` | Are our predictions systematically optimistic or pessimistic? |

The calibration view is there because a success-rate predictor that is never checked
against reality is just a confident number. In testing we seeded a deliberate ×0.92
bias into the ledger; the report caught it and reported **-6.3 points, optimistic**.

**Three layers of memory, all of them live:**

| Layer | Where | What it does |
|---|---|---|
| Ledger | ClickHouse | One row per generation, written at the moment you press *keep* or *again* |
| Distilled craft notes | Firestore | Once a style has 20+ observations, the refinery condenses what worked and what didn't into a memory block |
| Carried into planning | `shot_planner` | That block is injected into every storyboard, so the plan itself gets smarter |

Distillation runs after every segment is assembled, and on demand
(`POST /api/config/learning/refine/{style}`). What the photoreal style has learned so far,
from 39 first takes — written by the system, not by us:

> Prefer single-character shots (85% first pass, n=27); avoid two-shots (33%, n=3, small
> sample). Close-ups, wides and mediums pass at 82–86%; medium close-ups 67%; inserts 50%
> (n=4). Static camera 73% (n=26); handheld 60% (n=5). Frame chaining makes no significant
> difference to pass rate (78% vs 75%) — choose it for the cut, not the odds. The system's
> estimates run conservative: shots predicted at 20–50% actually passed 67–100% (n=17), so
> don't drop a shot for a low score.

Every one of those sentences is queryable in the ledger, and the last two corrected
assumptions we had written into the priors ourselves.

**And you can see it, not just be told about it.** The learning-loop panel on every
project page — and the banner on the home page — shows live numbers from ClickHouse
(as of this writing: 66 generations, 78.4% first-pass, 36 distinct shots), and expands
to the **raw ledger rows**: timestamp, shot, attempt number, verdict, the prediction at
the time, why it was rejected, cost, latency, model, table name and host. Statistics can
be fabricated; timestamped rows carrying model ids and real costs cannot.

Three things we're explicit about rather than quietly hiding:

- **Cold start is labelled.** On day one the ledger is empty and every estimate says
  "prior estimate, no observations yet," along with how many samples it would take to
  switch over. It never pretends to have data it doesn't have.
- **Observed constraint lift is not causal.** Constraints fire automatically based on
  shot difficulty, so harder shots get more constraints — that's selection bias, and
  the report says so. Proving causation needs an A/B design, and we say that instead
  of quoting a number we can't defend.
- **Backfilled rows are flagged.** Rows reconstructed after the fact carry
  `is_backfill`, and the calibration report excludes them by default — their predicted
  rates were recomputed using priors that had already been tuned against those very
  outcomes. Including them would be marking your own exam with the answer key open.

**Your history is yours.** The numbers you see come from your own projects, not from a
pool of other people's — the more you shoot, the less it costs you to get what you want.

If ClickHouse is unreachable, planning degrades to priors, keeps working, and the UI says
so. The dependency is real, but it is not a single point of failure for the user.

---

## The gates: where money changes hands

```
screenplay  ──▶  script_parser    ──▶  scenes, characters, runtime
                       │
                       ▼
                 segmenter         ──▶  chapters, each priced
                       │
                       ▼
                 shot_planner      ──▶  shots, durations, frame links, voice roles
                       │                (carries the memory block distilled from history)
                       ▼
                 content_screener  ──▶  flagged lines, before any spend
                       │
                       ▼
              character_designer   ──▶  3 candidates per character, per round
                       │
                       ▼
                 prompt_writer     ──▶  every prompt, yours to take elsewhere
                       │
                       ▼
             Gemini Omni Flash     ──▶  test shot → batch → frame_auditor → assembly
              + Gemini TTS
                       │
                       ▼
                 ClickHouse        ──▶  one row per generation, feeding the next plan
                (via mcp-clickhouse)
```

| Step | Cost | Gate |
|---|---|---|
| 1. Parse script | ~$0.15 | |
| 2. Plan shots | ~$0.05 | |
| 3. Style + aspect ratio | **$0** | 🛑 buttons, live re-estimate |
| 4. Compare tiers | **$0** | 🛑 six quality tiers side by side |
| 5. Safety pre-scan | ~$0.10 | 🛑 flagged lines with an action card each |
| 6. **Casting** | $0.06/round | 🛑 **hard gate — nothing generates until every character is locked**; bring your own face, or change wardrobe per chapter |
| 7. Export prompts | **$0** | 🛑 take them to any other tool |
| 8. Test shot | ~$1 | 🛑 one shot before committing to the batch |
| 9. Batch generate | ~$1/shot | 🛑 per-shot approval, or one-click |
| 10. Assemble segment | **$0** | narration mixed, frame-chained |
| 11. Assemble film | **$0** | all segments into one, missing ones named |
| 12. Subtitles | **$0** (~$0.002 first translation) | download `.srt` or burn them in |
| 13. Rewind | **$0** | 🛑 preview the cost, then commit |
| 14. Media library | **$0** | keep the good ones, delete the rest |

Most gates cost nothing. That's deliberate: **the expensive decisions should be made
while the decisions are still free.**

**Money is calculable, and it's a choice.** Every quality tier has a price in
[`config/pricing.yaml`](config/pricing.yaml), and the system prices your selection before you commit. Bigger
budget, higher tier and resolution. Smaller budget, cheaper tier, and you still get the
whole film. Same script, you can shoot it cheap first to confirm the story lands, then
re-shoot a handful of key moments at the top tier.

### A real gate is an event, not a polite request in a prompt

> "We need this to be systematic. We can't be *begging* Gemini inside the prompt.
> I want a real gate — you press next, and only then does it send the instruction."

Pressing a button fires an event. The event is what makes the backend read the project
state, assemble the prompt, and call the tool. Nothing is left to a model deciding
whether to respect an instruction it was asked nicely to follow. If the gate is closed,
no code path reaches the generator. Every gate is a state transition in
[`schemas/project.py`](backend/app/schemas/project.py).

---

## Two ways to shoot a batch

Both exist because both kinds of people exist.

**Per-shot.** Generate one, look at it (the auditor already has), *keep* or *again*. The
next shot is locked until you decide. This is the default, and it is the reason the
product saves money — you find out the style is wrong on shot two, not after paying for
twelve.

**One-click.** Generate everything that's left, continuously, with a progress bar, a
stop button, and a pointer to the cut when it finishes.

> **One-click is not auto-approve.** Every shot still lands in *awaiting your review*.
> Auto-approving would write "accepted" into the ledger for takes no human ever looked
> at — and the honesty of that pass-rate number is the core of the product. We are not
> going to poison it for a nicer-looking progress bar.

The loop runs in the browser, one request per shot: you can see *shot 3 of 12*, stop
midway, and close the tab without leaving half a batch in limbo.

### Every take is kept, and the director picks

A retake is not always better. Every generation is money the user has already spent, so
every take is saved as its own file — a reshoot never overwrites the previous one. When
a shot has more than one take, the shot card grows a take switcher: pick any take as the
one the film uses, for $0, and switch back any time. The assembled segment marks itself
stale so the next re-assembly (also $0) picks up your choice.

The same rule extends down the stack: the narration mix and any extension write back to
*their own take's file*, and the storage bucket keeps versioned history — nothing a user
paid for is ever silently destroyed.

---

## Going backwards without starting over

> "What if he doesn't like the first batch? Say I want to redo chapter one in live
> action — that's terrible if there's no reset."

Deciding you want a different visual style *after* you've cast the characters is a
completely normal thing to do. Every stage can be rewound, and rewinding is two steps:

1. **Preview** — the system tells you exactly what this will invalidate: "3 locked
   characters ($0.18 spent), the exported prompts, the approved test shot."
2. **Commit** — only after you've seen the price.

Three things rewind deliberately does **not** touch:

- **Your files.** Every image and video stays in the media library.
- **Your casting candidates.** Locks are released; the images you already paid for stay.
  One of them might still be the right one.
- **The ledger.** What the system learned survives starting over. Re-run a project and
  it should be *smarter* than last time, not amnesiac.

## The media library

Everything the project generates accumulates somewhere, and you can see all of it.

> "Let the user pick which ones to delete. They won't all go at once — maybe they keep
> the good-looking ones. Same with the images. Delete the ugly ones first."

So: every image and video, with previews, deletable **one at a time**. There is no
"select all" and that is not an unfinished feature — a batch button encourages wiping
everything, which is the opposite of the point. Large files are served straight from
Cloud Storage through short-lived signed URLs, so playback and download never wait on the
app server.

---

## Honesty, as a feature

- **Inference sources are shown.** When a script doesn't describe a character's
  appearance — which is most of the time — we infer it and label exactly what from:
  an explicit description, a line of dialogue, an age signal, a narrative function,
  a way of speaking. "From the script" and "inferred by the system" are different
  claims and users deserve to know which they're looking at.
- **Estimates come as ranges, not single numbers.** We quote the precision we actually
  have, and the ledger tells us what that is.
- **Missing pieces are named.** Assemble a film while two segments are unrendered and
  it will tell you which two are missing rather than quietly handing you a short film.
- **Prompts are exportable** — in Chinese, English, or Japanese. Plain text, Markdown
  with the reasoning behind each constraint, or JSON. Yes, you can take them to a
  competitor. A tool confident enough to let you leave with your work is a tool worth
  trusting.
- **Prompt quality is a supervision problem, not a text problem.** Someone has to notice
  that the screen will face away from camera, that the fingers will come out wrong, that
  a bodiless character must not be described as a man. That someone is the constraint
  engine before the shot and the frame auditor after it. A raw text box does neither.

---

## Trilingual, in three independent dimensions

Interface language, prompt language, and film language are **three separate choices**,
not one setting:

| Dimension | What it controls | Where it lives |
|---|---|---|
| Interface | what the user reads | frontend state |
| Prompt | the language the model is described to in | `ShotList.prompt_language` |
| Film | what characters say, and the subtitles | `ShotList.film_language` |

A Taiwanese writer can work in a Chinese interface, write prompts in Japanese —
where terms like 間 and 余白 are simply more precise for animation — and ship a
Japanese-dubbed film because that's where the audience is.

Subtitles come in all three, either as `.srt` or **burned into the picture**, and the
burned file is saved alongside the original rather than replacing it — so changing your
mind about the language costs nothing. Chinese and Japanese subtitles are wrapped by our
own line-breaker, which breaks at punctuation and never leaves a punctuation mark at the
start of a line — because CJK text has no spaces and the subtitle is for a human to read.

> Film-level subtitles are not the segment files stapled together. Each segment's
> timeline starts at zero, so the offsets are accumulated from **measured** segment
> durations, not the durations we requested.

What is **not** duplicated per language: cost estimation, success prediction, the
constraint engine, casting, project state. Those must produce identical results in
every language. Only prompt-writing conventions and UI copy live in per-language rooms
(`config/prompts/{zh,en,ja}.yaml`).

---

## Built to run in production, not just to demo

- **One deadline for the whole request.** Every long route runs inside a single time
  budget (`_ROUTE_BUDGET_SECONDS`, below the platform's own limit), and every retry
  draws from that same budget. When time runs out, the failure is recorded and the clip
  state is saved — the user and the ledger both know what happened.
- **The system finds its own rate limit.** Per-minute quotas for generation models aren't
  published, so `rate_governor` doesn't guess: it uses AIMD (the same control law as
  TCP) — back off multiplicatively on a 429, creep forward additively on success —
  converging on the invisible line without crossing it.
- **Demo and production are one build.** The public demo is the *same container image*
  as the production service, with one environment variable. Three guards sit on the
  three exits money or media can leave through — the Gemini client, the video generator,
  and the media store — and return a clear `402` with an explanation instead of spending
  or overwriting anything. A static check in the verification suite makes sure every
  route lets that signal through.
- **Fresh credentials, always.** Tokens are refreshed on their real expiry and forced on
  a 401, so a retry is never the same failed request replayed.
- **Config-driven everywhere.** Prices, priors, physical constraints, styles, narrative
  formats, casting directions, content policy and per-language prompt conventions all
  live in `config/*.yaml`, each with a schema; the loader refuses to start if a file and
  its schema drift.
- **Right-sized for video.** Cloud Run at 2 GiB / 2 vCPU with CPU throttling off, so
  ffmpeg and audio mixing run at full speed.

---

## How we know it works

[`scripts/verify_wiring.py`](scripts/verify_wiring.py) — **140+ checks, $0, no AI calls.** It drives the real HTTP
routes with a real screenplay fixture and stubs only the calls that cost money.
Project state goes to a temp directory; the ledger goes to a throwaway database that is
dropped afterwards. It checks that both learning-loop APIs return the same shape, that
every route lets the demo-mode signal through, and — the rule that named the script —
that every module is actually *called* by something.

[`scripts/health_check.py`](scripts/health_check.py) — 25 checks against the live deployment: model ids reachable,
ClickHouse connected and answering, fonts usable for CJK subtitle burn-in, media store
writable.

`/api/health` on the running service reports the same, and the planning API returns
`confidence: blended` only when real observations came back through the ClickHouse pipe.

---

## Architecture

Four diagrams — the whole system, the gates, the learning loop, and how failure is
handled: **[docs/architecture.md](docs/architecture.md)**
(rendered overview: [docs/architecture_overview.png](docs/architecture_overview.png)).

---

## Contest compliance

- **Google-only at runtime.** Every model call goes through `google-genai` (the Google
  GenAI SDK) on Vertex AI:

  | Job | Model |
  |---|---|
  | Screenplay parsing, segmenting, storyboarding, safety pre-scan | `gemini-3.1-pro-preview` |
  | Prompt writing, subtitle translation, frame audit, voice profiling | **`gemini-3.6-flash`** |
  | Cheap batch work | **`gemini-3.5-flash-lite`** |
  | Character stills | `gemini-3.1-flash-image` / `gemini-3-pro-image` |
  | Video | `gemini-omni-flash-preview` (Interactions API) |
  | Narration | `gemini-3.1-flash-tts-preview` |

  No non-Google model, agent framework, or AI API is imported or called anywhere in
  this repository. (ffmpeg is a codec, not a model.)

- **Google Agent Framework.** `google-genai` SDK and ADK throughout — see
  [`backend/app/services/genai_client.py`](backend/app/services/genai_client.py), the single entry point every agent goes
  through.
- **Google Cloud infrastructure.** Cloud Run (the app), Firestore (state and distilled
  memory), Cloud Storage (media), Compute Engine (ClickHouse), Secret Manager, Cloud
  Build, Cloud Logging.
- **ClickHouse via the official MCP server.** `mcp-clickhouse` is spawned and queried at
  runtime — see [`backend/app/services/clickhouse_mcp.py`](backend/app/services/clickhouse_mcp.py), [`services/ledger.py`](backend/app/services/ledger.py), and
  [`deployment/clickhouse/schema.sql`](deployment/clickhouse/schema.sql). Verified live: `/api/health` reports
  `clickhouse: connected`, and planning returns `confidence: blended`, which is only
  possible if real observations came back through that pipe.
- **Built from scratch during the contest period.** Work started 2026-08-07. No code
  carried over from earlier projects; no pre-existing work is incorporated.
- **Hosted and public.** https://greenlight-demo-309793842076.us-central1.run.app
- **MIT licensed.**

---

## Running it

Full, step-by-step instructions — prerequisites, environment, permissions, deploy flags,
verification, troubleshooting — are in **[docs/SETUP.md](docs/SETUP.md)**. Three paths:

| Path | Time | Cost |
|---|---|---|
| **Just look** — the public read-only demo | 0 min | $0 |
| **Run locally** — Python 3.13, Node 20+, a Google Cloud project with Vertex AI | ~15 min | $0 until you press a generate button |
| **Deploy to your own Google Cloud** — one container on Cloud Run, same image as ours | ~45 min | Cloud Run pennies + generation at [`config/pricing.yaml`](config/pricing.yaml) prices |

The 60-second version of "run locally":

```bash
git clone https://github.com/rainingsnow0914tw-ship-it/greenlight.git && cd greenlight
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt && (cd frontend && npm install)
gcloud auth application-default login
cp .env.example .env                               # set GCP_PROJECT_ID — the only required line
python scripts/demo_plan.py                        # $0: planning stack end to end, no network
```

Then `cd backend && uvicorn app.main:app --port 8020` in one terminal,
`cd frontend && npm run dev` in another, and open http://localhost:5173.

ClickHouse is optional locally — planning degrades to priors without it, and the UI
says so. [`deployment/clickhouse/docker-compose.yml`](deployment/clickhouse/docker-compose.yml) brings one up in a minute.

### Scripts worth knowing

| Command | Cost | What it does |
|---|---|---|
| `CLICKHOUSE_ALLOW_DROP=true python scripts/verify_wiring.py` | **$0** | End-to-end checks against real routes, no mocks at the boundary |
| `python scripts/health_check.py` | $0 | 25 live checks against the deployment |
| `python scripts/demo_plan.py` | $0 | Planning stack end to end, no API calls |
| `python scripts/parse_script.py <file>` | ~$0.15 | Parse a real screenplay |
| `python scripts/plan_segment.py <id> <n>` | ~$0.05 | Storyboard one segment |
| `python scripts/design_character.py <id> <name>` | ~$0.065 | One casting round |
| `python scripts/refine_memory.py <style>` | $0 | What the system has learned so far |
| `python scripts/ledger_report.py` | $0 | Calibration report from ClickHouse |
| `python scripts/verify_apis.py --check all` | ~$0.65 | Confirm every model ID is live |

---

## Status

**The whole path works, end to end, in production.** Verified against a real 45-minute
screenplay: a full chapter — 22 shots, frame-chained, narrated, assembled and subtitled
in three languages — is playable on the demo site now, roughly three minutes of finished
film.

| | |
|---|---|
| Parsing · segmentation · shot planning · safety pre-scan | ✅ |
| Cost estimation · success prediction · constraint engine · prompt assembly | ✅ |
| Casting: interpretation, generation, gate logic, reroll directions | ✅ |
| Bring-your-own face (with consent record) · locked-face rerolls · per-chapter wardrobe | ✅ |
| Video generation: test shot, per-shot batch, one-click batch | ✅ |
| Frame audit · evidence-carrying retry · frame chaining · auto chain-break with cards | ✅ |
| Narration: voice roles · narrative formats · TTS-first timing · audio mix | ✅ |
| Segment assembly · film assembly · trilingual subtitles · burn-in | ✅ |
| Rewind with cost preview · media library · every take kept, director picks | ✅ |
| ClickHouse ledger via MCP · distillation · calibration · raw rows in UI | ✅ |
| Trilingual React frontend · read-only public demo, deployed | ✅ |

Total spend to date: about **$86** — for everything above, including all the experiments
that produced the numbers in this README.

---

## Design principles

1. **Nothing hardcoded.** Prices, success priors, physical constraints, styles, and
   casting directions all live in `config/*.yaml`. The config loader refuses to start
   if a YAML file and its schema have drifted apart.
2. **Schemas are the boundaries.** Every layer talks to the next through a Pydantic
   model. Whether an agent's output is acceptable is decided by validation, not by hope.
3. **One thing per file.** No 1000-line [`main.py`](backend/app/main.py).
4. **Every spending action passes a gate.** Before you press it, you can see what it costs.
5. **Cold start needs priors.** Say "this is a prior estimate." Never fake having data.
6. **Solve it in the architecture before you solve it in the prompt.** Duration limits
   are clamped in code, not requested politely of a model.
7. **Zero input.** Upload, then buttons. Forever.

---

MIT © 2026
