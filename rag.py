# -*- coding: utf-8 -*-
"""RAG.ipynb

Automatically generated by Colab.

Original file is located at
    https://colab.research.google.com/drive/1FZBdmx5WKSaLtMYcu5-JcBLVzCYIn1Wy

Step 1: Imports and configuration
"""

import json, os, re, unicodedata, pathlib
from typing import List, Dict, Tuple

import torch, faiss
from sentence_transformers import SentenceTransformer
from transformers import AutoTokenizer, AutoModelForSeq2SeqLM

DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")

KB_PATH   = "knowledge_base.json"
EMBED_MODEL_NAME = "all-MiniLM-L6-v2"
GEN_MODEL_NAME   = "google/flan-t5-small"

INDEX_FILE = "faiss.index"
EMB_FILE   = "embeddings.npy"

TOP_K = 3                    # retrieved chunks
DISTANCE_THRESHOLD = 1     # > threshold → “I don’t know”
HISTORY_MAX = 3              # previous Q-A pairs fed back to model

"""Step 2: Simple text cleaner"""

PUNCT_RE = re.compile(r"[^\w\s]")

def clean(text: str) -> str:
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode()  # drop non-ASCII remnants
    text = text.lower()
    text = PUNCT_RE.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()

"""Step 3: Load the two models (MiniLM + Flan-T5)"""

embedder  = SentenceTransformer(EMBED_MODEL_NAME, device=DEVICE)
tokenizer = AutoTokenizer.from_pretrained(GEN_MODEL_NAME)
generator = AutoModelForSeq2SeqLM.from_pretrained(GEN_MODEL_NAME).to(DEVICE)

"""Step 4: Read the restaurants knowledge base"""

def load_kb(path: str) -> List[Dict]:
    data = json.load(open(path, encoding="utf-8"))
    return list(data.values()) if isinstance(data, dict) else data

restaurants = load_kb(KB_PATH)

"""Step 5: Flatten each menu item into an indexable text chunk"""

texts, meta = [], []

for r in restaurants:
    rname, rloc = r["restaurant_name"], r["location"]
    for it in r["items"]:
        txt_raw = (
            f"{rname} {rloc} {it['category']} "
            f"{it['item_name']} {it['description']} "
            f"{' '.join(it['special_features'] or [])}"
        )
        texts.append(clean(txt_raw))
        meta.append(
            {
                "restaurant": rname,
                "category"  : it["category"],
                "item_name" : it["item_name"],
                "url"       : it["product_url"],
                "price"     : it["price"],
                "features"  : it["special_features"],
            }
        )

print(f"Prepared {len(texts)} chunks")

"""Step 6: Build an FAISS index"""

if os.path.exists(INDEX_FILE) and os.path.exists(EMB_FILE):
    faiss_index = faiss.read_index(INDEX_FILE)
else:
    emb = embedder.encode(texts, batch_size=64, show_progress_bar=True,
                          convert_to_numpy=True)
    faiss_index = faiss.IndexFlatL2(emb.shape[1])
    faiss_index.add(emb)
    faiss.write_index(faiss_index, INDEX_FILE)
    emb.tofile(EMB_FILE)

print("Index size:", faiss_index.ntotal)

"""Step 7: Top-k retrieval"""

def retrieve(query: str, k: int = TOP_K) -> List[Dict]:
    q_emb = embedder.encode([clean(query)], convert_to_numpy=True)
    dist, idx = faiss_index.search(q_emb, k)
    return [
        {
            "text"    : texts[i],
            "meta"    : meta[i],
            "distance": float(dist[0][rank]),
        }
        for rank, i in enumerate(idx[0])
    ]

"""Step 8: Minimal history"""

class Conversation:
    def __init__(self, max_turns: int = HISTORY_MAX):
        self.max = max_turns
        self.memory: List[Tuple[str, str]] = []

    def add(self, user: str, assistant: str) -> None:
        self.memory.append((user, assistant))
        if len(self.memory) > self.max:
            self.memory.pop(0)

    def format_history(self) -> str:
        if not self.memory:
            return ""
        lines = [f"User: {u}\nAssistant: {a}" for u, a in self.memory]
        return "\n".join(lines) + "\n"

"""Step 9: Build prompt with context + history"""

SYSTEM = (
    "You answer questions about restaurant menus using ONLY the CONTEXT. "
    "If the answer cannot be found, say you do not know."
)

def make_prompt(query: str,
                ctx_chunks: List[str],
                history: str) -> str:
    ctx = "\n".join(ctx_chunks)
    return (
        f"{SYSTEM}\n\n"
        f"{history}"
        f"CONTEXT:\n{ctx}\n\n"
        f"Question: {query}\nAnswer:"
    )

"""Step 10: Decode helpers"""

def dedupe_tokens(text: str) -> str:
    toks = text.split()
    out = [toks[0]] if toks else []
    for tok in toks[1:]:
        if tok != out[-1]:
            out.append(tok)
    return " ".join(out)

"""Step 11: RAG answer function"""

def answer(query: str, conv: Conversation,
           top_k: int = TOP_K) -> Tuple[str, List[Dict]]:
    retrieved = retrieve(query, top_k)

    # out-of-scope / ambiguous check
    if (not retrieved) or (retrieved[0]["distance"] > DISTANCE_THRESHOLD):
        response = (
            "I do not know. The knowledge base does not contain "
            "information relevant to this question."
        )
        conv.add(query, response)
        return response, []                 # ← return empty list instead of nothing

    prompt = make_prompt(
        query,
        [r["text"] for r in retrieved],
        conv.format_history()
    )

    inputs = tokenizer(
        prompt,
        return_tensors="pt",
        truncation=True,
        max_length=512
    ).to(DEVICE)

    out_ids = generator.generate(
        **inputs,
        max_length=220,
        num_beams=4,
        temperature=0.7,
        no_repeat_ngram_size=3,
        repetition_penalty=1.15
    )

    response = tokenizer.decode(out_ids[0], skip_special_tokens=True)
    response = dedupe_tokens(response)
    conv.add(query, response)
    return response, retrieved              # ← always two objects

"""Step 12: Demo query"""

if __name__ == "__main__":
    chat = Conversation()

    user_query = "is Big Mac® vegetarian food?"
    reply, ctx = answer(user_query, chat)

    print("Query:")
    print(user_query)
    print("\nAnswer:")
    print(reply)
    print("\nRetrieved context:")
    for r in ctx:
      print(f"- {r['text']}  (distance={r['distance']:.3f})")
      print("\n")

