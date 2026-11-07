"""Background job queue worker (SYSTEM_ARCHITECTURE §6).

Jobs live in SQLite; the worker claims, processes, and retries them.
No Redis/Celery/broker required (D5).
"""
