# Demo video script (English, under 3 minutes)

Times below are approximate; the exact row starts are generated with the narration (`starts.json`).

Narration is generated with the same MiniMax H3 model the app renders with: a young professional female
narrator, one voice reference shared by every segment so it stays the same person. Only the audio is
used. The picture is screen recordings of the real app plus clips we rendered with it.

Test scene: chapter 10, scene 61 of our series (Tsukine and Lisa arguing in the office restroom), in the
test project `%TEMP%\gl_lang`.

| Time | On screen | Narration |
|---|---|---|
| 0:00–0:13 | Finished study clip of shot 6 (Lisa at the sink, Japanese with readings, English subtitle). | I grew up watching anime. Learning Japanese, I got stuck in the middle. Textbooks were too easy, real shows too fast, and almost nothing had readings over the kanji. |
| 0:13–0:34 | Home screen: drop the scene 61 script. Project page fills in scenes and characters. | So I started making my own. Drop in a story. Get a finished film, directed by NVIDIA Nemotron, rendered on your own laptop, and checked before anyone watches it. When a shot comes out wrong, nobody tells you why. You re-roll, and you pay again. |
| 0:30–0:55 | Segment page: director progress, rounds, "the gates caught 3", producer notes with quotes. | Greenlight Local gives Nemotron a director's rulebook. Twenty-five rules, nineteen of them enforced by code. Any draft that breaks one goes back with the exact reason, and a second Nemotron reviews the cut as a producer. |
| 0:55–1:20 | Shot 3 clip card: line match 1.0, gibberish ✗ 4.3 s, late start ⚠, face strip. | Every rendered shot is measured before you see it. This one matched the script word for word. But the loudness check found four extra seconds of speech that nobody wrote. |
| 1:20–1:45 | Diagnosis: options A / B / C with costs, evidence, before/after prompt. Click "apply" on C. | Nemotron diagnoses it from a library of proven causes, cites the evidence, and lays out every fix with its real cost. Trim it for free, or rewrite the action and render again. You decide, and the system remembers. |
| 1:45–2:00 | Mouth closed vs mouth moving, then the symptom library entry with both prompt lines. | It even knows how to keep the voice and close the mouth, which is exactly what an inner monologue needs. |
| 1:30–1:57 | Study mode: line table (source → spoken Japanese with readings, register, both subtitles, "why"), subtitle preview, study notes. | Turn on study mode, and any story becomes a language lesson. Nemotron rewrites every line the way the character would really say it, adds readings and bilingual subtitles, and explains why. Every line shows its register, the readings, both subtitles, and a note on why it is said that way. Study notes then pull out the most useful patterns. |
| 2:25–2:45 | Finishing panel running, finished part playing, settings page with the knowledge folder. | Approved shots are trimmed, upscaled and subtitled in one pass. And everything the system knows lives in plain files, so anyone can teach it more. |
| 2:10–2:22 | A shot from my own Japanese study anime (episode 1, readings over the kanji, English below), then the title card. | Now I make my own anime, and I learn from every line. Greenlight Local. A framework that makes any AI director better, one shot at a time. |

## Recording notes

- Record the app at 1280×800 or larger; the clip cards and options read best at that width.
- Shot 6 (study subtitles): `%TEMP%\gl_lang\shots\s001_06_ja-zh.mp4`. Mouth closed vs moving:
  `s001_06_v1_rep1.mp4` and `s001_06.mp4` in the same folder.
- Shot 3's check and diagnosis are on the segment page of the test project (preview `greenlight-local-lang`).
- The panels already hold the logs from real runs, so nothing needs to be re-run on camera.
- Let a second of the Japanese dialogue play before the narration starts on the opening shot.
