# Why a take failed — seven laws and an investigation order

Distilled from 100+ failed takes while producing a 10-chapter anime series on an
open-weights model that generates picture **and** voice from one text path.

The most important lesson is not any single law. It is this: **when a take fails,
the instinct is to add more words to the prompt — and that instinct was wrong
seven times out of seven in one day.** Successful fixes are almost always
*subtraction* or a *setup change* (a picture, a duration, a split), not more text.

## The seven laws

1. **Gaps get filled.** Whatever you did not assign, the model invents: an object
   appears in an empty part of the frame, a camera move reveals a region the
   reference image never showed and the model furnishes it, extra seconds get filled
   with invented speech, a dark room turns its lights on when a long shot forces the
   model to redraw a big area (it falls back to "bedrooms are bright").
2. **One thing, one definer.** If the image already defines something, don't
   describe it again in text; if the text needs something the image doesn't have,
   the model has no choice but to conjure it mid-shot. Text controls *what exists*;
   it cannot control *where things are*. Pictures win over text when they disagree —
   even the voice is inferred from the face in the first frame.
3. **Duration is a container.** Too short and actions get skipped or rushed; too long
   and actions repeat or the gap fills (law 1). One narrative action ≈ 3–4 s.
4. **Every word adds weight.** Mentioning something — even to forbid it — gives it
   weight. Writing mouth, lips, smiling, sighing, or speaking in the performance line
   makes the model produce sound. Adding a paragraph of prohibitions makes things worse.
5. **Lock only the first frame.** Locking both first and last frames freezes the pose.
6. **The measuring tool can be wrong.** A failing check and a passing check look the
   same. Speech-to-text loops repeated words on its own; homophones hide real
   mispronunciations. Ask what conditions a measure was calibrated under.
7. **Audio and video are generated together.** One text path conditions both.
   Changing a visual word re-rolls the voice. If a line *describes drawable things*,
   the model draws them and the speech collapses. Unlabelled language gets guessed.

## Investigation order

Check in this order and stop at the first real cause:
**duration → images (first frame / reference images) → frame locks → seed →
shared mechanism (something every failing shot has in common) → prompt text last.**
When you do reach the prompt, look first at what was written *too much*, not what is missing.

Before reasoning at all, look for a **control group**: the most similar take that
*succeeded* (same character, same first frame, same shot type). Diff them field by
field. Anything only the failing take has — especially something added recently —
is the prime suspect.

## When the laws don't explain it: ask the platform itself

Models and nodes change; documentation lags the code, and these notes lag the
documentation. When no law fits (`law: 0`) or you are unsure, put a question in
`research_query` — the render model's official docs and known issues will be
searched and handed back to you. Do this especially **before concluding "the model
can't do this"**: we once wrote down "this angle is impossible because the first
frame is locked" as a model limit; the node's own documentation showed the image
could be pinned to any frame. The limit came from how the tool was used.

## Fix categories

- `duration_split` — shorten, or split one shot into two (second continues from the first's last frame)
- `first_frame_or_reference_image` — give a picture that defines the thing (add it to the first frame, or a reference image that covers the region)
- `remove_text` — delete the word or sentence that summons the problem
- `change_blocking` — change what the character physically does or where they face
- `rewrite_dialogue` — change how the line is phrased (keep the meaning)
- `name_hands_props` — say which hand holds what; don't give one hand two jobs
- `reroll_seed` — same setup, new seed (only when nothing structural differs from a success)
- `add_text` — add description (rarely right; justify it explicitly)

## Worked cases (symptom → the wrong first guess → true cause → fix)

- **A counted object gains one** (seven light points become eight when they light up).
  Guess: write "seven" more clearly / reroll. True cause: the reference image showed a
  finger touching the card, and "a ring plus a center" is the model's default layout;
  writing the number or asking it to rotate makes the count drift.
  Fix: reference image with fingers only on the edge and the ring's center empty; never write the number. (`first_frame_or_reference_image` + `remove_text`)
- **A prop appears in her hand again and she puts it down a second time.**
  Guess: the model adds random actions. True cause: shots that don't chain used a
  generic first-frame card from earlier in the scene, when the phone was still in her hand.
  Fix: after a prop moves, later shots start from the previous shot's last frame. (`first_frame_or_reference_image`)
- **The AI character's void turns into a room with a desk and a webcam.**
  Guess: the reference image of the void should be enough. True cause: a reference
  image is a suggestion, not a fixed frame; a 14 s line gave it room to drift.
  Fix: give a first frame of the void. (`first_frame_or_reference_image`)
- **A hand holding a strange toy appears next to her chin in a close-up.**
  Guess: random artifact. True cause: she is listening to a phone, so the model
  expects a hand near the face, but neither the image nor the text says what the hand holds.
  Fix: a first frame where both hands hold the white phone. (`first_frame_or_reference_image`)
- **The camera starts pushing in and the composition drifts after adding a reference image.**
  Guess: more references are safer. True cause: the reference showed a face that never
  appears in this shot; the model moves the camera to go find it.
  Fix: only feed references for things visible in this shot. (`remove_text`-style subtraction of the image)
- **An object appears twice; the room layout changes.**
  Guess: description not precise enough. True cause: the same noun was written twice.
  Fix: name each thing once, then use pronouns. (`remove_text`)
- **Two people's lines in one shot; the man's line comes out in the woman's voice.**
  Guess: the model cannot do two-person dialogue. True cause: both lines were crammed
  into one unlabelled line. Fix: one speaker per line, named, in speaking order, each
  with their own preceding action. (`rewrite_dialogue`)
- **Three seconds of gibberish before the line.** Performance line: "looks at her knees
  while talking, lowers her head halfway through". Guess: reroll. True cause: "talking"
  in the performance line, and nothing physical to do before the line.
  Fix: remove the speaking words; give a body action first. (`remove_text`)
- **Transcript ends with "hug hug hug hug".** Guess: the shot is too long and the gap
  was filled — shorten it. True cause: the speech-to-text model's own repetition loop;
  with word timestamps the transcript is clean. Fix: nothing — the take was good. (law 6)
- **An extra invented sentence fills the start of a short line containing "Luna".**
  Guess: reroll. True cause: duration was computed counting "Luna" as 4 syllables;
  1.3 s of slack got filled. Fix: count Latin names as length/2 syllables, shorten the
  shot to fit. (`duration_split`)
