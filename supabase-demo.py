"""
Tenant-isolated RAG using m-stash identity with Supabase Postgres + pgvector.

Requires the table and match_chunks RPC documented in README.md. Supabase's
service-role key bypasses RLS, so this backend verifies every m-stash token and
passes only its verified claims.uid to the SQL vector-search function.
"""

import hashlib
import os
import secrets
import time
from pathlib import Path
from typing import Any, Dict, List

import requests
from dotenv import load_dotenv

load_dotenv()

MSTASH_BASE_URL = os.getenv("MSTASH_BASE_URL", "https://m-stash.onrender.com").rstrip("/")
MSTASH_APP_KEY = os.getenv("MSTASH_APP_KEY", "pk_zA7kKpy3Ydk378fBJwtdCY4vRYaNA2mO")
MSTASH_TIMEOUT = float(os.getenv("MSTASH_TIMEOUT", "60"))
SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
VOYAGE_API_KEY = os.getenv("VOYAGE_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

EMBEDDING_MODEL = "voyage-3"
EMBEDDING_DIMENSIONS = 1024
CHAT_MODEL = "gemini-2.5-flash"

DEMO_USERS = {
    "alice": ("alice.ragdemo@example.com", "alice-ragdemo"),
    "bob": ("bob.ragdemo@example.com", "bob-ragdemo"),
}
_verify_cache: Dict[str, tuple] = {}


def _mstash(method: str, path: str, token: str = None, body: dict = None) -> requests.Response:
    headers = {"X-M-Stash-App": MSTASH_APP_KEY}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return requests.request(
        method,
        f"{MSTASH_BASE_URL}{path}",
        headers=headers,
        json=body,
        timeout=MSTASH_TIMEOUT,
    )


def verify_mstash_user(session_token: str) -> str:
    """Return the user ID from m-stash's server-verified token claims."""
    if not session_token:
        raise PermissionError("Missing m-stash session token.")
    cache_key = hashlib.sha256(session_token.encode()).hexdigest()
    cached = _verify_cache.get(cache_key)
    if cached and cached[1] > time.time():
        return cached[0]

    response = _mstash("GET", "/v1/auth/verify", token=session_token)
    if response.status_code != 200:
        raise PermissionError(f"m-stash rejected the token (HTTP {response.status_code}).")
    body = response.json()
    claims = body.get("claims") or {}
    user_id = claims.get("uid")
    if not body.get("active") or not user_id:
        raise PermissionError("m-stash token did not resolve to an active user.")

    expires_at = float(claims.get("expiresAt") or 0)
    _verify_cache[cache_key] = (
        str(user_id),
        min(time.time() + 300, expires_at),
    )
    return str(user_id)


def demo_password() -> str:
    """Return a local, gitignored password shared with demo.py."""
    if os.getenv("MSTASH_DEMO_PASSWORD"):
        return os.environ["MSTASH_DEMO_PASSWORD"]
    path = Path(__file__).with_name(".mstash_demo_password")
    if not path.exists():
        path.write_text(secrets.token_urlsafe(24))
        path.chmod(0o600)
    return path.read_text().strip()


def mstash_login_or_signup(email: str, username: str, password: str) -> str:
    response = _mstash("POST", "/v1/auth/login", body={"email": email, "password": password})
    if response.status_code == 401:
        response = _mstash(
            "POST",
            "/v1/auth/signup",
            body={"email": email, "password": password, "username": username},
        )
        if response.status_code == 409:
            raise RuntimeError(
                f"{email} already exists with a different password; "
                "set MSTASH_DEMO_PASSWORD to the original."
            )
    if response.status_code not in (200, 201):
        raise RuntimeError(f"m-stash auth failed: HTTP {response.status_code} {response.text}")
    return response.json()["token"]


def get_embeddings(texts: List[str], input_type: str) -> List[List[float]]:
    if not VOYAGE_API_KEY:
        raise RuntimeError("Set VOYAGE_API_KEY in .env.")
    response = requests.post(
        "https://api.voyageai.com/v1/embeddings",
        headers={"Authorization": f"Bearer {VOYAGE_API_KEY}"},
        json={"model": EMBEDDING_MODEL, "input": texts, "input_type": input_type},
        timeout=60,
    )
    response.raise_for_status()
    data = response.json()["data"]
    embeddings = [row["embedding"] for row in sorted(data, key=lambda row: row["index"])]
    if any(len(vector) != EMBEDDING_DIMENSIONS for vector in embeddings):
        raise ValueError(f"{EMBEDDING_MODEL} must return {EMBEDDING_DIMENSIONS}-dimension vectors.")
    return embeddings


def _supabase_request(
    method: str, path: str, headers: dict = None, **kwargs: Any
) -> requests.Response:
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        raise RuntimeError("Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY in .env.")
    request_headers = {
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
        "Content-Type": "application/json",
    }
    if headers:
        request_headers.update(headers)
    response = requests.request(
        method,
        f"{SUPABASE_URL}/rest/v1/{path}",
        headers=request_headers,
        timeout=60,
        **kwargs,
    )
    response.raise_for_status()
    return response


def _vector_literal(vector: List[float]) -> str:
    return "[" + ",".join(str(value) for value in vector) + "]"


def ingest_user_document(session_token: str, doc_id: str, chunks: List[str]) -> int:
    verified_user_id = verify_mstash_user(session_token)
    if not chunks:
        return 0
    embeddings = get_embeddings(chunks, "document")
    payload = [
        {
            "doc_id": doc_id,
            "chunk_index": index,
            "user_id": verified_user_id,
            "content": text,
            "embedding": _vector_literal(embedding),
        }
        for index, (text, embedding) in enumerate(zip(chunks, embeddings))
    ]
    _supabase_request("POST", "chunks", json=payload, headers={"Prefer": "return=minimal"})
    print(f"[Ingest] Stored {len(payload)} chunks for verified user='{verified_user_id}'")
    return len(payload)


def retrieve_user_chunks(
    session_token: str, query: str, limit: int = 3
) -> List[Dict[str, Any]]:
    verified_user_id = verify_mstash_user(session_token)
    query_vector = get_embeddings([query], "query")[0]
    # The caller cannot provide p_user_id: it comes only from /v1/auth/verify.
    response = _supabase_request(
        "POST",
        "rpc/match_chunks",
        json={
            "p_query_embedding": _vector_literal(query_vector),
            "p_user_id": verified_user_id,
            "p_match_count": limit,
        },
    )
    return response.json()


def ask_rag(question: str, hits: List[Dict[str, Any]]) -> str:
    if not hits:
        return "No relevant documents found in your private workspace."
    if not GEMINI_API_KEY:
        raise RuntimeError("Set GEMINI_API_KEY in .env.")
    context = "\n\n".join(f"- {hit['content']}" for hit in hits)
    response = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{CHAT_MODEL}:generateContent",
        headers={"x-goog-api-key": GEMINI_API_KEY},
        json={
            "systemInstruction": {
                "parts": [
                    {
                        "text": (
                            "Answer using ONLY the provided context. "
                            "If the answer is not present, say you don't know."
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


def main() -> None:
    password = demo_password()
    alice_token = mstash_login_or_signup(*DEMO_USERS["alice"], password)
    bob_token = mstash_login_or_signup(*DEMO_USERS["bob"], password)
    alice_id = verify_mstash_user(alice_token)
    bob_id = verify_mstash_user(bob_token)
    print("[m-stash] Resolved Alice and Bob from verified session tokens.")

    # These are scoped deletes: this demo never truncates the shared table.
    for user_id in (alice_id, bob_id):
        _supabase_request(
            "DELETE",
            "chunks",
            params={"user_id": f"eq.{user_id}"},
        )

    ingest_user_document(
        alice_token,
        "alice_notes_1",
        [
            "Project Apollo launch budget is $450,000 approved by Alice.",
            "Project Apollo target launch date is October 14.",
        ],
    )
    ingest_user_document(
        bob_token,
        "bob_notes_1",
        [
            "Project Apollo secret codename is Silver Fox, managed by Bob.",
            "Project Apollo server keys are stored in Vault-B.",
        ],
    )

    question = "What is the Project Apollo codename and budget?"
    for name, token, user_id in (
        ("ALICE", alice_token, alice_id),
        ("BOB", bob_token, bob_id),
    ):
        print(f"\n--- Querying as {name} ---")
        hits = retrieve_user_chunks(token, question)
        if any(hit["user_id"] != user_id for hit in hits):
            raise AssertionError("Cross-tenant result returned from Supabase vector search.")
        for hit in hits:
            print(f"  [score={hit['score']:.4f} | owner={hit['user_id']}] {hit['content']}")
        print("  Answer:", ask_rag(question, hits))

    print("\n--- Forged token ---")
    try:
        retrieve_user_chunks(
            "eyJhbGciOiJIUzI1NiJ9.eyJ1aWQiOiJhbGljZSJ9.forged",
            question,
        )
    except PermissionError as exc:
        print(f"  Refused before touching Supabase: {exc}")
    else:
        raise AssertionError("A forged token reached Supabase.")


if __name__ == "__main__":
    main()
