"""Unified search (brief §35): Postgres full-text always; pgvector semantic when an embeddings
provider is configured. One index table, rebuilt from the owning modules; never a
source of truth."""
