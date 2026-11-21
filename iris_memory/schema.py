"""Versioned DDL; see docs/memory-storage.md for the data dictionary."""

SOURCE_SCHEMA = (
    "CREATE TABLE owners (id TEXT PRIMARY KEY)",
    (
        "CREATE TABLE conversations (id TEXT PRIMARY KEY, owner TEXT NOT NULL REFERENCES "
        "owners(id), created_at TEXT NOT NULL, UNIQUE(owner, id))"
    ),
    (
        "CREATE TABLE active_conversations (owner TEXT PRIMARY KEY REFERENCES owners(id),"
        " conversation TEXT NOT NULL, FOREIGN KEY(owner, conversation) REFERENCES "
        "conversations(owner, id) ON DELETE CASCADE)"
    ),
    (
        "CREATE TABLE exchanges (sequence INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT "
        "NULL UNIQUE, owner TEXT NOT NULL, conversation TEXT NOT NULL, created_at TEXT "
        "NOT NULL, origin TEXT NOT NULL CHECK(origin IN ('human','automatic')), user_text"
        " TEXT NOT NULL, assistant_text TEXT NOT NULL, tool_names TEXT NOT NULL, FOREIGN "
        "KEY(owner, conversation) REFERENCES conversations(owner, id) ON DELETE CASCADE)"
    ),
    "CREATE INDEX conversation_exchanges ON exchanges(conversation, sequence)",
    "PRAGMA user_version=1",
)

SEARCH_SCHEMA = (
    (
        "CREATE TABLE summaries (id TEXT PRIMARY KEY, owner TEXT NOT NULL, conversation "
        "TEXT NOT NULL, through_sequence INTEGER NOT NULL, created_at TEXT NOT NULL "
        "DEFAULT CURRENT_TIMESTAMP, payload TEXT NOT NULL, FOREIGN "
        "KEY(owner,conversation) REFERENCES conversations(owner,id) ON DELETE CASCADE, "
        "UNIQUE(conversation,through_sequence))"
    ),
    (
        "CREATE TABLE notes (id TEXT PRIMARY KEY, summary TEXT NOT NULL REFERENCES "
        "summaries(id) ON DELETE CASCADE, owner TEXT NOT NULL, conversation TEXT NOT "
        "NULL, text TEXT NOT NULL, kind TEXT NOT NULL, scope TEXT NOT NULL, status TEXT "
        "NOT NULL, sources TEXT NOT NULL)"
    ),
    "CREATE INDEX conversation_notes ON notes(owner,conversation)",
    (
        "CREATE TABLE summary_jobs (conversation TEXT PRIMARY KEY, owner TEXT NOT NULL, "
        "attempts INTEGER NOT NULL DEFAULT 0, retry_at REAL NOT NULL DEFAULT 0, FOREIGN "
        "KEY(owner,conversation) REFERENCES conversations(owner,id) ON DELETE CASCADE)"
    ),
    (
        "CREATE TABLE recall_blocks (owner TEXT NOT NULL, conversation TEXT PRIMARY KEY "
        "REFERENCES conversations(id) ON DELETE CASCADE)"
    ),
    (
        "CREATE TABLE chunks (id INTEGER PRIMARY KEY, exchange TEXT NOT NULL REFERENCES "
        "exchanges(id) ON DELETE CASCADE, owner TEXT NOT NULL, conversation TEXT NOT "
        "NULL, created_at TEXT NOT NULL, text TEXT NOT NULL, superseded INTEGER NOT NULL "
        "DEFAULT 0)"
    ),
    "CREATE INDEX chunk_sources ON chunks(exchange)",
    ("CREATE VIRTUAL TABLE chunks_fts USING fts5(text, content='chunks', content_rowid='id')"),
    (
        "CREATE TRIGGER chunks_insert AFTER INSERT ON chunks BEGIN INSERT INTO "
        "chunks_fts(rowid,text) VALUES (new.id,new.text); END"
    ),
    (
        "CREATE TRIGGER chunks_delete AFTER DELETE ON chunks BEGIN INSERT INTO "
        "chunks_fts(chunks_fts,rowid,text) VALUES ('delete',old.id,old.text); END"
    ),
    "PRAGMA user_version=2",
)

VECTOR_SCHEMA = (
    "CREATE TABLE embedding_models (id TEXT PRIMARY KEY, dimensions INTEGER NOT NULL)",
    (
        "CREATE TABLE embedding_vectors (chunk INTEGER NOT NULL REFERENCES chunks(id) ON "
        "DELETE CASCADE, model TEXT NOT NULL REFERENCES embedding_models(id), text_hash "
        "TEXT NOT NULL, vector BLOB NOT NULL, PRIMARY KEY(chunk,model))"
    ),
    "PRAGMA user_version=3",
)

DETAIL_SCHEMA = (
    (
        "CREATE TABLE memory_details (owner TEXT NOT NULL REFERENCES owners(id), label "
        "TEXT NOT NULL, text TEXT NOT NULL, updated_at TEXT NOT NULL, PRIMARY "
        "KEY(owner,label))"
    ),
    (
        "CREATE TABLE detail_uses (owner TEXT NOT NULL, label TEXT NOT NULL, conversation"
        " TEXT NOT NULL, FOREIGN KEY(owner,label) REFERENCES memory_details(owner,label) "
        "ON DELETE CASCADE, FOREIGN KEY(owner,conversation) REFERENCES "
        "conversations(owner,id) ON DELETE CASCADE, PRIMARY "
        "KEY(owner,label,conversation))"
    ),
    (
        "CREATE TABLE companion_topics (owner TEXT NOT NULL, conversation TEXT NOT NULL, "
        "blocked INTEGER NOT NULL DEFAULT 0, offered_at TEXT, PRIMARY "
        "KEY(owner,conversation), FOREIGN KEY(owner,conversation) REFERENCES "
        "conversations(owner,id) ON DELETE CASCADE)"
    ),
    (
        "CREATE TABLE conversation_uses (owner TEXT NOT NULL, source TEXT NOT NULL, "
        "target TEXT NOT NULL, PRIMARY KEY(owner,source,target), FOREIGN "
        "KEY(owner,source) REFERENCES conversations(owner,id) ON DELETE CASCADE, FOREIGN "
        "KEY(owner,target) REFERENCES conversations(owner,id) ON DELETE CASCADE)"
    ),
    "PRAGMA user_version=5",
)

HIERARCHY_SCHEMA = (
    (
        "CREATE TABLE memory_facts (id TEXT PRIMARY KEY, owner TEXT NOT NULL, fact_key "
        "TEXT NOT NULL, value TEXT NOT NULL, kind TEXT NOT NULL, scope TEXT NOT NULL, "
        "lifetime TEXT NOT NULL, importance TEXT NOT NULL, status TEXT NOT NULL, source "
        "TEXT NOT NULL REFERENCES exchanges(id) ON DELETE CASCADE, evidence TEXT NOT "
        "NULL, supersedes TEXT REFERENCES memory_facts(id) ON DELETE SET NULL, "
        "detail_label TEXT, FOREIGN KEY(owner,detail_label) REFERENCES "
        "memory_details(owner,label) ON DELETE CASCADE)"
    ),
    "CREATE INDEX fact_selection ON memory_facts(owner,status,scope,lifetime)",
    "CREATE INDEX detail_fact_selection ON memory_facts(owner,detail_label,status)",
    (
        "CREATE TABLE detail_supersessions (owner TEXT NOT NULL, label TEXT NOT NULL, "
        "PRIMARY KEY(owner,label), FOREIGN KEY(owner,label) REFERENCES "
        "memory_details(owner,label) ON DELETE CASCADE)"
    ),
    (
        "CREATE TABLE memory_topics (id TEXT PRIMARY KEY, owner TEXT NOT NULL, label TEXT"
        " NOT NULL, scope TEXT NOT NULL, source TEXT NOT NULL REFERENCES exchanges(id) ON"
        " DELETE CASCADE, UNIQUE(owner,id))"
    ),
    "CREATE INDEX topic_selection ON memory_topics(owner,label)",
    (
        "CREATE TABLE memory_attention (topic TEXT NOT NULL REFERENCES memory_topics(id) "
        "ON DELETE CASCADE, source TEXT NOT NULL REFERENCES exchanges(id) ON DELETE "
        "CASCADE, engaged INTEGER NOT NULL, status TEXT NOT NULL, evidence TEXT NOT NULL,"
        " PRIMARY KEY(topic,source))"
    ),
    "PRAGMA user_version=6",
)

INDEX_SCHEMA = (
    "CREATE INDEX IF NOT EXISTS embedding_vectors_model_chunk ON embedding_vectors(model, chunk)",
    "CREATE INDEX IF NOT EXISTS chunks_owner_active ON chunks(owner, superseded, id)",
    "CREATE INDEX IF NOT EXISTS chunks_owner_conversation ON chunks(owner, conversation)",
    (
        "CREATE INDEX IF NOT EXISTS exchanges_owner_conversation_sequence "
        "ON exchanges(owner, conversation, sequence)"
    ),
    (
        "CREATE INDEX IF NOT EXISTS summary_jobs_schedule "
        "ON summary_jobs(owner, attempts, retry_at, last_served)"
    ),
    "CREATE INDEX IF NOT EXISTS notes_summary ON notes(summary)",
    "CREATE INDEX IF NOT EXISTS notes_owner_status ON notes(owner, status)",
    "CREATE INDEX IF NOT EXISTS memory_facts_source ON memory_facts(source)",
    "CREATE INDEX IF NOT EXISTS memory_facts_supersedes ON memory_facts(supersedes)",
    (
        "CREATE INDEX IF NOT EXISTS memory_facts_lookup "
        "ON memory_facts(owner, fact_key, scope, status)"
    ),
    "CREATE INDEX IF NOT EXISTS memory_topics_source ON memory_topics(source)",
    "CREATE INDEX IF NOT EXISTS memory_attention_source ON memory_attention(source)",
    "CREATE INDEX IF NOT EXISTS detail_uses_owner_conversation ON detail_uses(owner, conversation)",
    "CREATE INDEX IF NOT EXISTS conversation_uses_owner_target ON conversation_uses(owner, target)",
    (
        "CREATE TRIGGER IF NOT EXISTS chunks_update AFTER UPDATE OF text ON chunks BEGIN "
        "INSERT INTO chunks_fts(chunks_fts,rowid,text) VALUES ('delete',old.id,old.text); "
        "INSERT INTO chunks_fts(rowid,text) VALUES (new.id,new.text); END"
    ),
)
