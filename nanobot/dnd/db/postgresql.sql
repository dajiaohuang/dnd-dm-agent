CREATE EXTENSION IF NOT EXISTS vector;
-- SQLAlchemy creates the portable MVP tables. These pgvector columns enable semantic search in PostgreSQL.
ALTER TABLE IF EXISTS rule_chunks ADD COLUMN IF NOT EXISTS embedding_vector vector(1024);
CREATE INDEX IF NOT EXISTS idx_rule_chunks_embedding_vector ON rule_chunks
USING ivfflat (embedding_vector vector_cosine_ops) WITH (lists = 100);
