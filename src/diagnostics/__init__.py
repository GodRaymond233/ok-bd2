"""Privacy-conscious local diagnostics for user-submitted problem reports."""

from src.diagnostics.models import DiagnosticSnapshot, ReportResult
from src.diagnostics.service import DiagnosticsManager

__all__ = ["DiagnosticSnapshot", "DiagnosticsManager", "ReportResult"]
