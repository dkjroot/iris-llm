# Frozen memory archives

These SQL files describe supported archive versions 1–9. They contain synthetic
sources and owners only. Each file includes the historical table definitions and
seed rows for the state available at that version, including FTS triggers.

Load a fixture with `sqlite3.Connection.executescript` into a disposable database.
Tests must not regenerate it from the current migration code: that would make a
broken migration define its own expected input. Add a new frozen fixture when
adding a schema version, and retain the older ones.

The corpus has Alice's old, current and dependent conversations and Bob's separate
conversation. Later versions add vectors, private policy, an explicit detail,
provenance, a grounded fact/topic and maintenance failures. The accented text,
emoji and newline in the oldest source must survive every upgrade unchanged.
Schema-specific corrections and supersession interactions also have dedicated
regressions in the hierarchy and persistence tests.
