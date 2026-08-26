"""Job-read acquisition layer (ADR-015): strategies → artifacts → quality → extraction.

Pure by contract (import-linter): no database, no domain services, no AI providers. Persistent
artifacts are ``OpportunityRaw`` rows written by the sync layer, never from here.
"""
