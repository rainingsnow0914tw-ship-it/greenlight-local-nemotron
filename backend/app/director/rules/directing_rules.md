# Directing rules for the render model

These rules were learned the hard way while producing a 10-chapter anime series
with an open-weights video model that generates picture **and** voice in one pass.
Every rule exists because breaking it produced a ruined take. The deterministic
gates in `gates.py` enforce the mechanical ones; the rest are for you, the director.

## How the render model behaves (why the rules look the way they do)

1. **Anything you mention gets drawn.** Name an object, a body part or a sound and
   the model puts it on screen — including things you mention in order to forbid
   them. "No rain" summons rain. Never write negations; describe only what *is* there.
2. **Empty time gets filled.** If a shot is longer than the action you wrote, the
   model invents more: extra lines of dialogue, a camera drift, a light turning on.
   Every second must be owned by an action or a line.
3. **Mouth words make it talk.** Writing "speaks", "opens her mouth", "smiles",
   "sighs", "yawns" or any sound-making action makes the model produce extra
   speech or noise. The performance line describes the **body only**; the dialogue
   field carries the words.
4. **Face-only beats don't use time.** Blinking, glancing, closing the eyes,
   frowning take no screen time. If those are the only actions before a line is
   spoken, the gap is filled with gibberish. Put at least one visible body action
   (hand, head, torso, standing, sitting, turning, picking up…) before the final beat.
5. **Walk it step by step.** Write 3–5 body actions in order joined by
   之後 / 然後 / 最後 (after / then / finally). Vague actions get improvised.
6. **Make it clear who speaks.** One speaker: they must be in the frame. A voice from
   off screen: set `voice_role` to `offscreen` and keep that speaker *out* of the frame
   (the render prompt then says the person on screen is not talking). Two people trading
   lines in one frame works — but only as `dialogue_lines`: one line per speaker, in
   speaking order, both speakers in the frame, and each of them gets a body action
   before their line. A single unlabelled `dialogue` with two people's words makes the
   voice switch mid-shot.

## Shot rules

7. **10 seconds maximum.** Long shots fail more and cost more (7 s ≈ 10 min,
   12 s ≈ 24 min of GPU). If a line is too long, split the line across two
   consecutive shots — never delete words.
8. **Long enough for the line.** Duration must cover the spoken line plus about
   1.5 s for the action to land (see `gates.py: speech_seconds`). Never shorten a
   shot below that.
9. **Dialogue comes from the script.** Copy lines as written. You may split a line
   across shots, and you may pad a very short line (under 6 characters) with a few
   words that fit the moment — very short lines get mumbled. Never change meaning.
10. **Each character stays in their own world.** Characters whose `location` differs never
    share a frame (in scene 29, Luna is in her room and Shenxi is in the void): cross-cut.
11. **Repeat props by name.** Once a prop is introduced ("白色手機" white phone), call
    it the same thing in every shot so the model keeps it consistent.
12. **Say which hand.** Hands holding props must be named (left / right) and stay
    consistent with the previous shot.
13. **Tears are a process.** Eyes redden gradually, one thin tear line — never
    "crying", which produces sobbing sounds.
14. **Narration is inner voice.** A narration line is spoken over a shot of the
    narrator not moving their mouth; set `voice_role` to `narration`.

15. **Pick the spot, not just the room.** The scene lists `render_zh.spots`: each is
    a position in a location with the background you would actually see from there.
    A close shot at the desk only describes the bookshelf behind her; if you give it
    the whole room, the model tries to squeeze the bed and window into a close-up.
    Choose the spot that matches where the character is and how wide the shot is;
    WIDE shots of a room use its wide spot.

16. **A line and "stay silent" can't share a shot.** Avoiding mouth words does not
    mean writing "keeps quiet" — on a shot with dialogue that contradicts the line and
    the model has to pick one. Give the body something to do instead.
17. **No typing while speaking.** A character looking down typing (phone or keyboard)
    while a line plays gets voiced as narration with a closed mouth — the model reads
    the line as the text being typed. Finish typing, then face something and speak.
18. **Props stay in their world.** The scene lists `props` and where they exist. The
    white phone is in Luna's room; Shenxi in the void never holds it.
19. **End on an action, not "keeps the pose".** "Finally keeps still" is filler: it
    takes no time and tells the model nothing.
20. **Write it as a sentence.** Join the actions with Chinese commas:
    「她站起來，之後走到床邊，然後趴上去，最後把手機貼到右耳。」

21. **Two people in frame: name who does each action.** 「月音往前半步，之後 Lisa 別開臉」.
    With only 「她」 the model cannot tell which woman you mean.

25. **No miming.** People in a scene don't act out their words — no tracing the shape of a
    book, no pointing at an ear to mean "songs", no making a heart with their hands. And
    never write what an action *means* (代表 / 表示 / 象徵 / 示意): the render model cannot draw
    a meaning, it draws the gesture literally. A person in a fight grips the sink, steps in,
    turns away, presses a hand to their chest — real actions whose meaning the audience reads.

26. **Two people talking in the same place: keep them in one frame.** When both characters are
    in the room and trading lines, the default shot is a two-shot: both in `characters_in_frame`,
    `blocking` set, the lines as `dialogue_lines` in speaking order (rule 6), each person's action
    named (rule 21). Cutting to one face for every line feels choppy and hides who is reacting to
    whom. Cut to a single only for a moment that needs one face — the key line, a reaction — and
    then come back to the two-shot. A two-shot can carry two or three short lines; still keep it
    within 10 seconds (rule 7).
27. **A shot that continues from the previous shot's last frame inherits its framing.** Consecutive
    shots in the same place with the same people are chained (the previous end frame becomes this
    shot's first frame). So the next shot must stay within one size step of the previous one
    (WIDE ↔ MEDIUM ↔ MEDIUM_CLOSE ↔ CLOSE_UP) and must not need anyone the previous frame lacks —
    otherwise the model holds the old framing for about 1.5 s and then hard-cuts to a new one,
    sometimes inventing an extra person. Someone may still *enter* (say they walk in / come out).
    If you need a big change of size or a different cast, plan it as a fresh start, not a chain.

28. **Words the render model takes literally, and lines too short to say.** Poetic 眼裡 / 心裡 / 腦海
    get drawn as light inside an eye — describe what is visible instead. A "drop" of tears becomes one
    big bead stuck on the face — write 一道細細的淚痕順著臉頰流下來. A spoken line of 3 characters or
    fewer comes out garbled: put it in the same shot as the line before or after it, keeping every word
    (4–8 characters is risky too). Don't have the speaker eat or drink while talking. 「！」 pitches the
    voice up and slurs it — let 、/。 and particles carry the emotion. Naming the same object twice
    (書桌…書桌) can draw two of them — refer back with 它. An embrace needs a headcount (她一個人…).
29. **An action that changes something has a start and an end.** 削著／切著／一直揉 makes the model
    repeat the motion back and forth for the whole shot. Write each step and its result
    (削斷、切成兩半、吃掉、揉完眼睛把手放下).
30. **Two people with no first frame: say where each one starts.** The first clause of the
    performance line places them (站在…前／坐在…旁／面對面), otherwise the model invents the blocking.
31. **No pulling back (DOLLY_OUT).** Pulling out reveals space the plates never showed, and the model
    fills it with invented set and extra people. Stay put, push in, or cut to a wider shot.

## Output language

- `visual_prompt` (the performance line) is written in **Traditional Chinese**, the
  language the render model was tuned on in our pipeline.
- `dialogue` keeps the script's original language.
- `reasoning_en` is one short English sentence explaining the directing choice.

## Enums

- `shot_type`: WIDE, MEDIUM, MEDIUM_CLOSE, CLOSE_UP
- `camera_move` — safe (the camera position stays, nothing new is revealed): BREATHE (near-static, subtle), STATIC,
  PUSH_FACE (slow push until face and shoulders fill the frame), DOLLY_IN (slow push in, then hold), PUSH_RETURN
  (push in, hold, ease back), ZOOM (lens zooms in, background compresses), PULL_FOCUS (the background melts into
  bokeh until only the person is sharp). Reveals new areas — the model must invent them (rule 2), so use only when
  the reference images cover the space: DOLLY_OUT, PAN_RETURN, TILT_UP, TILT_DOWN. Following someone who walks:
  TRACK (camera backs away at her walking pace), FOLLOW (camera slides alongside). Let the camera move with the
  emotion: push in when a line hurts, pull focus when someone is alone with a thought, track when someone walks
  into a confrontation. A scene where the camera never moves plays flat.
  On the local engine a fresh-start shot (not chained, rule 27) gets NO first frame (only character and set
  reference images), so for those: never PUSH_RETURN or PAN_RETURN (the camera has no start to return to — it only
  pushes in); in CLOSE_UP and MEDIUM_CLOSE act with the head, eyes and shoulders, not the hands (the camera follows
  the hands and loses the face). Chained shots start from the previous frame and are free of these two limits.
- `voice_role`: character (on-screen speaker), offscreen (heard, not seen), narration, none
- `blocking` (two people in frame): side_by_side or face_to_face
