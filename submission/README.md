# Submission materials

Everything here is generated from the running app or its real output, by the scripts next to it.

## screenshots/

| File | What it shows |
|---|---|
| `thumbnail_youtube.png` | Video thumbnail (1280×720) |
| `architecture.png` | How the system fits together |
| `01_learner_view_ja_en_shenxi.png`, `02_learner_view_ja_en_tsukine.png` | What a learner watches: Japanese with readings over the kanji, English below (test scene rendered through the app) |
| `03_my_study_anime_ep1_jasmine.png`, `04_my_study_anime_ep1_cave.png` | My own Japanese study anime, episode 1 |
| `05_line_director_grammar_notes.png` | Line director: source line → spoken Japanese with readings, register, both subtitles, and why it is said that way |
| `06_study_notes_patterns.png` | Study notes: the pattern, casual vs polite, textbook form, more examples |
| `07_director_loop_gates_producer.png` | Director rounds, what the gates caught, producer notes |
| `08_shot_checks_measured.png` | Post-render checks with measurements and the face strip |
| `09_diagnosis_fixes_with_costs.png` | Diagnosis: three fixes side by side, costs computed by the program |
| `10_finishing_pipeline.png` | Finishing: trim, upscale, transitions, join, subtitles |

## Scripts

| Script | Output |
|---|---|
| `screenshots.py` | App screenshots 05–10 (opens the packaged app and scrolls to each section) |
| `make_thumbnail.py` | `thumbnail_youtube.png` |
| `make_architecture.py` | `architecture.png` |
| `demo-video/gen_narration.py` | Narration lines, rendered with MiniMax H3 (audio only), checked against the script |
| `demo-video/join_narration.py` | Full narration track and the start time of every row |
| `demo-video/record_app.py` | Screen recordings of the packaged app, one per row |
| `demo-video/build_demo.py` | The demo video: recordings, rendered clips, narration and burned-in English subtitles |

Large media (narration audio, recordings, the finished video) are not kept in the repository.
