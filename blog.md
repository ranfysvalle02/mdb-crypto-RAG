# Tenant isolation is part of the retrieval algorithm

*A small RAG demo with m-stash, MongoDB Atlas Vector Search, Voyage embeddings, and Gemini—and what changes when the vector store is Supabase.*

RAG makes it easy to retrieve text that is semantically close to a question. That is useful when the text belongs to one person. In a multi-user application, it is also a way to accidentally retrieve the wrong person's private data.

That risk gets more serious with sensitive information. Imagine two patients whose records both discuss chest pain, medication, or a diagnosis. A semantic search can find another patient's note because it is relevant, not because the application intended to share it. A model prompt is not an access-control boundary: once the wrong chunk reaches the prompt, the privacy failure has already happened.

The demo in [`demo.py`](demo.py) focuses on one invariant:

> Every chunk is stored with the identity verified from the session, and every retrieval is restricted to that same verified identity before results are returned.

## Trust the verified identity, not a request field

The browser sends a session token, not an owner ID. The backend asks m-stash to verify the token at `/v1/auth/verify`, then uses `claims.uid`. It does not accept a `user_id` from the request body or decode an unverified JWT.

On ingestion, the backend stamps every chunk with that verified ID. At retrieval, the identity is resolved again and passed into the vector-search filter:

```python
"filter": {"user_id": {"$eq": verified_user_id}}
```

This belongs inside `$vectorSearch`, not in a `$match` stage after vector search. Filtering after the top results have already been selected can let other tenants' chunks crowd the result set; those chunks might be discarded later, but the user's own relevant chunks can be missed.

Filtering only by a document ID, topic, organization label, or other client-controlled metadata is not a substitute for ownership. A client may know or guess those values, and semantic overlap is expected. Other criteria can narrow an already authorized search, but they must not replace the owner check. If a product needs shared records—for example, a care team—model explicit memberships and permissions. Do not silently turn a patient-level boundary into a topic-level one.

## What the live demo returned

Both demo users asked the same question about Project Apollo. Their stored chunks intentionally overlap semantically:

```text
--- Querying as ALICE ---
  Project Apollo launch budget is $450,000 approved by Alice.
  Project Apollo target launch date is October 14.
  Answer: I don't know the Project Apollo codename. The Project Apollo budget is $450,000.

--- Querying as BOB ---
  Project Apollo secret codename is Silver Fox, managed by Bob.
  Project Apollo server keys are stored in Vault-B.
  Answer: The Project Apollo codename is Silver Fox. I don't know the budget for Project Apollo.

--- Forged token ---
  Refused before touching MongoDB: m-stash rejected the token (HTTP 403).
```

The full run used live m-stash verification, Voyage embeddings, Gemini generation, and Atlas Vector Search. It is a useful integration check, not a security certification or proof that every deployment is safe.

## What feels harder with Supabase

Supabase can implement the same isolation. The friction is at the boundary between the authentication model and the database policy.

Supabase Row Level Security commonly uses Supabase Auth's JWT claims—for example, `auth.uid()`—to decide which rows a request may access. In this example, identity comes from m-stash. An m-stash token is not automatically a Supabase Auth token, so Supabase's `auth.uid()` does not magically become the m-stash user ID.

The comparison script, [`supabase-demo.py`](supabase-demo.py), therefore uses a backend-only Supabase service-role key. The server verifies the m-stash token, then passes that verified UID to a narrowly scoped Postgres RPC. The SQL applies `WHERE c.user_id = p_user_id` before ordering and limiting the matches.

That approach works, but it creates a sharp edge: the service-role key bypasses RLS. If a future endpoint accepts `p_user_id` from the browser, forgets the SQL predicate, or calls a broad table query, the database will not rescue the request with per-user RLS. Keep the service key server-side, verify identity before each data operation, and make the owner predicate part of every read, update, and delete. The SQL function is also extra schema and migration work compared with putting a filter directly in an Atlas `$vectorSearch` stage.

There is a separate retrieval trade-off. In Postgres with pgvector, an HNSW approximate-nearest-neighbor scan plus a `WHERE user_id = ...` condition may return fewer than the requested number of matches under some distributions, because filtering and approximate index traversal interact. The SQL condition still limits which rows can be returned; this is a recall/performance issue, not permission to remove the ownership condition. Test with realistic tenant sizes and use an index/query strategy appropriate to the deployment.

This is not a claim that Supabase is insecure or that MongoDB makes isolation automatic. Both need deliberate access-control design and tests. Supabase Auth plus RLS can be a clean fit when Supabase owns the identity boundary. With an external identity provider and a privileged service key, more of the tenant boundary becomes an application and SQL-function responsibility.

## Sensitive-data warning

For medical records or other regulated data, tenant filtering is necessary but not sufficient. A vector index and an LLM prompt are additional places where sensitive text is processed. Before using real records, assess the applicable legal and contractual requirements, provider data-retention and training policies, encryption and key management, audit logging, backups, deletion behavior, access reviews, and incident response. Confirm that every vendor and deployment is approved for the data; this demo does not establish HIPAA compliance or any other regulatory compliance.

Use synthetic text for development. Avoid logging retrieved chunks, prompts, access tokens, database URIs, or provider keys. A retrieval bug can expose data even when the generation prompt says “answer only from this context.”

## Running the examples

The MongoDB version is documented in [`README.md`](README.md). The Supabase version uses the same m-stash demo accounts and Voyage/Gemini keys, plus a Supabase URL and server-only service-role key. Its table, vector index, and `match_chunks` RPC setup SQL are also in the README. Both examples explicitly assert that returned hits belong to the verified user and reject a forged token before database access.
