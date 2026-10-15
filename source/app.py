"""
MergeMeme - seven AI labs, one meme.

Flow per run:
  1. Council  : one text model per lab (OpenAI, Google, Meta, xAI, Anthropic, Microsoft, NVIDIA)
                receives the chosen meme characters and returns name ideas, ONE visual element it
                insists on, a caption, a lore line and a prompt for its own sketch.
  2. Sketch   : labs that have an image model on OpenRouter render their own sketch.
                Labs without an image model contribute concept only.
  3. Director : one model merges all council JSON into the final name, ticker, tagline, lore,
                credits and a master merge prompt that enumerates every lab's element.
  4. Merge    : one image model renders the final meme using the sketches (+ reference images)
                as input_references, so every lab's contribution is physically in the final image.
  5. Card     : Pillow adds a bottom banner (name, tagline, lab credits) -> final_card.png

Provenance: every model call is written to output/<run>/trail/NN_<kind>_<lab>.json with the exact
request body (reference images replaced by their sha256), the raw response (image bytes replaced by
their sha256) and the OpenRouter generation id. provenance.json chains the sha256 of every trail file.
`python verify.py output/<run>` re-checks everything offline.

Everything goes through one OpenRouter key (OPENROUTER_API_KEY in .env).
"""
import base64
import copy
import hashlib
import io
import json
import os
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, request, send_from_directory, abort

try:
    from PIL import Image, ImageDraw, ImageFont
except Exception:  # pillow optional for the card step
    Image = None

APP_VERSION = "0.7.1"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))

OUTPUT_DIR = os.path.join(BASE_DIR, "output")
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
CHAR_IMG = os.path.join(BASE_DIR, "static", "characters")
os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(UPLOAD_DIR, exist_ok=True)

OR_BASE = "https://openrouter.ai/api/v1"
API_KEY = os.getenv("OPENROUTER_API_KEY", "").strip()
PORT = int(os.getenv("PORT", "5077"))
APP_TITLE = "MergeMeme"
MOCK = os.getenv("MERGEMEME_MOCK", "") == "1"   # offline dry run: fake models, no credits used

# ----------------------------------------------------------------------------- roster
# text = council delegate, image = the lab's own image model (None -> concept only)
LABS = [
    {"id": "openai",    "name": "OpenAI",    "text": "openai/gpt-6.1-sol-pro",           "image": "openai/gpt-image-2.5-sunburst"},
    {"id": "google",    "name": "Google",    "text": "google/gemini-4-argon",            "image": "google/gemini-3-pro-image"},
    {"id": "meta",      "name": "Meta",      "text": "meta/muse-wonder",                "image": "meta/muse-image"},
    {"id": "xai",       "name": "xAI",       "text": "x-ai/grok-4.7",                    "image": "x-ai/grok-imagine-image-quality"},
    {"id": "anthropic", "name": "Anthropic", "text": "anthropic/claude-opus-5.5",        "image": None},
    {"id": "microsoft", "name": "Microsoft", "text": "microsoft/phi-4",                  "image": "microsoft/mai-image-2.6"},
    {"id": "nvidia",    "name": "NVIDIA",    "text": "nvidia/nemotron-3-ultra-550b-a55b", "image": None},
]
LAB_BY_ID = {l["id"]: l for l in LABS}
DEFAULT_DIRECTOR = "anthropic/claude-fable-5.1"
DEFAULT_MERGE = "google/gemini-3-pro-image"
MERGE_CHOICES = [
    "google/gemini-3-pro-image",
    "openai/gpt-image-2.5-sunburst",
    "google/gemini-nano-banana-2.1",
    "bytedance-seed/seedream-5-0-flash",
    "openai/gpt-image-2",
    "google/gemini-3.1-flash-image",
]
DIRECTOR_CHOICES = [
    "anthropic/claude-fable-5.1",
    "anthropic/claude-opus-5.5",
    "openai/gpt-6.1-sol-pro",
    "google/gemini-4-argon",
    "x-ai/grok-4.7",
    "anthropic/claude-sonnet-5.5",
]
# per-lab model choices (first entry = default = newest/best tier)
TEXT_CHOICES = {
    "openai": ["openai/gpt-6.1-sol-pro", "openai/gpt-6.1-sol", "openai/gpt-6-luna-pro", "openai/gpt-6-luna"],
    "google": ["google/gemini-4-argon", "google/gemini-3.8-flash", "google/gemini-3.7-flash"],
    "meta": ["meta/muse-wonder", "meta/muse-spark-1.3", "meta/muse-glimmer-30b", "meta-llama/llama-4-maverick"],
    "xai": ["x-ai/grok-4.7", "x-ai/grok-4.6", "x-ai/grok-4.5", "x-ai/grok-4.3"],
    "anthropic": ["anthropic/claude-opus-5.5", "anthropic/claude-fable-5.1", "anthropic/claude-sonnet-5.5", "anthropic/claude-haiku-5.5"],
    "microsoft": ["microsoft/phi-4", "microsoft/wizardlm-2-8x22b"],
    "nvidia": ["nvidia/nemotron-3-ultra-550b-a55b", "nvidia/nemotron-3.5-lightning", "nvidia/nemotron-3-super-120b-a12b", "nvidia/nemotron-3.5-lightning:free"],
}
IMAGE_CHOICES = {
    "openai": ["openai/gpt-image-2.5-sunburst", "openai/gpt-image-2.5-flare", "openai/gpt-image-2", "openai/gpt-image-1-mini"],
    "google": ["google/gemini-3-pro-image", "google/gemini-nano-banana-2.1", "google/gemini-3.1-flash-image"],
    "meta": ["meta/muse-image"],
    "xai": ["x-ai/grok-imagine-image-quality", "x-ai/grok-imagine-image-2.0"],
    "anthropic": [],
    "microsoft": ["microsoft/mai-image-2.6", "microsoft/mai-image-2.6-flash", "microsoft/mai-image-2.5-pro"],
    "nvidia": [],
}
# ids that look newer in the catalog but are not real chat models (used by the newest-model check)
NOT_A_MODEL = {"nvidia/switchyard", "meta-llama/llama-guard-4-12b", "nvidia/nemotron-3.5-content-safety"}
LAB_PREFIX = {"openai": ("openai/",), "google": ("google/",), "meta": ("meta-llama/", "meta/"), "xai": ("x-ai/",),
              "anthropic": ("anthropic/",), "microsoft": ("microsoft/",), "nvidia": ("nvidia/",)}


def _load_popularity():
    try:
        return json.load(open(os.path.join(CHAR_IMG, "popularity.json"), encoding="utf-8"))
    except Exception:
        return {"data": {}, "source": "", "window": ""}


POPULARITY = _load_popularity()


def _load_traits():
    try:
        return (json.load(open(os.path.join(CHAR_IMG, "traits.json"), encoding="utf-8")) or {}).get("data") or {}
    except Exception:
        return {}


TRAITS = _load_traits()


def _build_characters():
    out = []
    for cid, v in (POPULARITY.get("data") or {}).items():
        img = None
        for ext in (".png", ".jpg", ".webp"):
            if os.path.exists(os.path.join(CHAR_IMG, cid + ext)):
                img = f"/static/characters/{cid}{ext}"
                break
        if not img:
            continue
        out.append({"id": cid, "name": v.get("name") or cid, "image": img, "views": int(v.get("est_people") or 0), "note": v.get("note") or "",
                    "traits": [t.get("trait") for t in (TRAITS.get(cid) or {}).get("traits") or []]})
    out.sort(key=lambda c: -c["views"])
    for i, c in enumerate(out):
        c["rank"] = i + 1
        c["top"] = True
    return out


# characters = the images in memeimage/ (normalised into static/characters/), ranked by an estimated
# number of people worldwide who recognise them (static/characters/popularity.json, an estimate, not a statistic)
PRESET_CHARACTERS = _build_characters()


# ----------------------------------------------------------------------------- app + state
app = Flask(__name__, static_folder=None)
JOBS = {}          # job_id -> dict
JOBS_LOCK = threading.Lock()
IMAGE_MODEL_CAPS = {"ts": 0, "models": {}}


def now_iso():
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def sha256_bytes(b):
    return hashlib.sha256(b).hexdigest()


def sha256_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def or_headers():
    return {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "http://localhost:%d" % PORT,
        "X-Title": APP_TITLE,
    }


def to_data_url(png_bytes, mime="image/png"):
    return f"data:{mime};base64," + base64.b64encode(png_bytes).decode("ascii")


def data_url_digest(u):
    """Replace a data URL by a small record (mime, bytes, sha256) for the trail."""
    if not isinstance(u, str) or not u.startswith("data:"):
        return {"url": u}
    try:
        head, b64 = u.split(",", 1)
        raw = base64.b64decode(b64)
        return {"mime": head[5:].split(";")[0], "bytes": len(raw), "sha256": sha256_bytes(raw)}
    except Exception:
        return {"url": "data:<unparsable>"}


# ----------------------------------------------------------------------------- openrouter helpers
def image_model_caps(force=False):
    """Cache of /images/models -> {id: {max_refs, resolutions, aspect_ratios}}"""
    if not force and IMAGE_MODEL_CAPS["models"] and time.time() - IMAGE_MODEL_CAPS["ts"] < 3600:
        return IMAGE_MODEL_CAPS["models"]
    out = {}
    try:
        r = requests.get(f"{OR_BASE}/images/models", timeout=30)
        r.raise_for_status()
        for m in r.json().get("data", []):
            sp = m.get("supported_parameters") or {}
            refs = sp.get("input_references") or {}
            out[m["id"]] = {
                "max_refs": int(refs.get("max", 0) or 0),
                "resolutions": (sp.get("resolution") or {}).get("values") or [],
                "aspect_ratios": (sp.get("aspect_ratio") or {}).get("values") or [],
                "quality": (sp.get("quality") or {}).get("values") or [],
                "name": m.get("name"),
            }
        IMAGE_MODEL_CAPS["models"] = out
        IMAGE_MODEL_CAPS["ts"] = time.time()
    except Exception as e:
        print("[caps] failed:", e)
    return IMAGE_MODEL_CAPS["models"]


def image_model_has_endpoint(model_id):
    try:
        r = requests.get(f"{OR_BASE}/images/models/{model_id}/endpoints", timeout=20)
        if r.status_code != 200:
            return False
        return len(r.json().get("endpoints") or []) > 0
    except Exception:
        return False


def extract_json(text):
    """Pull the first JSON object out of a model reply (handles ```json fences and chatter)."""
    if not text:
        raise ValueError("empty reply")
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*", "", t)
    t = re.sub(r"\s*```$", "", t)
    try:
        return json.loads(t)
    except Exception:
        pass
    start = t.find("{")
    if start < 0:
        raise ValueError("no JSON object in reply: " + t[:200])
    depth = 0
    for i in range(start, len(t)):
        if t[i] == "{":
            depth += 1
        elif t[i] == "}":
            depth -= 1
            if depth == 0:
                return json.loads(t[start:i + 1])
    raise ValueError("unbalanced JSON in reply")


def _mock_chat(model, system, user):
    time.sleep(0.5)
    if "Director" in system:
        labs = re.findall(r'"(Lab [A-G])":', user)
        names = re.findall(r"^- (.+?)(?: \(base|\s\[user note| *$)", user.split("User theme")[0], flags=re.M)
        slots = ["head", "mouth", "body", "feet", "eyes", "jaw", "accessory", "palette", "pose"]
        obj = {"final_name": "Blorpo", "tagline": "the frog that gave up winning", "base_character": names[0] if names else "?",
               "build": [{"slot": slots[i % len(slots)], "from_character": n, "trait": f"mock trait of {n}", "how": "mock"} for i, n in enumerate(names)],
               "character": {"form": "round green frog carrying mock parts", "face": "half-closed eyes, flat smile", "body_and_colors": "green, cream belly",
                             "pose": "slouched", "vibe": "calm defeat", "signature": "one eyebrow permanently raised"},
               "credits": [{"lab": l, "contribution": f"mock contribution of {l}"} for l in labs],
               "lore": "MOCK lore. " * 20, "merge_prompt": "MOCK merge prompt describing one original character.", "caption": "it is what it is"}
    else:
        lab = re.search(r"delegate of (.+?) in", system).group(1)
        names = re.findall(r"^- (.+?)(?: \(base|\s\[user note| *$)", user.split("Your special")[0], flags=re.M)
        obj = {"build": [{"slot": "head", "from_character": n, "trait": f"mock trait of {n}", "how": "mock"} for n in names],
               "base_character": names[0] if names else "?",
               "concept": {"form": f"{lab} blob", "face": "mock", "body_and_colors": "mock", "vibe": "mock", "signature": f"{lab} signature"},
               "name_ideas": ["Blorpo", "Nimbus", "Quazzle"],
               "caption": f"{lab} says hi", "lore": "mock lore", "image_prompt": f"mock sketch for {lab}"}
    txt = json.dumps(obj)
    meta = {"request": {"model": model, "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}], "mock": True},
            "response": {"id": "mock-" + uuid.uuid4().hex[:12], "model": model, "choices": [{"message": {"content": txt}}], "usage": {"cost": 0}, "mock": True},
            "attempts": 1}
    return obj, 0.0, txt, meta


def _mock_image(model, prompt, refs):
    time.sleep(0.6)
    im = Image.new("RGB", (512, 512), (40 + hash(model) % 150, 60, 120))
    dr = ImageDraw.Draw(im)
    dr.text((20, 20), "MOCK " + model, fill=(255, 255, 255), font=_font(22))
    dr.text((20, 60), prompt[:60], fill=(220, 220, 220), font=_font(16, bold=False))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    raw = buf.getvalue()
    meta = {"request": {"model": model, "prompt": prompt, "input_references": [data_url_digest(u) for u in (refs or [])], "mock": True},
            "response": {"id": "mock-" + uuid.uuid4().hex[:12], "data": [{"media_type": "image/png", "b64_sha256": sha256_bytes(raw), "bytes": len(raw)}], "usage": {"cost": 0}, "mock": True}}
    return raw, 0.0, meta


class ChatError(RuntimeError):
    """A text model failed after all retries; carries the failed attempts (for the trail) and what they cost."""
    def __init__(self, msg, attempts=None, cost=0.0):
        super().__init__(msg)
        self.attempts = attempts or []
        self.cost = cost


def chat_json(model, system, user, max_tokens=3000, temperature=0.9):
    """Chat completion that must return a JSON object. Returns (obj, cost_usd, raw_text, meta); cost includes failed attempts."""
    if MOCK:
        return _mock_chat(model, system, user)
    body = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "max_tokens": max_tokens,
        "temperature": temperature,
        "usage": {"include": True},
        # reasoning models (nemotron, grok, gpt-6) otherwise burn the whole max_tokens budget on hidden
        # thinking and return an empty content; the council does not need chain-of-thought
        "reasoning": {"enabled": False},
    }
    last_err = None
    attempts = []
    spent = 0.0
    for attempt in range(4):
        if attempt == 1:
            body.pop("response_format", None)   # some providers reject json_object -> retry plain
        elif attempt == 2:
            body.pop("reasoning", None)         # some providers reject the reasoning param
            body["max_tokens"] = max(body["max_tokens"], max_tokens * 2)
        elif attempt == 0:
            body["response_format"] = {"type": "json_object"}
        sent = copy.deepcopy(body)
        try:
            r = requests.post(f"{OR_BASE}/chat/completions", headers=or_headers(), json=body, timeout=180)
        except Exception as e:
            last_err = f"request failed: {str(e)[:200]}"
            attempts.append({"request": sent, "error": last_err})
            continue
        if r.status_code >= 400:
            last_err = f"HTTP {r.status_code}: {r.text[:300]}"
            attempts.append({"request": sent, "error": last_err})
            continue
        data = r.json()
        if "error" in data:
            last_err = str(data["error"])[:300]
            attempts.append({"request": sent, "error": last_err})
            continue
        spent += float((data.get("usage") or {}).get("cost") or 0)
        txt = ""
        try:
            txt = data["choices"][0]["message"]["content"] or ""
        except Exception:
            last_err = "no content: " + json.dumps(data)[:300]
            attempts.append({"request": sent, "error": last_err})
            continue
        if not txt.strip():
            fr = data["choices"][0].get("finish_reason")
            last_err = f"empty reply (finish_reason={fr}); raising token budget"
            attempts.append({"request": sent, "error": last_err, "response": data})
            body["max_tokens"] = int(body["max_tokens"] * 2)
            continue
        try:
            obj = extract_json(txt)
            return obj, spent, txt, {"request": sent, "response": data, "attempts": len(attempts) + 1, "failed_attempts": attempts}
        except Exception as e:
            fr = data["choices"][0].get("finish_reason")
            last_err = f"bad JSON ({e}; finish_reason={fr})"
            attempts.append({"request": sent, "error": last_err, "response": data})
            if fr == "length":
                # the reply was cut off mid-JSON (run "[REDACTED_PRIOR_PROJECT]": meta/muse-wonder); give it a much bigger budget and start over
                body["max_tokens"] = int(body["max_tokens"] * 2)
                body["messages"] = body["messages"][:2]
            else:
                body["messages"] = body["messages"][:2] + [{"role": "assistant", "content": txt},
                                                           {"role": "user", "content": "Reply again with ONLY the complete JSON object, nothing else."}]
    raise ChatError(f"{model}: {last_err}", attempts=attempts, cost=spent)


def chat_json_fallback(models, system, user, max_tokens=3000, temperature=0.9):
    """Try several text models of the same lab in order (first = the chosen one). Returns (obj, cost, raw, meta, used_model).
    cost includes every failed attempt of every model so the run's total stays honest."""
    tried = []
    spent = 0.0
    for m in [x for x in models if x]:
        try:
            obj, cost, raw, meta = chat_json(m, system, user, max_tokens=max_tokens, temperature=temperature)
            if tried:
                meta["fallback_from"] = tried
            return obj, cost + spent, raw, meta, m
        except ChatError as e:
            spent += e.cost
            tried.append({"model": m, "error": str(e)[:300], "attempts": e.attempts})
        except Exception as e:
            tried.append({"model": m, "error": str(e)[:300], "attempts": []})
    err = ChatError("all text models failed: " + "; ".join(f"{t['model']}: {t['error'][:120]}" for t in tried), cost=spent)
    err.tried = tried
    raise err


def gen_image(model, prompt, refs=None, aspect_ratio="1:1", resolution="1K"):
    """Dedicated OpenRouter Image API. refs = list of data URLs. Returns (png_bytes, cost, meta)."""
    caps = image_model_caps().get(model, {})
    max_refs = caps.get("max_refs", 0)
    refs = [u for u in (refs or []) if u][:max_refs] if (max_refs or MOCK) else []
    if MOCK:
        return _mock_image(model, prompt, refs)
    body = {"model": model, "prompt": prompt, "n": 1}
    if caps.get("aspect_ratios"):
        body["aspect_ratio"] = aspect_ratio if aspect_ratio in caps["aspect_ratios"] else caps["aspect_ratios"][0]
    if caps.get("resolutions") and resolution in caps["resolutions"]:
        body["resolution"] = resolution
    q = caps.get("quality") or []
    if q:   # always ask for the best quality tier the model offers (gpt-image: high, grok: medium/high)
        body["quality"] = next((x for x in ("max", "xhigh", "high", "highest", "hd", "medium") if x in q), q[-1])
    if refs:
        body["input_references"] = [{"type": "image_url", "image_url": {"url": u}} for u in refs]
    r = requests.post(f"{OR_BASE}/images", headers=or_headers(), json=body, timeout=300)
    sent = dict(body)
    sent["input_references"] = [data_url_digest(u) for u in refs]
    if r.status_code >= 400:
        raise RuntimeError(f"{model}: HTTP {r.status_code}: {r.text[:300]}")
    data = r.json()
    if "error" in data:
        raise RuntimeError(f"{model}: {str(data['error'])[:300]}")
    items = data.get("data") or []
    if not items or not items[0].get("b64_json"):
        raise RuntimeError(f"{model}: no image in response: {json.dumps(data)[:300]}")
    raw = base64.b64decode(items[0]["b64_json"])
    media = items[0].get("media_type") or "image/png"
    resp = copy.deepcopy(data)
    for it in resp.get("data", []):
        if "b64_json" in it:
            b = base64.b64decode(it.pop("b64_json"))
            it["b64_sha256"] = sha256_bytes(b)
            it["bytes"] = len(b)
    if Image is not None and media != "image/png":
        im = Image.open(io.BytesIO(raw)).convert("RGBA")
        buf = io.BytesIO()
        im.save(buf, "PNG")
        raw = buf.getvalue()
        resp["note"] = f"converted {media} to PNG locally; b64_sha256 is the original bytes"
    cost = float((data.get("usage") or {}).get("cost") or 0)
    return raw, cost, {"request": sent, "response": resp}


def gen_image_fallback(models, prompt, refs=None, aspect_ratio="1:1", resolution="1K"):
    """Try several image models in order (provider quota 429s, missing endpoints). Returns (png, cost, meta, used_model)."""
    caps = image_model_caps()
    tried = []
    for m in [x for x in models if x]:
        if not MOCK and (m not in caps or not image_model_has_endpoint(m)):
            tried.append({"model": m, "error": "no active endpoint"})
            continue
        try:
            png, cost, meta = gen_image(m, prompt, refs=refs, aspect_ratio=aspect_ratio, resolution=resolution)
            if tried:
                meta["fallback_from"] = tried
            return png, cost, meta, m
        except Exception as e:
            tried.append({"model": m, "error": str(e)[:300]})
    raise RuntimeError("all image models failed: " + "; ".join(f"{t['model']}: {t['error'][:120]}" for t in tried))


# ----------------------------------------------------------------------------- prompts
COUNCIL_SYSTEM = """You are the official AI delegate of {lab} in the "Merge Meme Council".
Seven AI labs (OpenAI, Google, Meta, xAI, Anthropic, Microsoft, NVIDIA) are fusing famous meme characters into ONE new meme character.
The rule of this council: the new character is built from REAL, RECOGNISABLE body parts of the source memes. Anyone who knows
the memes must be able to point at the picture and say "that is Doge's head, those are Pepe's lips, that is Wojak's body,
those are Tralalero's sneakers". At the same time it must be ONE coherent being: one body, one consistent colour scheme,
one silhouette, not a collage, not several faces, not a pile of parts.
You answer with ONE strict JSON object and nothing else. No markdown, no commentary."""

COUNCIL_USER = """Source memes and their signature traits (slot = the body part the trait belongs to):
{characters}

Your special responsibility: make sure {custody} are unmistakably visible in the result.
Recently produced builds by this council (try a different base body or a different combination): [REDACTED_UNRELATED_PROJECT_HISTORY]
User theme / notes: {notes}

Return exactly this JSON shape:
{{
  "build": [
    {{"slot": "<body part: head | eyes | mouth | jaw | face | body | arms | feet | skin_or_palette | accessory | pose | style>", "from_character": "<source meme exactly as listed>", "trait": "<the concrete visible trait, copied or adapted from the list above>", "how": "<how it is drawn on the new character, one sentence>"}}
  ],
  "base_character": "<which source meme gives the overall body shape and silhouette>",
  "concept": {{
    "form": "<what the new character IS in one sentence: the base body plus the parts it carries>",
    "face": "<eyes, mouth, brows, expression; name the source meme of each part>",
    "body_and_colors": "<body, proportions, ONE consistent palette of 3-5 colours for the whole body (the skin/fur/material is one material and one main colour), textures, outfit or accessory>",
    "pose": "<a characteristic pose or gesture>",
    "vibe": "<the single emotion or energy people will meme with it>",
    "signature": "<the ONE unmistakable feature at thumbnail size>"
  }},
  "name_ideas": ["<invented word>", "<invented word>", "<invented word>"],
  "caption": "<one punchy meme caption, max 10 words>",
  "lore": "<1-2 sentences of origin lore>",
  "image_prompt": "<a complete prompt for YOUR OWN image model. It MUST name each source meme next to its part, for example 'the head of Doge the Shiba Inu with the sideways side-eye, Pepe the Frog's thick wide reddish lips, the thin pale body of Wojak, the three legs in blue Nike sneakers of Tralalero Tralala'. Then: one single character, full body, centered, clean confident lineart, flat colours, one consistent palette, expressive face, on a solid single-colour background; no shadows, no gradients, no lighting effects, no floor, no background elements, no text, no watermark>"
}}
Build rules: EVERY source meme listed above must appear in "build" with at least one clearly visible trait (a shape, a part, a
colour, a pose), not only an attitude. Each slot is taken from exactly one source meme. Pick traits that are famous for that meme.
The whole must still read as one creature with one skin/material and one palette; parts are adapted to the base body, not pasted on.
Name rules: each name is ONE invented word, 4-8 letters, easy to say and spell, like 'Pepe', 'Doge', 'Wojak', 'Bonk'.
It must NOT contain, combine or echo any source name or a fragment of it. Portmanteaus of source names are banned."""

DIRECTOR_SYSTEM = """You are the Director of the Merge Meme Council. You receive the builds of up to seven AI labs for ONE new meme character
fused from famous meme characters. Choose the strongest build or combine the best slot choices into one: the result must show a
recognisable part of EVERY source meme (head, eyes, mouth, jaw, body, feet, accessory, pose, palette...) and still be ONE coherent
being with one body, one material and one consistent palette, with a brand-new one-word name. Answer with ONE strict JSON object and nothing else."""

DIRECTOR_USER = """Source memes and their signature traits:
{characters}

User theme / notes: {notes}
Recently produced builds by this council (prefer a different base body or combination): [REDACTED_UNRELATED_PROJECT_HISTORY]

Lab builds (JSON, labs anonymised as Lab A..G):
{contribs}

Return exactly this JSON shape:
{{
  "final_name": "<ONE invented word, 4-8 letters, pronounceable; one of the labs' ideas or a better new one; must not contain any source name fragment>",
  "tagline": "<max 8 words>",
  "base_character": "<the source meme that gives the overall body shape>",
  "build": [
    {{"slot": "<head | eyes | mouth | jaw | face | body | arms | feet | skin_or_palette | accessory | pose | style>", "from_character": "<source meme exactly as listed>", "trait": "<the concrete visible trait>", "how": "<how it is drawn on the final character>"}}
  ],
  "character": {{
    "form": "<what it is in one sentence: base body plus the parts it carries, naming the source memes>",
    "face": "<eyes, brows, mouth, expression in detail, naming the source meme of each part>",
    "body_and_colors": "<body shape and proportions, ONE consistent palette of 3-5 colours, one skin/fur/material for the whole body, textures, outfit or accessory>",
    "pose": "<default pose or gesture>",
    "vibe": "<the one emotion people meme with it>",
    "signature": "<the ONE unmistakable feature>"
  }},
  "credits": [
    {{"lab": "<Lab A..G exactly as given>", "contribution": "<which slot choice or idea of this lab made it into the final character, or what was rejected and why>"}}
  ],
  "lore": "<120-180 words: who the character is, which memes it carries in which body part, why the internet needs it; mention that seven AI labs designed it together>",
  "reference_labs": ["<Lab A..G whose sketch already looks like the final character; empty list if none does>"],
  "merge_prompt": "<the exact image prompt for the final render. Describe the character part by part and NAME THE SOURCE MEME of each part, e.g. 'head: the Shiba Inu head of Doge with the sideways side-eye; mouth: Pepe the Frog's thick wide reddish-brown lips; body: the thin pale slumped body of Wojak; feet: three blue Nike-style sneakers like Tralalero Tralala'. State the base body, the one consistent palette, the pose and the signature feature. Style: clean character illustration, full body, centered, confident lineart, flat colours with at most minimal flat shading, solid single-colour background; no shadows, no gradients, no lighting effects, no floor, no background elements. State explicitly: ONE single character with ONE body and ONE face, parts fused into the body and not pasted on, no collage, no text, no logos.>",
  "caption": "<one line of meme text>"
}}
Rules: EVERY source meme must appear in "build" with a visible trait; a lab build that leaves a source meme out is incomplete.
One slot per source meme is enough, two is the maximum; the most famous trait of each meme wins (Doge head or side-eye, Pepe lips
or heavy lids, Trollface grin, Wojak body or bald head, Gigachad jaw, Harambe gorilla build, Tralalero sneakers, Sahur wooden log
body or bat, NPC grey blank face). The result must still be ONE coherent character: one body, one material, one palette.
Every lab must appear in credits. Fairness: the labs are anonymised as Lab A..G on purpose. Judge only the ideas.
The name must be a completely new word: no 'Pepe', 'Doge', 'Wojak', 'Troll', 'Giga', 'Chad', 'Harambe', 'Trala', 'Sahur', 'NPC' or parts of them inside."""


def character_trait_lines(chars):
    """Text block for the prompts: every selected character with its signature traits (catalogue or 'describe from image')."""
    by_name = {v.get("name", "").lower(): v for v in (POPULARITY.get("data") or {}).values()}
    id_by_name = {v.get("name", "").lower(): k for k, v in (POPULARITY.get("data") or {}).items()}
    lines = []
    for c in chars:
        cid = c.get("id") or id_by_name.get((c.get("name") or "").lower())
        t = TRAITS.get(cid or "") or {}
        head = f"- {c['name']}"
        if t.get("base"):
            head += f" (base: {t['base']})"
        if c.get("notes"):
            head += f" [user note: {c['notes']}]"
        lines.append(head)
        for tr in t.get("traits") or []:
            lines.append(f"    {tr['slot']}: {tr['trait']}")
        if not t:
            lines.append("    (no catalogue entry; take the most famous visible traits of this character" +
                         (" and the attached reference image)" if c.get("ref") else ")"))
    return "\n".join(lines)


def recent_forms(limit=10):
    """Names and builds of the last runs so council and director can vary the base body."""
    out = []
    try:
        for d in sorted(os.listdir(OUTPUT_DIR), reverse=True):
            p = os.path.join(OUTPUT_DIR, d, "manifest.json")
            if not os.path.exists(p):
                continue
            m = json.load(open(p, encoding="utf-8"))
            dd = ((m.get("director") or {}).get("data")) or {}
            if not dd.get("final_name"):
                continue
            if dd.get("build"):
                parts = ", ".join(f"{b.get('slot')}={b.get('from_character')}" for b in dd["build"] if isinstance(b, dict))[:160]
                out.append(f"{dd['final_name']}: base {dd.get('base_character') or '?'}; {parts}")
            else:
                form = ((dd.get("character") or {}).get("form") or "").strip()
                if form:
                    out.append(f"{dd['final_name']}: {form[:90]}")
            if len(out) >= limit:
                break
    except Exception:
        pass
    return out


def build_problem(build, char_names):
    """None if every selected character has at least one visible trait in the build, else a reason."""
    if not isinstance(build, list) or not build:
        return "build list missing"
    seen = set()
    for b in build:
        if isinstance(b, dict) and b.get("from_character"):
            seen.add(str(b["from_character"]).strip().lower())
    missing = []
    for cn in char_names:
        low = cn.lower()
        toks = [t for t in re.findall(r"[a-z]+", low) if len(t) >= 3]
        if low in seen or any(low in s or s in low for s in seen) or any(any(t in s for t in toks) for s in seen):
            continue
        missing.append(cn)
    if missing:
        return "no visible trait from: " + ", ".join(missing)
    return None


NAME_FRAGMENTS = {"pepe", "doge", "wojak", "troll", "giga", "chad", "harambe", "trala", "tralal", "sahur", "npc", "meme", "coin", "lero", "tung"}


def name_problem(name, char_names):
    """Return None if the name is a clean invented one-word name, else a reason."""
    if not isinstance(name, str):
        return "name missing"
    n = name.strip()
    if not re.fullmatch(r"[A-Za-z]{4,9}", n):
        return "must be one word, letters only, 4-9 characters"
    low = n.lower()
    frags = set(NAME_FRAGMENTS)
    for cn in char_names:
        for tok in re.findall(r"[a-z]+", cn.lower()):
            if len(tok) >= 3:
                frags.add(tok)
            if len(tok) >= 4:
                frags.add(tok[:3])      # 'woj', 'pep', 'dog', 'gig', ... derived names are banned too
            if len(tok) >= 5:
                for i in range(len(tok) - 3):
                    frags.add(tok[i:i + 4])
    hit = [f for f in frags if f in low]
    if hit:
        return "contains source-name fragment(s): " + ", ".join(sorted(hit)[:4])
    return None


PROMPT_TEMPLATE_HASHES = {
    "council_system": sha256_bytes(COUNCIL_SYSTEM.encode()),
    "council_user": sha256_bytes(COUNCIL_USER.encode()),
    "director_system": sha256_bytes(DIRECTOR_SYSTEM.encode()),
    "director_user": sha256_bytes(DIRECTOR_USER.encode()),
}


# ----------------------------------------------------------------------------- job runner + trail
def job_log(job, step, status, **kw):
    ev = {"t": now_iso(), "step": step, "status": status}
    ev.update(kw)
    with JOBS_LOCK:
        job["events"].append(ev)
        job["updated"] = now_iso()


def trail_add(job, kind, lab, model, started, meta, output=None, error=None, cost=0.0):
    """Write one trail file and append its hash to the chain. Thread-safe."""
    with job["_trail_lock"]:
        n = len(job["trail"]) + 1
        fn = f"trail/{n:02d}_{kind}_{lab or 'run'}.json"
        rec = {
            "n": n, "kind": kind, "lab": lab, "model": model,
            "started": started, "finished": now_iso(),
            "request": (meta or {}).get("request"),
            "response": (meta or {}).get("response"),
            "failed_attempts": (meta or {}).get("failed_attempts") or [],
            "output": output, "error": error, "cost_usd": cost,
        }
        path = os.path.join(job["dir"], fn)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(rec, f, ensure_ascii=False, indent=1, sort_keys=True)
        h = sha256_file(path)
        prev = job["trail"][-1]["chain"] if job["trail"] else ""
        chain = sha256_bytes((prev + h).encode())
        resp = rec["response"] or {}
        summary = {
            "n": n, "kind": kind, "lab": lab, "model": model, "file": fn, "sha256": h, "chain": chain,
            "started": started, "finished": rec["finished"],
            "generation_id": resp.get("id"), "provider": resp.get("provider"),
            "usage": resp.get("usage"), "cost_usd": cost, "error": error,
            "image": (output or {}).get("image"),
        }
        job["trail"].append(summary)
        return summary


def write_provenance(job):
    cfg = job["config"]
    files = {}
    for fn in sorted(os.listdir(job["dir"])):
        if fn.lower().endswith(".png"):
            files[fn] = {"sha256": sha256_file(os.path.join(job["dir"], fn)), "bytes": os.path.getsize(os.path.join(job["dir"], fn))}
    chars = []
    for c in cfg.get("characters", []):
        rec = {"name": c.get("name"), "notes": c.get("notes") or ""}
        if c.get("ref"):
            rec["reference_image"] = data_url_digest(c["ref"])
        chars.append(rec)
    d = ((job.get("director") or {}).get("data")) or {}
    prov = {
        "app": {"name": APP_TITLE, "version": APP_VERSION, "mock": MOCK, "gateway": "https://openrouter.ai/api/v1"},
        "run_id": job["id"], "created": job["created"], "finished": job.get("finished"), "mock": MOCK,
        "characters": [c["name"] for c in chars], "character_inputs": chars,
        "labs": cfg.get("labs"), "lab_models": {l["id"]: {"text": l["text"], "image": l["image"]} for l in job.get("_labs", [])},
        "director_model": cfg.get("director"), "merge_model": cfg.get("merge_model"),
        "result": {"final_name": d.get("final_name"), "tagline": d.get("tagline"), "caption": d.get("caption"),
                   "character": d.get("character"), "base_character": d.get("base_character"), "build": d.get("build"), "name_rejected": d.get("name_rejected")},
        "prompt_template_sha256": PROMPT_TEMPLATE_HASHES,
        "steps": job["trail"],
        "files": files,
        "final_chain": job["trail"][-1]["chain"] if job["trail"] else None,
        "total_cost_usd": round(job["cost"], 6),
        "how_to_verify": [
            "python verify.py output/" + job["id"],
            "each trail/*.json holds the exact request (reference images as sha256) and the raw OpenRouter response",
            "generation ids can be looked up by the key owner: GET https://openrouter.ai/api/v1/generation?id=<generation_id>",
            "image bytes: sha256 in the trail file == sha256 of the png in this folder",
        ],
    }
    with open(os.path.join(job["dir"], "provenance.json"), "w", encoding="utf-8") as f:
        json.dump(prov, f, ensure_ascii=False, indent=1)
    # human readable
    lines = [f"# MergeMeme run {job['id']}", "",
             f"Created {job['created']}  finished {job.get('finished')}  mock={MOCK}", "",
             f"Characters: {', '.join(prov['characters'])}", "",
             f"Result: **{d.get('final_name')}** - {d.get('tagline')}", "",
             "| # | step | lab | model | generation id | cost USD | file sha256 |", "|---|---|---|---|---|---|---|"]
    for s in job["trail"]:
        lines.append(f"| {s['n']} | {s['kind']} | {s.get('lab') or ''} | {s.get('model') or ''} | {s.get('generation_id') or ''} | {s.get('cost_usd') or 0:.5f} | {s['sha256'][:16]} |")
    lines += ["", "Files:", ""] + [f"- {k}: sha256 {v['sha256']}" for k, v in files.items()]
    lines += ["", f"Final chain hash: `{prov['final_chain']}`", "", "Verify: `python verify.py output/" + job["id"] + "`"]
    with open(os.path.join(job["dir"], "TRAIL.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def public_job(job):
    """Job dict without the big data-URL references (for polling and the manifest)."""
    j = {k: v for k, v in job.items() if not k.startswith("_") and k != "dir"}
    cfg = dict(j.get("config") or {})
    cfg["characters"] = [{"id": c.get("id"), "name": c.get("name"), "notes": c.get("notes"), "has_ref": bool(c.get("ref") or c.get("has_ref"))}
                         for c in cfg.get("characters", [])]
    j["config"] = cfg
    return j


def save_manifest(job):
    path = os.path.join(job["dir"], "manifest.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(public_job(job), f, ensure_ascii=False, indent=2)


def run_job(job):
    cfg = job["config"]
    chars = cfg["characters"]
    notes = cfg.get("notes") or "none"
    char_lines = character_trait_lines(chars)
    labs = []
    for l in LABS:
        if l["id"] not in cfg["labs"]:
            continue
        l = dict(l)
        ov = (cfg.get("lab_models") or {}).get(l["id"]) or {}
        if ov.get("text"):
            l["text"] = ov["text"]
        if ov.get("image") is not None:
            l["image"] = ov["image"] or None
        labs.append(l)
    job["_labs"] = labs
    if not labs:
        job["status"] = "error"
        job_log(job, "setup", "error", message="no labs selected")
        return
    user_refs = [c["ref"] for c in chars if c.get("ref")]

    # custody round-robin so every character has at least one lab watching it
    custody = {l["id"]: [] for l in labs}
    for i, c in enumerate(chars):
        custody[labs[i % len(labs)]["id"]].append(c["name"])
    for l in labs:
        if not custody[l["id"]]:
            custody[l["id"]] = [c["name"] for c in chars]

    recent = recent_forms()
    recent_txt = "; ".join(recent) if recent else "none yet"
    job["recent"] = recent
    trail_add(job, "setup", None, None, job["created"],
              {"request": {"characters": [{"name": c["name"], "notes": c.get("notes") or "", "reference_image": data_url_digest(c["ref"]) if c.get("ref") else None} for c in chars],
                           "labs": [{"id": l["id"], "text": l["text"], "image": l["image"]} for l in labs],
                           "director": cfg.get("director"), "merge_model": cfg.get("merge_model"), "notes": notes,
                           "character_traits": char_lines, "recent_builds": recent,
                           "prompt_templates": {"council_system": COUNCIL_SYSTEM, "council_user": COUNCIL_USER,
                                                "director_system": DIRECTOR_SYSTEM, "director_user": DIRECTOR_USER}},
               "response": {"app_version": APP_VERSION, "mock": MOCK}})

    # ---- phase 1: council (parallel)
    job["status"] = "council"
    job_log(job, "council", "start", labs=[l["name"] for l in labs])

    def council_one(lab):
        # chosen model first, then the lab's other text models: a lab must never drop out because one model
        # returned broken JSON (run "[REDACTED_PRIOR_PROJECT]": meta/muse-wonder cut its JSON off, Meta was missing from the council)
        chain = [lab["text"]] + [m for m in TEXT_CHOICES.get(lab["id"], []) if m != lab["text"]]
        job_log(job, "council", "running", lab=lab["id"], model=lab["text"])
        t0 = now_iso()
        try:
            obj, cost, raw, meta, used = chat_json_fallback(
                chain,
                COUNCIL_SYSTEM.format(lab=lab["name"]),
                COUNCIL_USER.format(characters=char_lines, custody=", ".join(custody[lab["id"]]), notes=notes, recent=recent_txt),
            )
            job["cost"] += cost
            s = trail_add(job, "council", lab["id"], used, t0, meta, output={"parsed": obj}, cost=cost)
            job["council"][lab["id"]] = {"lab": lab["name"], "model": used, "ok": True, "data": obj, "cost": cost,
                                         "generation_id": s.get("generation_id"), "trail": s["file"], "fallback_from": meta.get("fallback_from")}
            if meta.get("fallback_from"):
                job_log(job, "council", "fallback", lab=lab["id"], model=used,
                        message="fallback after " + ", ".join(f"{t['model']} ({t['error'][:80]})" for t in meta["fallback_from"]))
            job_log(job, "council", "done", lab=lab["id"], cost=cost, model=used, name_ideas=obj.get("name_ideas"),
                    base=obj.get("base_character"), build_check=build_problem(obj.get("build"), [c["name"] for c in chars]) or "ok",
                    generation_id=s.get("generation_id"))
        except Exception as e:
            job["cost"] += float(getattr(e, "cost", 0) or 0)
            trail_add(job, "council", lab["id"], lab["text"], t0, {"failed_attempts": getattr(e, "tried", None) or getattr(e, "attempts", [])},
                      error=str(e), cost=float(getattr(e, "cost", 0) or 0))
            job["council"][lab["id"]] = {"lab": lab["name"], "model": lab["text"], "ok": False, "error": str(e)}
            job_log(job, "council", "error", lab=lab["id"], message=str(e)[:400])

    with ThreadPoolExecutor(max_workers=len(labs)) as ex:
        list(ex.map(council_one, labs))
    save_manifest(job)

    good = {k: v for k, v in job["council"].items() if v.get("ok")}
    if not good:
        job["status"] = "error"
        job_log(job, "council", "error", message="every council model failed; check OPENROUTER_API_KEY and credits")
        return

    # ---- phase 2: sketches (parallel, labs with an image model)
    job["status"] = "sketch"
    sketch_labs = [l for l in labs if l["image"] and l["id"] in good and cfg.get("sketches", True)]
    job_log(job, "sketch", "start", labs=[l["name"] for l in sketch_labs])
    caps = image_model_caps()

    def sketch_one(lab):
        chain = [lab["image"]] + [m for m in IMAGE_CHOICES.get(lab["id"], []) if m != lab["image"]]
        job_log(job, "sketch", "running", lab=lab["id"], model=lab["image"])
        concept = good[lab["id"]]["data"].get("concept") or {}
        prompt = (good[lab["id"]]["data"].get("image_prompt") or "").strip()
        if not prompt:
            prompt = "A single square illustration of a new meme character: " + "; ".join(f"{k}: {v}" for k, v in concept.items() if v)
        prompt += " One single character, full body, centered, clean lineart, flat colours, expressive face, on a solid single-colour background. No shadows, no gradients, no lighting effects, no floor, no background elements. No text, no letters, no watermark."
        t0 = now_iso()
        try:
            png, cost, meta, used = gen_image_fallback(chain, prompt, refs=user_refs, aspect_ratio="1:1", resolution=cfg.get("sketch_res", "1K"))
            fn = f"sketch_{lab['id']}.png"
            with open(os.path.join(job["dir"], fn), "wb") as f:
                f.write(png)
            job["cost"] += cost
            s = trail_add(job, "sketch", lab["id"], used, t0, meta, output={"image": {"file": fn, "sha256": sha256_bytes(png), "bytes": len(png)}}, cost=cost)
            job["sketches"][lab["id"]] = {"lab": lab["name"], "model": used, "ok": True, "file": fn, "cost": cost, "prompt": prompt,
                                          "sha256": sha256_bytes(png), "generation_id": s.get("generation_id"), "trail": s["file"],
                                          "fallback_from": meta.get("fallback_from")}
            job_log(job, "sketch", "done", lab=lab["id"], model=used, file=f"/output/{job['id']}/{fn}", cost=cost, generation_id=s.get("generation_id"))
        except Exception as e:
            msg = str(e)
            skipped = "all image models failed" in msg and "no active endpoint" in msg and "HTTP" not in msg
            trail_add(job, "sketch", lab["id"], lab["image"], t0, None, error=msg)
            job["sketches"][lab["id"]] = {"lab": lab["name"], "model": lab["image"], "ok": False, "skipped": skipped,
                                          "error": "no image endpoint on OpenRouter, concept only" if skipped else msg}
            job_log(job, "sketch", "skipped" if skipped else "error", lab=lab["id"], message=msg[:400])

    if sketch_labs:
        with ThreadPoolExecutor(max_workers=len(sketch_labs)) as ex:
            list(ex.map(sketch_one, sketch_labs))
    save_manifest(job)

    # ---- phase 3: director (with name validation: the name must be a brand-new one-word name)
    job["status"] = "director"
    director = cfg.get("director") or DEFAULT_DIRECTOR
    job_log(job, "director", "running", model=director)
    # anonymise the labs (Lab A..G) so the director cannot favour its own model family
    alias = {}
    contribs = {}
    for i, (lid, v) in enumerate(sorted(good.items())):
        tag = "Lab " + chr(65 + i)
        alias[tag] = lid
        contribs[tag] = v["data"]
    job["director_alias"] = {k: LAB_BY_ID[v]["name"] for k, v in alias.items()}
    char_names = [c["name"] for c in chars]
    user_prompt = DIRECTOR_USER.format(characters=char_lines, notes=notes, recent=recent_txt,
                                       contribs=json.dumps(contribs, ensure_ascii=False, indent=1))

    def unalias(obj):
        def real(x):
            x = str(x or "").strip()
            key = next((k for k in alias if k.lower() == x.lower()), None)
            return LAB_BY_ID[alias[key]]["name"] if key else x
        for c in obj.get("credits") or []:
            if isinstance(c, dict):
                c["lab"] = real(c.get("lab"))
        obj["reference_labs"] = [real(x) for x in (obj.get("reference_labs") or [])]
        return obj
    rejected = []
    build_rejected = []
    ok = False
    for attempt in range(4):
        t0 = now_iso()
        try:
            extra = ""
            if rejected:
                extra += "\n\nPREVIOUS ATTEMPT REJECTED. " + " ".join(f"The name '{r[0]}' was rejected: {r[1]}." for r in rejected) + \
                         " Invent a completely new word that shares no letters-sequence with any source meme name."
            if build_rejected:
                extra += "\n\nPREVIOUS BUILD REJECTED: " + build_rejected[-1] + \
                         ". Every source meme must contribute one visible trait in 'build' and in 'merge_prompt'."
            dchain = [director] + [m for m in DIRECTOR_CHOICES if m != director]
            obj, cost, raw, meta, director_used = chat_json_fallback(dchain, DIRECTOR_SYSTEM, user_prompt + extra, max_tokens=4000, temperature=0.8)
            job["cost"] += cost
            if meta.get("fallback_from"):
                job_log(job, "director", "fallback", model=director_used,
                        message="fallback after " + ", ".join(f"{t['model']} ({t['error'][:80]})" for t in meta["fallback_from"]))
            problem = name_problem(obj.get("final_name"), char_names)
            bproblem = build_problem(obj.get("build"), char_names)
            s = trail_add(job, "director", None, director_used, t0, meta,
                          output={"parsed": obj, "name_check": problem or "ok", "build_check": bproblem or "ok", "attempt": attempt + 1}, cost=cost)
            obj.pop("ticker", None)
            if problem:
                rejected.append((obj.get("final_name"), problem))
                job_log(job, "director", "retry", model=director, message=f"name '{obj.get('final_name')}' rejected: {problem}")
                continue
            if bproblem and attempt < 3:
                build_rejected.append(bproblem)
                job_log(job, "director", "retry", model=director, message=f"build rejected: {bproblem}")
                continue
            obj["build_warning"] = bproblem
            obj = unalias(obj)
            obj["name_rejected"] = [{"name": r[0], "reason": r[1]} for r in rejected]
            obj["build_rejected"] = build_rejected
            job["director"] = {"model": director_used, "ok": True, "data": obj, "cost": cost, "generation_id": s.get("generation_id"),
                               "trail": s["file"], "fallback_from": meta.get("fallback_from")}
            job_log(job, "director", "done", cost=cost, final_name=obj.get("final_name"), generation_id=s.get("generation_id"))
            ok = True
            break
        except Exception as e:
            job["cost"] += float(getattr(e, "cost", 0) or 0)
            trail_add(job, "director", None, director, t0, {"failed_attempts": getattr(e, "tried", None) or getattr(e, "attempts", [])},
                      error=str(e), cost=float(getattr(e, "cost", 0) or 0))
            job["director"] = {"model": director, "ok": False, "error": str(e)}
            job_log(job, "director", "error", message=str(e)[:400])
            break
    if not ok:
        if job.get("director") and job["director"].get("ok") is None:
            pass
        if not (job.get("director") or {}).get("ok"):
            if rejected and not (job.get("director") or {}).get("error"):
                job["director"] = {"model": director, "ok": False, "error": "no valid new name after 4 attempts: " + ", ".join(r[0] or "?" for r in rejected)}
                job_log(job, "director", "error", message=job["director"]["error"])
            job["status"] = "error"
            return
    save_manifest(job)

    # ---- phase 4: merge (references = the labs' sketches of the NEW character; source memes only if requested)
    job["status"] = "merge"
    merge_model = cfg.get("merge_model") or DEFAULT_MERGE
    chain = [merge_model] + [m for m in MERGE_CHOICES if m != merge_model]
    d = job["director"]["data"]
    merge_prompt = (d.get("merge_prompt") or "").strip()
    refs, ref_notes = [], []
    # Reference images for the final render. Default "sources": the original meme images of the selected characters, so the
    # image model draws the real Doge head / Pepe lips / Wojak body instead of inventing them (run "[REDACTED_PRIOR_PROJECT]", the only good one so
    # far, was drawn this way). "sources_sketches" adds the labs' sketches of the new character. "none" renders from the text only
    # (a single sketch as the only reference gets copied 1:1 by image-editing models, so sketches alone are never the default).
    mode = cfg.get("merge_refs") or "sources"
    use_sketch_refs = mode == "sources_sketches"
    use_source_refs = mode in ("sources", "sources_sketches")
    if use_source_refs:
        for c in chars:
            if c.get("ref"):
                refs.append(c["ref"])
                ref_notes.append(f"Reference image {len(refs)}: the original {c['name']} meme. Take ONLY the parts of it named in the description above and fuse them into the one new character.")
    wanted = {str(x).strip().lower() for x in (d.get("reference_labs") or []) if x}
    for lab in labs:
        s = job["sketches"].get(lab["id"])
        if use_sketch_refs and s and s.get("ok") and (not wanted or lab["name"].lower() in wanted or lab["id"] in wanted):
            with open(os.path.join(job["dir"], s["file"]), "rb") as f:
                refs.append(to_data_url(f.read()))
            ref_notes.append(f"Reference image {len(refs)}: {lab['name']}'s sketch of the same new character (overall look only).")
    if not refs:
        job_log(job, "merge", "note", message="rendering the final from the character sheet only (no reference images)")
    max_refs = max([caps.get(m, {}).get("max_refs", 0) for m in chain] + [0])
    if max_refs and len(refs) > max_refs:
        refs, ref_notes = refs[:max_refs], ref_notes[:max_refs]
    full_prompt = merge_prompt
    if ref_notes:
        full_prompt += "\n\n" + "\n".join(ref_notes) + \
            "\nDo NOT copy any reference image as a whole and do NOT place the references side by side. Build ONE brand-new " \
            "character with ONE body and ONE face that carries the named parts of each reference, fused into one consistent " \
            "material and palette. Where a reference and the text disagree, the text wins."
    full_prompt += "\nSquare format, clean character illustration: confident lineart, flat colours, minimal flat shading at most, solid single-colour background. No shadows, no gradients, no lighting effects, no floor, no background elements. Absolutely no text, letters or logos in the image."
    job_log(job, "merge", "running", model=merge_model, refs=len(refs), mode=mode)
    t0 = now_iso()
    try:
        png, cost, meta, used = gen_image_fallback(chain, full_prompt, refs=refs, aspect_ratio="1:1", resolution=cfg.get("merge_res", "1K"))
        with open(os.path.join(job["dir"], "final.png"), "wb") as f:
            f.write(png)
        job["cost"] += cost
        s = trail_add(job, "merge", None, used, t0, meta, output={"image": {"file": "final.png", "sha256": sha256_bytes(png), "bytes": len(png)}}, cost=cost)
        job["merge"] = {"model": used, "ok": True, "file": "final.png", "cost": cost, "prompt": full_prompt, "refs": len(refs), "mode": mode,
                        "sha256": sha256_bytes(png), "generation_id": s.get("generation_id"), "trail": s["file"], "fallback_from": meta.get("fallback_from")}
        job_log(job, "merge", "done", model=used, file=f"/output/{job['id']}/final.png", cost=cost, generation_id=s.get("generation_id"))
    except Exception as e:
        trail_add(job, "merge", None, merge_model, t0, None, error=str(e))
        job["merge"] = {"model": merge_model, "ok": False, "error": str(e), "prompt": full_prompt}
        job_log(job, "merge", "error", message=str(e)[:400])
        job["status"] = "error"
        return

    # ---- phase 5: card (local, no model)
    try:
        card = make_card(os.path.join(job["dir"], "final.png"), d, [l["name"] for l in labs if l["id"] in good])
        if card:
            with open(os.path.join(job["dir"], "final_card.png"), "wb") as f:
                f.write(card)
            job["card"] = {"ok": True, "file": "final_card.png", "sha256": sha256_bytes(card)}
            job_log(job, "card", "done", file=f"/output/{job['id']}/final_card.png")
    except Exception as e:
        job["card"] = {"ok": False, "error": str(e)}
        job_log(job, "card", "error", message=str(e)[:300])

    job["status"] = "done"
    job["finished"] = now_iso()
    job_log(job, "run", "done", total_cost=round(job["cost"], 4))


def _font(size, bold=True):
    for name in (["arialbd.ttf", "segoeuib.ttf", "impact.ttf"] if bold else ["arial.ttf", "segoeui.ttf"]):
        p = os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "Fonts", name)
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass
    return ImageFont.load_default()


def make_card(final_path, d, lab_names):
    if Image is None:
        return None
    im = Image.open(final_path).convert("RGB")
    W = 1024
    im = im.resize((W, int(im.height * W / im.width)))
    name = (d.get("final_name") or "Untitled").strip()
    tag = (d.get("tagline") or d.get("caption") or "").strip()
    credit = "Built by " + " x ".join(lab_names) + "  |  MergeMeme"
    bar_h = 190
    card = Image.new("RGB", (W, im.height + bar_h), (18, 18, 17))
    card.paste(im, (0, 0))
    dr = ImageDraw.Draw(card)
    y = im.height + 18
    title = name
    f1 = _font(54)
    while dr.textlength(title, font=f1) > W - 48 and f1.size > 24:
        f1 = _font(f1.size - 4)
    dr.text((24, y), title, font=f1, fill=(242, 241, 237))
    y += f1.size + 14
    f2 = _font(30, bold=False)
    while dr.textlength(tag, font=f2) > W - 48 and f2.size > 16:
        f2 = _font(f2.size - 2, bold=False)
    dr.text((24, y), tag, font=f2, fill=(190, 189, 182))
    y += f2.size + 16
    f3 = _font(20, bold=False)
    while dr.textlength(credit, font=f3) > W - 48 and f3.size > 12:
        f3 = _font(f3.size - 1, bold=False)
    dr.text((24, y), credit, font=f3, fill=(151, 149, 142))
    buf = io.BytesIO()
    card.save(buf, "PNG")
    return buf.getvalue()


# ----------------------------------------------------------------------------- routes
@app.route("/")
def index():
    return send_from_directory(BASE_DIR, "index.html")


@app.route("/output/<path:p>")
def output_file(p):
    return send_from_directory(OUTPUT_DIR, p)


@app.route("/static/<path:p>")
def static_file(p):
    return send_from_directory(os.path.join(BASE_DIR, "static"), p)


@app.route("/uploads/<path:p>")
def upload_file(p):
    return send_from_directory(UPLOAD_DIR, p)


@app.route("/api/config")
def api_config():
    return jsonify({
        "has_key": bool(API_KEY), "key_suffix": API_KEY[-3:] if API_KEY else "", "mock": MOCK, "version": APP_VERSION,
        "labs": LABS,
        "presets": PRESET_CHARACTERS,
        "popularity_source": POPULARITY.get("source"), "popularity_window": POPULARITY.get("window"),
        "text_choices": TEXT_CHOICES,
        "image_choices": IMAGE_CHOICES,
        "director_choices": DIRECTOR_CHOICES,
        "merge_choices": MERGE_CHOICES,
        "default_director": DEFAULT_DIRECTOR,
        "default_merge": DEFAULT_MERGE,
    })


@app.route("/api/models/check")
def api_models_check():
    """No key needed: verifies every roster model exists on OpenRouter and which image models have endpoints."""
    out = {"text": {}, "image": {}, "merge": {}}
    try:
        r = requests.get(f"{OR_BASE}/models", timeout=30)
        ids = {m["id"]: m for m in r.json().get("data", [])}
    except Exception as e:
        return jsonify({"error": f"models list failed: {e}"}), 502
    caps = image_model_caps(force=True)
    im_list = []
    try:
        im_list = requests.get(f"{OR_BASE}/images/models", timeout=30).json().get("data", [])
    except Exception:
        pass
    out["newest"] = {}
    for l in LABS:
        pre = LAB_PREFIX[l["id"]]
        txt = sorted([m for m in ids.values() if m["id"].startswith(pre) and ":" not in m["id"] and m["id"] not in NOT_A_MODEL
                      and float((m.get("pricing") or {}).get("prompt", 0) or 0) >= 0], key=lambda m: -m.get("created", 0))
        img = sorted([m for m in im_list if m["id"].startswith(pre)], key=lambda m: -m.get("created", 0))
        out["newest"][l["id"]] = {"text": txt[0]["id"] if txt else None, "text_is_default": bool(txt) and txt[0]["id"] == l["text"],
                                  "image": img[0]["id"] if img else None, "image_is_default": (img[0]["id"] == l["image"]) if img else None}
    for l in LABS:
        m = ids.get(l["text"])
        p = (m or {}).get("pricing") or {}
        out["text"][l["id"]] = {"model": l["text"], "ok": m is not None,
                                "usd_per_m_in": round(float(p.get("prompt", 0)) * 1e6, 3) if m else None,
                                "usd_per_m_out": round(float(p.get("completion", 0)) * 1e6, 3) if m else None}
        if l["image"]:
            out["image"][l["id"]] = {"model": l["image"], "listed": l["image"] in caps,
                                     "endpoint": image_model_has_endpoint(l["image"]) if l["image"] in caps else False,
                                     "max_refs": caps.get(l["image"], {}).get("max_refs")}
    for mid in MERGE_CHOICES:
        out["merge"][mid] = {"listed": mid in caps, "endpoint": image_model_has_endpoint(mid) if mid in caps else False,
                             "max_refs": caps.get(mid, {}).get("max_refs")}
    return jsonify(out)


@app.route("/api/credits")
def api_credits():
    if not API_KEY:
        return jsonify({"error": "no key"}), 400
    try:
        r = requests.get(f"{OR_BASE}/credits", headers=or_headers(), timeout=20)
        return jsonify(r.json()), r.status_code
    except Exception as e:
        return jsonify({"error": str(e)}), 502


@app.route("/api/upload", methods=["POST"])
def api_upload():
    f = request.files.get("file")
    if not f:
        return jsonify({"error": "no file"}), 400
    ext = os.path.splitext(f.filename or "")[1].lower()
    if ext not in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
        return jsonify({"error": "png/jpg/webp/gif only"}), 400
    raw = f.read()
    if len(raw) > 8 * 1024 * 1024:
        return jsonify({"error": "max 8 MB"}), 400
    name = uuid.uuid4().hex + ext
    with open(os.path.join(UPLOAD_DIR, name), "wb") as fh:
        fh.write(raw)
    return jsonify({"url": f"/uploads/{name}", "sha256": sha256_bytes(raw)})


@app.route("/api/run", methods=["POST"])
def api_run():
    if not API_KEY and not MOCK:
        return jsonify({"error": "OPENROUTER_API_KEY missing in .env"}), 400
    cfg = request.get_json(force=True) or {}
    chars = []
    for c in cfg.get("characters") or []:
        name = (c.get("name") or "").strip()
        if not name:
            continue
        ref = c.get("ref") or ""
        if ref.startswith("/uploads/") or ref.startswith("/static/characters/"):
            p = os.path.join(UPLOAD_DIR if ref.startswith("/uploads/") else CHAR_IMG, os.path.basename(ref))
            if os.path.exists(p):
                with open(p, "rb") as fh:
                    ext = os.path.splitext(p)[1][1:].lower()
                    mime = "image/jpeg" if ext in ("jpg", "jpeg") else f"image/{ext}"
                    ref = to_data_url(fh.read(), mime)
            else:
                ref = ""
        elif ref:
            ref = ""   # only local files are accepted as references
        cid = c.get("id") if isinstance(c.get("id"), str) and re.fullmatch(r"[a-z0-9_-]{1,40}", c.get("id")) else None
        chars.append({"id": cid, "name": name[:80], "notes": (c.get("notes") or "")[:300], "ref": ref})
    if len(chars) < 2:
        return jsonify({"error": "pick at least 2 characters"}), 400
    labs = [l for l in (cfg.get("labs") or [x["id"] for x in LABS]) if l in LAB_BY_ID]
    lab_models = {}
    for lid, mm in (cfg.get("lab_models") or {}).items():
        if lid in TEXT_CHOICES and isinstance(mm, dict):
            t = mm.get("text") if mm.get("text") in TEXT_CHOICES[lid] else None
            i = mm.get("image") if mm.get("image") in IMAGE_CHOICES[lid] else None
            if mm.get("image") in ("", None, "none"):
                i = ""
            lab_models[lid] = {"text": t, "image": i}
    job_id = datetime.utcnow().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    jdir = os.path.join(OUTPUT_DIR, job_id)
    os.makedirs(os.path.join(jdir, "trail"), exist_ok=True)
    merge_refs = cfg.get("merge_refs") if cfg.get("merge_refs") in ("sources", "sources_sketches", "none") else \
        ("sources_sketches" if cfg.get("sketch_refs_in_merge") else "sources")
    public_cfg = {"characters": [{"id": c.get("id"), "name": c["name"], "notes": c["notes"], "has_ref": bool(c["ref"])} for c in chars],
                  "labs": labs, "lab_models": lab_models, "notes": (cfg.get("notes") or "")[:600],
                  "director": cfg.get("director") if cfg.get("director") in DIRECTOR_CHOICES else DEFAULT_DIRECTOR,
                  "merge_model": cfg.get("merge_model") if cfg.get("merge_model") in MERGE_CHOICES else DEFAULT_MERGE,
                  "sketches": bool(cfg.get("sketches", True)), "merge_refs": merge_refs,
                  "sketch_res": cfg.get("sketch_res") or "1K", "merge_res": cfg.get("merge_res") or "2K"}
    job = {
        "id": job_id, "dir": jdir, "created": now_iso(), "updated": now_iso(), "status": "queued", "mock": MOCK,
        "config": public_cfg,
        "events": [], "council": {}, "sketches": {}, "director": None, "merge": None, "card": None, "cost": 0.0,
        "trail": [], "_trail_lock": threading.Lock(), "_labs": [],
    }
    run_cfg = dict(public_cfg)
    run_cfg["characters"] = chars
    with JOBS_LOCK:
        JOBS[job_id] = job

    def _run():
        job["config"] = run_cfg
        try:
            run_job(job)
        except Exception as e:
            job["status"] = "error"
            job_log(job, "run", "error", message=str(e)[:400])
        finally:
            if not job.get("finished"):
                job["finished"] = now_iso()
            try:
                write_provenance(job)
            except Exception as e:
                job_log(job, "provenance", "error", message=str(e)[:300])
            job["config"] = public_cfg
            save_manifest(job)

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"job_id": job_id})


@app.route("/api/jobs/<job_id>")
def api_job(job_id):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if job is None:
        p = os.path.join(OUTPUT_DIR, job_id, "manifest.json")
        if not os.path.exists(p):
            abort(404)
        with open(p, encoding="utf-8") as f:
            return jsonify(json.load(f))
    return jsonify(public_job(job))


@app.route("/api/history")
def api_history():
    items = []
    for d in sorted(os.listdir(OUTPUT_DIR), reverse=True)[:60]:
        p = os.path.join(OUTPUT_DIR, d, "manifest.json")
        if not os.path.exists(p):
            continue
        try:
            with open(p, encoding="utf-8") as f:
                m = json.load(f)
            dd = (m.get("director") or {}).get("data") or {}
            items.append({"id": d, "created": m.get("created"), "status": m.get("status"), "cost": round(m.get("cost") or 0, 4),
                          "name": dd.get("final_name"), "mock": m.get("mock"),
                          "characters": [c["name"] for c in (m.get("config") or {}).get("characters", [])],
                          "final": f"/output/{d}/final.png" if os.path.exists(os.path.join(OUTPUT_DIR, d, "final.png")) else None})
        except Exception:
            continue
    return jsonify(items)


if __name__ == "__main__":
    print(f"[MergeMeme {APP_VERSION}] http://localhost:{PORT}  key={'yes' if API_KEY else 'MISSING (.env)'}  mock={MOCK}")
    app.run(host="127.0.0.1", port=PORT, debug=False, threaded=True)
