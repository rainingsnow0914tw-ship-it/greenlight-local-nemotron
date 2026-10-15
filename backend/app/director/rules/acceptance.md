# Accepting a take

A take is accepted when **two things** are true:

1. **The action is reasonable** — what the character does, where the props are and
   where the camera goes all make sense for the situation and obey physics.
2. **The line is right** — the words, the pronunciation and the lip sync are correct.

That's all. **A take that differs from the written performance line is not wrong.**
The performance line is an instruction, not a contract: if the model adds a
reasonable action, takes a different but reasonable path into a pose, or pushes the
camera in or out a little and what it reveals makes sense, accept it — that is the
model's own direction.

> One shot went through seven versions because the director treated "she sat up
> instead of staying on her stomach" as an error. The producer looked at version six
> and said: the action is reasonable and the line is right — accept.

**To reject a take you must be able to say in one sentence what is unreasonable.**
If you cannot, accept.

## Reasons that ARE grounds for rejection

- Something appears from nowhere, disappears, duplicates, or changes size/shape
- Physics breaks: an object passes through a body, food vanishes before reaching the mouth
- A hard cut inside the shot (the shot should be one continuous take)
- The line is missing, cut off, mispronounced, or extra speech is invented
- The mouth does not move while the on-screen character speaks (or moves when they shouldn't)
- The scene's continuity breaks (costume, lighting state, which hand holds the prop)

## Don't regenerate what you can patch (no GPU needed)

- `cut_head` — the opening is wrong but the rest is good: trim the first N seconds
- `hush_segment` — gibberish before or after the line while the picture is good:
  replace just that span of audio with the shot's own room tone
- `soft_cut` — a hard cut inside the shot between two good halves: turn it into a dissolve.
  Only when both sides are **the same moment continuing** (same pose, same prop
  positions, same framing drifting). If the cut **jumps** — a different pose, a prop
  that moved, a new angle — a dissolve just shows the jump more slowly: regenerate.

Regenerating re-rolls picture *and* voice together, so a patch is preferred whenever
the picture is good. Choose `regenerate` only when the picture itself is unreasonable
or the line itself is wrong; then hand the take to diagnosis.

## Don't trust a single measurement

- A transcript that repeats one word at the end ("hug hug hug") is often the
  speech-to-text model's own loop. If word-level timestamps show the line cleanly,
  the audio is fine.
- A homophone of a character's name in the transcript cannot tell "the transcriber
  misheard" from "the model mispronounced". Ask for a human ear (`human_check`).
- A 20-frame contact sheet can miss fast events (props swapping hands, food reaching
  the mouth). Ask for dense frames around hand-offs before accepting them.

## Verdicts

`accept` · `patch` (with which patch and the time span) · `regenerate` (with the
one-sentence reason) · `human_check` (with exactly what a human should listen or look for)
