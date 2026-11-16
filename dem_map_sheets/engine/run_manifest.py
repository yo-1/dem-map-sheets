"""run.json schema metadata. Independent of settings-profile schema."""
SCHEMA_VERSION = 1
QA_NOT_EVALUATED = "not_evaluated"
QA_STATUSES = frozenset(("not_evaluated", "pass", "fail", "review_required"))

def ensure_manifest_schema(record):
    """Add schema metadata without changing processing status or QA results."""
    record.setdefault("schema_version", SCHEMA_VERSION)
    record.setdefault("qa_status", QA_NOT_EVALUATED)
    return record

def read_manifest_schema(record):
    """Validate a run record in memory; never rewrite historical files."""
    if not isinstance(record, dict):
        raise ValueError("run.json must be a JSON object")
    result = dict(record)
    version = result.get("schema_version", SCHEMA_VERSION)
    if type(version) is not int or version != SCHEMA_VERSION:
        raise ValueError("Unsupported run.json schema_version: %r" % version)
    qa = result.get("qa_status", QA_NOT_EVALUATED)
    if qa not in QA_STATUSES:
        raise ValueError("Unsupported run.json qa_status: %r" % qa)
    return ensure_manifest_schema(result)
