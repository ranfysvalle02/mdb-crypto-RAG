"""
Tenant-isolated RAG: m-stash identity + MongoDB Atlas Vector Search.

  Identity  (m-stash)   GET /v1/auth/verify turns a bearer token into a verified uid.
                        A user_id is never read from client input.
  Ingestion (MongoDB)   Chunks are embedded and stamped with the verified uid.
  Retrieval (Atlas)     $vectorSearch pre-filters on {"user_id": {"$eq": uid}}, so one
                        user never sees another user's chunks, even for similar queries.

Run:
  pip install pymongo requests python-dotenv
  # Put VOYAGE_API_KEY, GEMINI_API_KEY, and MONGODB_URI in .env
  python demo.py
"""

import hashlib
import os
import secrets
import time
from pathlib import Path
from typing import Any, Dict, List

import requests
from dotenv import load_dotenv
from pymongo import MongoClient
from pymongo.operations import SearchIndexModel

load_dotenv()

# ---------------------------------------------------------------------------
# 1. CONFIGURATION
# ---------------------------------------------------------------------------
MONGODB_URI = os.getenv("MONGODB_URI")
VOYAGE_API_KEY = os.getenv("VOYAGE_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
MSTASH_BASE_URL = os.getenv("MSTASH_BASE_URL", "https://m-stash.onrender.com").rstrip("/")
# Publishable key: safe to ship. Never put the sk_ secret key here.
MSTASH_APP_KEY = os.getenv("MSTASH_APP_KEY", "pk_zA7kKpy3Ydk378fBJwtdCY4vRYaNA2mO")
# m-stash can cold-start for tens of seconds after idle.
MSTASH_TIMEOUT = float(os.getenv("MSTASH_TIMEOUT", "60"))

DB_NAME = "rag_demo"
COLLECTION_NAME = "chunks"
INDEX_NAME = "vector_index"
EMBEDDING_MODEL = "voyage-3"
EMBEDDING_DIMENSIONS = 1024
CHAT_MODEL = "gemini-2.5-flash"

mongo_client = MongoClient(MONGODB_URI or "mongodb://localhost:27017")
chunks_col = mongo_client[DB_NAME][COLLECTION_NAME]


# ---------------------------------------------------------------------------
# 2. AUTH BOUNDARY: VERIFIED USER_ID FROM M-STASH
# ---------------------------------------------------------------------------
_verify_cache: Dict[str, tuple] = {}
VERIFY_CACHE_SECONDS = 300


def _mstash(method: str, path: str, token: str = None, json: dict = None) -> requests.Response:
    headers = {"X-M-Stash-App": MSTASH_APP_KEY}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return requests.request(
        method, f"{MSTASH_BASE_URL}{path}", headers=headers, json=json, timeout=MSTASH_TIMEOUT
    )


def verify_mstash_user(session_token: str) -> str:
    """
    Verifies an m-stash bearer token server-side and returns the authoritative uid.
    m-stash signs tokens with HS256, so a backend cannot verify them offline; it must
    ask /v1/auth/verify. Tokens issued for a different app are rejected there too.
    """
    if not session_token:
        raise PermissionError("Missing m-stash session token.")

    cache_key = hashlib.sha256(session_token.encode()).hexdigest()
    cached = _verify_cache.get(cache_key)
    if cached and cached[1] > time.time():
        return cached[0]

    resp = _mstash("GET", "/v1/auth/verify", token=session_token)
    if resp.status_code != 200:
        raise PermissionError(f"m-stash rejected the token (HTTP {resp.status_code}).")

    body = resp.json()
    claims = body.get("claims") or {}
    user_id = claims.get("uid")
    if not body.get("active") or not user_id:
        raise PermissionError("m-stash token did not resolve to an active user.")

    # Cache briefly and never beyond the token's own expiry.
    expires_at = float(claims.get("expiresAt") or 0)
    _verify_cache[cache_key] = (str(user_id), min(time.time() + VERIFY_CACHE_SECONDS, expires_at))
    return str(user_id)


def mstash_login_or_signup(email: str, username: str, password: str) -> str:
    """Logs in an existing account, or signs it up on first run. Returns a bearer token."""
    resp = _mstash("POST", "/v1/auth/login", json={"email": email, "password": password})
    if resp.status_code == 401:
        resp = _mstash(
            "POST",
            "/v1/auth/signup",
            json={"email": email, "password": password, "username": username},
        )
        if resp.status_code == 409:
            raise RuntimeError(
                f"{email} already exists with a different password "
                f"(code={resp.json().get('code')}). Set MSTASH_DEMO_PASSWORD to the original."
            )
    if resp.status_code not in (200, 201):
        raise RuntimeError(f"m-stash auth failed for {email}: HTTP {resp.status_code} {resp.text}")
    return resp.json()["token"]


def demo_password() -> str:
    """A per-checkout random password for the demo accounts, kept out of git."""
    if os.getenv("MSTASH_DEMO_PASSWORD"):
        return os.environ["MSTASH_DEMO_PASSWORD"]
    path = Path(__file__).with_name(".mstash_demo_password")
    if not path.exists():
        path.write_text(secrets.token_urlsafe(24))
        path.chmod(0o600)
    return path.read_text().strip()


# ---------------------------------------------------------------------------
# 3. EMBEDDINGS + VECTOR INDEX
# ---------------------------------------------------------------------------
def get_embeddings(texts: List[str], input_type: str) -> List[List[float]]:
    if not VOYAGE_API_KEY:
        raise RuntimeError("Set VOYAGE_API_KEY in .env to generate embeddings.")
    response = requests.post(
        "https://api.voyageai.com/v1/embeddings",
        headers={"Authorization": f"Bearer {VOYAGE_API_KEY}"},
        json={"model": EMBEDDING_MODEL, "input": texts, "input_type": input_type},
        timeout=60,
    )
    response.raise_for_status()
    data = response.json()["data"]
    return [row["embedding"] for row in sorted(data, key=lambda row: row["index"])]


def ensure_vector_index(timeout: float = 120) -> None:
    """Creates the vector index (with user_id as a filter field) and waits until queryable."""
    if COLLECTION_NAME not in chunks_col.database.list_collection_names():
        chunks_col.database.create_collection(COLLECTION_NAME)

    definition = {
        "fields": [
            {
                "type": "vector",
                "path": "embedding",
                "numDimensions": EMBEDDING_DIMENSIONS,
                "similarity": "cosine",
            },
            {"type": "filter", "path": "user_id"},
        ]
    }
    indexes = list(chunks_col.list_search_indexes(INDEX_NAME))
    if not indexes:
        chunks_col.create_search_index(
            SearchIndexModel(name=INDEX_NAME, type="vectorSearch", definition=definition)
        )
        print(f"[Index] Created '{INDEX_NAME}', waiting for it to become queryable...")
    else:
        current_definition = indexes[0].get("latestDefinition", {})
        fields = current_definition.get("fields", [])
        vector_field = next(
            (field for field in fields if field.get("type") == "vector" and field.get("path") == "embedding"),
            {},
        )
        has_user_filter = any(
            field.get("type") == "filter" and field.get("path") == "user_id" for field in fields
        )
        if vector_field.get("numDimensions") != EMBEDDING_DIMENSIONS or not has_user_filter:
            chunks_col.update_search_index(INDEX_NAME, definition)
            print(f"[Index] Updated '{INDEX_NAME}' for Voyage embeddings and user_id filtering...")

    deadline = time.time() + timeout
    while time.time() < deadline:
        index = next(iter(chunks_col.list_search_indexes(INDEX_NAME)), None)
        if index and index.get("queryable"):
            return
        time.sleep(2)
    raise TimeoutError(f"Vector index '{INDEX_NAME}' was not queryable after {timeout}s.")


# ---------------------------------------------------------------------------
# 4. INGESTION: STAMP CHUNKS WITH VERIFIED USER_ID
# ---------------------------------------------------------------------------
def ingest_user_document(session_token: str, doc_id: str, chunks: List[str]) -> int:
    verified_user_id = verify_mstash_user(session_token)
    if not chunks:
        return 0

    docs = [
        {
            "doc_id": doc_id,
            "chunk_index": idx,
            "user_id": verified_user_id,  # indexed as type "filter"
            "text": text,
            "embedding": embedding,
        }
        for idx, (text, embedding) in enumerate(zip(chunks, get_embeddings(chunks, "document")))
    ]
    chunks_col.insert_many(docs)
    print(f"[Ingest] Stored {len(docs)} chunks for user='{verified_user_id}' (doc='{doc_id}')")
    return len(docs)


# ---------------------------------------------------------------------------
# 5. RETRIEVAL: ATLAS VECTOR SEARCH WITH PRE-FILTERING
# ---------------------------------------------------------------------------
def _vector_search(user_id: str, query_vector: List[float], limit: int) -> List[Dict[str, Any]]:
    if not user_id:
        raise PermissionError("Refusing to search without a verified owner.")
    pipeline = [
        {
            "$vectorSearch": {
                "index": INDEX_NAME,
                "path": "embedding",
                "queryVector": query_vector,
                "numCandidates": max(50, limit * 10),
                "limit": limit,
                # Pre-filter inside $vectorSearch. A later $match would run after the top-k
                # has been chosen, so other tenants' chunks could crowd out this user's.
                "filter": {"user_id": {"$eq": user_id}},
            }
        },
        {
            "$project": {
                "_id": 0,
                "doc_id": 1,
                "user_id": 1,
                "text": 1,
                "score": {"$meta": "vectorSearchScore"},
            }
        },
    ]
    return list(chunks_col.aggregate(pipeline))


def retrieve_user_chunks(session_token: str, query: str, limit: int = 3) -> List[Dict[str, Any]]:
    verified_user_id = verify_mstash_user(session_token)
    return _vector_search(verified_user_id, get_embeddings([query], "query")[0], limit)


def wait_until_searchable(user_id: str, expected: int, timeout: float = 60) -> None:
    """Atlas indexes new documents asynchronously; wait so the demo isn't racing the index."""
    sample = chunks_col.find_one({"user_id": user_id}, {"embedding": 1})
    deadline = time.time() + timeout
    while time.time() < deadline:
        if len(_vector_search(user_id, sample["embedding"], limit=expected + 5)) >= expected:
            return
        time.sleep(1)
    raise TimeoutError(f"Chunks for user '{user_id}' were not searchable after {timeout}s.")


# ---------------------------------------------------------------------------
# 6. RAG GENERATION: ANSWER USING ONLY ISOLATED CONTEXT
# ---------------------------------------------------------------------------
def ask_rag(session_token: str, question: str, hits: List[Dict[str, Any]] = None) -> str:
    if hits is None:
        hits = retrieve_user_chunks(session_token, question, limit=3)
    if not hits:
        return "No relevant documents found in your private workspace."

    context = "\n\n".join(f"- {h['text']}" for h in hits)
    if not GEMINI_API_KEY:
        raise RuntimeError("Set GEMINI_API_KEY in .env to generate answers.")
    response = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{CHAT_MODEL}:generateContent",
        headers={"x-goog-api-key": GEMINI_API_KEY},
        json={
            "systemInstruction": {
                "parts": [
                    {
                        "text": (
                            "Answer the user's question using ONLY the provided context. "
                            "If part of the answer is not in the context, say you don't know that part."
                        )
                    }
                ]
            },
            "contents": [
                {"role": "user", "parts": [{"text": f"Context:\n{context}\n\nQuestion: {question}"}]}
            ],
            "generationConfig": {"temperature": 0},
        },
        timeout=60,
    )
    response.raise_for_status()
    parts = response.json()["candidates"][0]["content"]["parts"]
    return "".join(part["text"] for part in parts if "text" in part)


# ---------------------------------------------------------------------------
# 7. END-TO-END TENANT ISOLATION SHOWCASE
# ---------------------------------------------------------------------------
DEMO_USERS = {
    "alice": ("alice.ragdemo@example.com", "alice-ragdemo"),
    "bob": ("bob.ragdemo@example.com", "bob-ragdemo"),
}


def main() -> None:
    if not MONGODB_URI:
        raise SystemExit("Set MONGODB_URI in .env (Atlas, or a mongodb-atlas-local container).")

    print(f"[m-stash] Signing in demo users via {MSTASH_BASE_URL} (first call may cold-start)...")
    password = demo_password()
    alice_token = mstash_login_or_signup(*DEMO_USERS["alice"], password)
    bob_token = mstash_login_or_signup(*DEMO_USERS["bob"], password)

    # From here on, only tokens cross the boundary; identity comes from m-stash.
    alice_id = verify_mstash_user(alice_token)
    bob_id = verify_mstash_user(bob_token)
    print(f"[m-stash] alice -> uid={alice_id}")
    print(f"[m-stash] bob   -> uid={bob_id}")

    ensure_vector_index()
    chunks_col.delete_many({"user_id": {"$in": [alice_id, bob_id]}})

    # Both users store confidential notes about the same project.
    ingest_user_document(
        alice_token,
        doc_id="alice_notes_1",
        chunks=[
            "Project Apollo launch budget is $450,000 approved by Alice.",
            "Project Apollo target launch date is October 14.",
        ],
    )
    ingest_user_document(
        bob_token,
        doc_id="bob_notes_1",
        chunks=[
            "Project Apollo secret codename is Silver Fox, managed by Bob.",
            "Project Apollo server keys are stored in Vault-B.",
        ],
    )
    wait_until_searchable(alice_id, expected=2)
    wait_until_searchable(bob_id, expected=2)

    question = "What is the Project Apollo codename and budget?"
    for name, token, uid in (("ALICE", alice_token, alice_id), ("BOB", bob_token, bob_id)):
        print(f"\n--- Querying as {name} ---")
        hits = retrieve_user_chunks(token, question)
        for h in hits:
            print(f"  [score={h['score']:.4f} | owner={h['user_id']}] {h['text']}")
        leaked = [h for h in hits if h["user_id"] != uid]
        assert not leaked, f"Cross-tenant leak: {leaked}"
        print("  Answer:", ask_rag(token, question, hits=hits))

    print("\n--- Forged token ---")
    try:
        retrieve_user_chunks("eyJhbGciOiJIUzI1NiJ9.eyJ1aWQiOiJhbGljZSJ9.forged", question)
        raise AssertionError("A forged token reached the database.")
    except PermissionError as exc:
        print(f"  Refused before touching MongoDB: {exc}")


if __name__ == "__main__":
    main()
