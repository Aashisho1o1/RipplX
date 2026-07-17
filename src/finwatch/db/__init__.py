"""SQLite data layer: schema installer + the thin typed repository."""

from finwatch.db.database import SchemaVersionError, connect, init_db
from finwatch.db.repositories import (
    LOCAL_USER_ID,
    Analysis,
    Company,
    Computation,
    Digest,
    Filing,
    FilingSection,
    FilingStageRun,
    Repo,
    User,
    UserCompany,
    UserPreference,
    VerificationResult,
    XbrlFact,
)

__all__ = [
    "connect",
    "init_db",
    "SchemaVersionError",
    "Repo",
    "Analysis",
    "Company",
    "Computation",
    "Digest",
    "Filing",
    "FilingStageRun",
    "FilingSection",
    "LOCAL_USER_ID",
    "User",
    "UserCompany",
    "UserPreference",
    "VerificationResult",
    "XbrlFact",
]
