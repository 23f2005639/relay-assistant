# How the answers were graded

All grades in `results.csv` are for runs made on 2026-11-12 with the final code. Each answer in
`runs/answers-<setting>-real*.jsonl` was read against the `gold` field of its question (gold was written from
the paper text before any model call). Earlier runs and their grades are in `runs/before_final_code/`.

Rules, applied strictly:

- `correct`: what the person is shown states the gold fact, and it is cited to a passage that contains it.
  This includes the case where only the verified quotes are shown (no written sentence) and the quotes state the fact.
- `wrong`: a factual error, the wrong paper, or the key part of the question left unanswered without saying so.
- `refused_correctly` / `refused_wrongly`: refused when the papers do not / do answer.

## Judgement calls

| Question | Setting | Grade | Why |
|---|---|---|---|
| q03 how chunk boundaries are decided | small, medium | wrong | Says where boundaries end up (whitespace, word starts), not how they are decided (cosine similarity of neighbouring encoder outputs). The routing-module passage ranks 16th-17th and is never sent. |
| q03 | large | wrong | Says the routing module "uses the key of the most recent token"; no comparison or similarity. Two sentences were dropped by the checker. |
| q05 Mimi frame rate and bitrate | small, medium | correct | The model wrote the sentence without a `[Q]` mark, so the sentence was dropped; the two verified quotes shown say "12.5Hz ... 1.1kbps". |
| q06 ratio loss, and why needed | small | correct | Gives the formula and what each term does (N sets the compression ratio). The "trivial solutions" sentence was dropped by the checker, so the "why" is thin. |
| q13 which model is distilled into Mimi | large | wrong | Cites the related-work sentence listing other self-supervised models; never names WavLM. |
| q13 | small, medium | correct | Says "WavLM8": the 8 is a footnote mark glued on by PDF extraction. |
| q16 H-Net on Chinese | large | refused_wrongly | Two quotes failed checking, so nothing survived. |
| q19 Transformer in Moshi vs H-Net | all three | refused_wrongly | No Moshi architecture passage was retrieved; the model said the passages were not enough. |

## Held-out questions (scenario `retrieval-medium-heldout`)

Eight questions written after the code was frozen and never used to change anything.

| Question | Grade | Why |
|---|---|---|
| h01, h02, h03, h06 | correct | match gold |
| h04 dataset for conversation dynamics | wrong | Names the 170-hour supervised multi-stream dataset; the paper says the Fisher dataset gives Moshi its conversational abilities. |
| h05 training data of H-Net's English models | refused_wrongly | The passage naming FineWeb-Edu was not retrieved. |
| hu1, hu2 | refused_correctly | not in the papers |
