# Brakko

**Seven AI model families. Nine classic memes. One new character.**

![Brakko — AI-generated meme character](images/Brakko_display.png)

**Brakko** is an original meme character created on **October 15, 2026**, through *MergeMeme*: an independent, user-operated workflow that coordinated AI models associated with OpenAI, Google, Meta, xAI, Anthropic, Microsoft, and NVIDIA via OpenRouter.

The public repository documents the design proposals, the AI director's selection of the name **Brakko**, and the final image-generation step. The recorded run was executed with `mock: false`.

> **Independent project:** This was a collaboration *between AI models arranged by a user*, not an official collaboration, partnership, or endorsement by the companies named above.

## At a glance

| | Recorded result |
|---|---|
| **Date** | October 15, 2026 |
| **Run ID** | `20261015-115328-0f0104` |
| **Participating text models** | 7 (one per model family) |
| **Source meme characters** | 9 |
| **Completed concept sketches** | 4 |
| **AI director** | `anthropic/claude-fable-5.1` |
| **Final image model** | `openai/gpt-image-2.5-sunburst` |
| **Recorded mode** | `mock: false` |
| **Public verification** | `python tools/verify_public.py` |

## The seven-model council

Each model proposed a character concept, a visual trait map, original name ideas, and an image prompt. The successful text-model submissions are preserved in [`evidence/trail/`](evidence/trail).

| Model family | Model used |
|---|---|
| OpenAI | `openai/gpt-6.1-sol-pro` |
| Google | `google/gemini-4-argon` |
| Meta | `meta/muse-wonder` |
| xAI | `x-ai/grok-4.7` |
| Anthropic | `anthropic/claude-opus-5.5` |
| Microsoft | `microsoft/phi-4` |
| NVIDIA | `nvidia/nemotron-3-ultra-550b-a55b` |

**Anthropic's council model proposed the name “Brakko.”** A separate AI director, `anthropic/claude-fable-5.1`, reviewed anonymized submissions and selected it for the final character.

## How Brakko was created

1. **Source selection:** Nine recognizable memes were chosen: Doge, Pepe the Frog, Trollface, Wojak, Gigachad, Harambe, Tralalero Tralala, Tung Tung Tung Sahur, and NPC Wojak.
2. **Council:** Seven text models independently proposed designs, names, and ways to combine recognizable features into one character.
3. **Sketches:** Four image models successfully generated separate concept sketches (OpenAI, Google, Microsoft, and xAI). Meta's image endpoint was unavailable; Anthropic and NVIDIA participated through text concepts only.
4. **Direction:** An AI director assessed the council proposals, chose the name **Brakko**, and wrote the final character specification and image prompt.
5. **Final render:** `openai/gpt-image-2.5-sunburst` generated the image using the director's prompt **and the nine original source-meme images as references**. The four concept sketches were **not** passed as image references for this final render.

Brakko's documented design includes a Doge-inspired head, Pepe-like eyes, a Trollface grin, Harambe's gorilla build, a Gigachad jaw, three blue sneakers inspired by Tralalero Tralala, and Tung Tung Tung Sahur's wooden bat.

## Verify the public archive

No API key is required to run the local public verification:

```bash
python tools/verify_public.py
```

The public checker validates the published files' SHA-256 checksums, the **redacted export's** hash chain, the original provenance-chain calculations, and the six included byte-identical original step files. It also checks the recorded council and director data.

**Start with these records:**

- [`evidence/run_summary.json`](evidence/run_summary.json) — concise run overview, models, source memes, and chosen result.
- [`evidence/trail/`](evidence/trail) — 15 privacy-scrubbed workflow events.
- [`evidence/redacted_provenance.json`](evidence/redacted_provenance.json) — checksums and chain for the public export.
- [`evidence/original_provenance.json`](evidence/original_provenance.json) — unchanged original provenance ledger.
- [`evidence/original_untouched/`](evidence/original_untouched) — six original step files retained byte-for-byte.
- [`evidence/image_integrity.json`](evidence/image_integrity.json) — image-byte integrity disclosure.

The OpenRouter generation IDs recorded in the logs provide an additional path for **account-authorized server-side cross-checks**:

```bash
# Set OPENROUTER_API_KEY locally. Never commit it to GitHub.
python tools/verify_openrouter_optional.py
```

## Evidence and verification limits

The archive is intended to make the recorded creation process **inspectable**, not to overstate what a public GitHub repository can prove.

- **Redacted logs:** Internal reasoning, failed-attempt payloads, and unrelated past project details were removed. The public trail files therefore have new hashes; the public chain validates the **redacted export**, not the byte identity of every original raw log.
- **Original commitments:** The unchanged original provenance ledger is included, together with six original step files that match its recorded SHA-256 hashes. The remaining original raw steps are not fully reproducible from this public subset.
- **Final image:** [`images/Brakko_display.png`](images/Brakko_display.png) is a display copy, **not a byte-identical copy** of the original `final.png` committed in the original provenance ledger. Its different hash is disclosed in [`evidence/image_integrity.json`](evidence/image_integrity.json).
- **Provider authentication:** A consistent public log or `mock: false` flag does **not independently prove** that a provider executed an API request. Independent authentication would require matching OpenRouter's server-side records.

## Repository map

```text
.
├── README.md
├── PRIVACY_AUDIT.md
├── SHA256SUMS.txt
├── evidence/
│   ├── trail/                     # 15 redacted workflow steps
│   ├── original_untouched/        # six unchanged original steps
│   ├── redacted_provenance.json
│   ├── original_provenance.json
│   ├── original_TRAIL.md
│   ├── run_summary.json
│   └── image_integrity.json
├── images/
│   └── Brakko_display.png
├── source/                        # privacy-scrubbed project source
└── tools/
    ├── verify_public.py
    ├── verify_original.py
    └── verify_openrouter_optional.py
```

## Privacy

This release excludes API keys, `.env` files, private uploads, local usernames and personal folder paths. Irrelevant prior project history and internal model reasoning were removed from the public logs. See [`PRIVACY_AUDIT.md`](PRIVACY_AUDIT.md) for the release notes.

---

**Brakko is a documented experiment in multi-model meme creation — not a product of an official seven-company alliance.**
