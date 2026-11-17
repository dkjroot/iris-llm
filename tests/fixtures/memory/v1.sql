-- Frozen synthetic archive fixture. Do not generate from migrations during tests.
PRAGMA foreign_keys=ON;
BEGIN IMMEDIATE;
CREATE TABLE owners (id TEXT PRIMARY KEY);
CREATE TABLE conversations (id TEXT PRIMARY KEY, owner TEXT NOT NULL REFERENCES owners(id), created_at TEXT NOT NULL, UNIQUE(owner, id));
CREATE TABLE active_conversations (owner TEXT PRIMARY KEY REFERENCES owners(id), conversation TEXT NOT NULL, FOREIGN KEY(owner, conversation) REFERENCES conversations(owner, id) ON DELETE CASCADE);
CREATE TABLE exchanges (sequence INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE, owner TEXT NOT NULL, conversation TEXT NOT NULL, created_at TEXT NOT NULL, origin TEXT NOT NULL CHECK(origin IN ('human','automatic')), user_text TEXT NOT NULL, assistant_text TEXT NOT NULL, tool_names TEXT NOT NULL, FOREIGN KEY(owner, conversation) REFERENCES conversations(owner, id) ON DELETE CASCADE);
CREATE INDEX conversation_exchanges ON exchanges(conversation, sequence);
INSERT INTO "owners" VALUES ('alice');
INSERT INTO "owners" VALUES ('bob');
INSERT INTO "conversations" VALUES ('old','alice','2025-01-02T12:34:56+00:00');
INSERT INTO "conversations" VALUES ('current','alice','2025-01-02T12:34:56+00:00');
INSERT INTO "conversations" VALUES ('dependent','alice','2025-01-02T12:34:56+00:00');
INSERT INTO "conversations" VALUES ('other','bob','2025-01-02T12:34:56+00:00');
INSERT INTO "active_conversations" VALUES ('alice','current');
INSERT INTO "active_conversations" VALUES ('bob','other');
INSERT INTO "exchanges" VALUES (1,'source-1','alice','old','2025-01-02T12:34:56+00:00','human','My greenhouse has rainwater storage. Original café 🌱
Second line.','Original reply.','["get_local_time"]');
INSERT INTO "exchanges" VALUES (2,'source-2','alice','current','2025-01-02T12:34:56+00:00','human','Continue the original greenhouse project.','Original reply.','["get_local_time"]');
INSERT INTO "exchanges" VALUES (3,'source-3','alice','dependent','2025-01-02T12:34:56+00:00','human','Copied source reference.','Original reply.','["get_local_time"]');
INSERT INTO "exchanges" VALUES (4,'source-4','bob','other','2025-01-02T12:34:56+00:00','human','Another owner’s unrelated conversation.','Original reply.','["get_local_time"]');
PRAGMA user_version=1;
COMMIT;
