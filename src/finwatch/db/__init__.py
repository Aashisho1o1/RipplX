"""SQLite data layer: schema, migrations, and the thin repository."""

from finwatch.db.database import MigrationError, apply_migrations, connect, init_db
from finwatch.db.repositories import (
    Analysis,
    AnalysisClaim,
    Company,
    Computation,
    Digest,
    Filing,
    FilingSection,
    FilingStageRun,
    Holding,
    Price,
    Repo,
    SignalShadowLog,
    VerificationResult,
    XbrlFact,
)

__all__ = [
    "apply_migrations",
    "connect",
    "init_db",
    "MigrationError",
    "Repo",
    "Analysis",
    "AnalysisClaim",
    "Company",
    "Computation",
    "Digest",
    "Filing",
    "FilingStageRun",
    "FilingSection",
    "Holding",
    "Price",
    "SignalShadowLog",
    "VerificationResult",
    "XbrlFact",
]
