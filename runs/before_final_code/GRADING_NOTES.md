# How the answers were graded

Graded by reading each answer in `runs/answers-<setting>-real.jsonl` against the `gold` field in `questions.json`
(gold was written from the paper text before any model call). Strict rules:

- `correct`: states the gold fact(s) and every shown sentence cites a passage that contains it.
- `wrong`: a factual error, the wrong paper, or the key part of the question left unanswered without saying so.
- `refused_correctly` / `refused_wrongly`: refused when the papers do not / do answer.

Judgement calls worth knowing about:

| Question | Setting | Grade | Why |
|---|---|---|---|
| q03 chunk boundaries | small, medium | wrong | Describes where boundaries end up (whitespace), not how they are decided (cosine similarity of adjacent encoder outputs). The routing-module passage was ranked 16th-17th and never sent. |
| q03 | large | correct | Says a boundary probability is computed from the current and previous representation. No word "cosine", but the mechanism is right. |
| q13 WavLM | large | wrong | Cites the related-work sentence listing other self-supervised models; never names WavLM. |
| q13 | small, medium | correct | Answer says "WavLM8": the 8 is a footnote mark glued on by PDF extraction. |
| q14 Helium size + audio hours | medium | wrong | Gives 7B parameters, silently omits the 7 million hours. Half an answer with no warning. |
| q17 DNA efficiency | medium | correct | Says "nearly 4x" (abstract). Body says 3.6x. Both are in the paper. |
| q19 Transformer in Moshi vs H-Net | small | wrong | Attributed an H-Net statement to Moshi while citing an H-Net passage. The checker now drops this (rule added afterwards; replaying the stored output confirms it). |
| q19 | medium, large | refused_wrongly | Retrieval returned no Moshi architecture passage. |
| q20 EN-FR BLEU | medium | wrong | Answered 41.0, citing section 6.1, which really says 41.0. The abstract and Table 2 say 41.8. The paper contradicts itself; graded against the table. |

## Held-out questions (scenario `retrieval-medium-heldout`)

Eight questions written after the code was frozen, never used to change anything.

| Question | Grade | Why |
|---|---|---|
| h01, h02, h03, h06 | correct | match gold |
| h04 Fisher | wrong | Named the 170-hour supervised multi-stream dataset; the paper says the Fisher dataset gives Moshi its conversational abilities. |
| h05 FineWeb-Edu | refused_wrongly | The passage naming the training data was not retrieved. |
| hu1, hu2 | refused_correctly | not in the papers |
