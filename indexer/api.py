"""Read-only FastAPI for the x402 trust layer indexer, exposing providers and audit evidence."""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field

app = FastAPI(
    title="x402 Trust Layer API",
    description="Read-only API for x402 provider registry and audit evidence",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

INDEXER_DIR = Path(__file__).parent
DB_PATH = INDEXER_DIR / "indexer.db"

# Ensure database is initialized
try:
    import database as db
    if not DB_PATH.exists():
        db.init_database()
except Exception as exc:
    import traceback
    print("DB init failed: " + str(exc))
    traceback.print_exc()
    db = None


class HealthResponse(BaseModel):
    status: str
    timestamp: str
    providers_count: int
    evidence_count: int


class ProviderResponse(BaseModel):
    provider_id: str
    label: str
    facilitator_base_url: str
    supported_url: str | None
    verify_url: str | None
    settle_url: str | None
    known_declaration_url: str | None
    networks: list[str]
    last_seen: str | None
    status: str


class EvidenceRecordResponse(BaseModel):
    schema_version: int = 1
    stored_at: str
    evidence_key: dict
    evidence_digest: str
    summary: dict
    chain_id: str | None = None
    provider_id: str | None = None
    audit_verdict: str | None = None
    evidence_source: str | None = None
    evidence_store_status: str | None = None
    transaction_hash: str | None = None


def _load_providers() -> list:
    """Load providers from database, falling back to JSON if needed."""
    if db is not None:
        try:
            return db.get_all_providers()
        except Exception:
            pass
    # Fallback to JSON
    providers_path = INDEXER_DIR / "providers.json"
    if not providers_path.exists():
        return []
    try:
        with open(providers_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _load_evidence() -> list:
    """Load evidence from database, falling back to JSON if needed."""
    if db is not None:
        try:
            # For compatibility, load all evidence
            return db.search_evidence(limit=10000)
        except Exception:
            pass
    # Fallback to JSON
    evidence_path = INDEXER_DIR / "evidence.json"
    if not evidence_path.exists():
        return []
    try:
        with open(evidence_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


@app.get("/health", response_model=HealthResponse)
def get_health():
    providers = _load_providers()
    evidence = _load_evidence()
    return HealthResponse(
        status="ok",
        timestamp=datetime.now(timezone.utc).isoformat(),
        providers_count=len(providers),
        evidence_count=len(evidence),
    )


@app.get("/facilitators", response_model=list[ProviderResponse])
def list_facilitators():
    providers = _load_providers()
    return providers


@app.get("/facilitators/{provider_id}", response_model=ProviderResponse)
def get_facilitator(provider_id: str):
    if db is not None:
        try:
            provider = db.get_provider(provider_id)
            if provider:
                return provider
        except Exception:
            pass
    # Fallback to JSON
    providers = _load_providers()
    for provider in providers:
        if provider.get("provider_id") == provider_id:
            return provider
    raise HTTPException(status_code=404, detail="provider not found")


@app.get("/evidence", response_model=list[EvidenceRecordResponse])
def list_evidence(
    chain_id: str | None = None,
    provider_id: str | None = None,
    verdict: str | None = None,
    evidence_source: str | None = None,
    status: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    limit: int = 100,
    offset: int = 0,
):
    if db is not None:
        try:
            results = db.search_evidence(
                chain_id=chain_id,
                provider_id=provider_id,
                audit_verdict=verdict,
                evidence_source=evidence_source,
                evidence_store_status=status,
                from_date=from_date,
                to_date=to_date,
                limit=limit,
                offset=offset,
            )
            return results
        except Exception:
            pass
    # Fallback to JSON
    evidence = _load_evidence()
    results = []
    for record in evidence:
        if chain_id is not None:
            key = record.get("evidenceKey", {})
            if key.get("chainId") != chain_id:
                continue
        if provider_id is not None:
            summary = record.get("summary", {})
            if summary.get("providerId") != provider_id:
                continue
        if verdict is not None:
            summary = record.get("summary", {})
            if summary.get("auditVerdict") != verdict:
                continue
        if evidence_source is not None:
            summary = record.get("summary", {})
            if summary.get("evidenceSource") != evidence_source:
                continue
        if status is not None:
            if record.get("evidenceStoreStatus") != status:
                continue
        if from_date is not None:
            stored_at = record.get("storedAt", "")
            if stored_at < from_date:
                continue
        if to_date is not None:
            stored_at = record.get("storedAt", "")
            if stored_at > to_date:
                continue
        results.append(record)
        if len(results) >= limit:
            break
    return results


@app.get("/evidence/{chain_id}/{transaction_hash}", response_model=list[EvidenceRecordResponse])
def get_evidence(chain_id: str, transaction_hash: str):
    if db is not None:
        try:
            results = db.find_evidence_by_key(chain_id, transaction_hash)
            if results:
                return results
        except Exception:
            pass
    # Fallback to JSON
    evidence = _load_evidence()
    results = []
    for record in evidence:
        key = record.get("evidenceKey", {})
        if key.get("chainId") == chain_id and key.get("transactionHash") == transaction_hash:
            results.append(record)
    if not results:
        raise HTTPException(status_code=404, detail="no evidence found for this transaction")
    return results


@app.post("/ingest/providers")
def ingest_providers(payload: dict):
    """Accept provider records from the local indexer."""
    providers = payload.get("providers", [])
    if db is None:
        raise HTTPException(status_code=503, detail="database not available")
    inserted = 0
    for provider in providers:
        try:
            db.upsert_provider(provider)
            inserted += 1
        except Exception as exc:
            print("Failed to insert provider: " + str(exc))
    return {"ingested": inserted}


@app.post("/ingest/evidence")
def ingest_evidence(payload: dict):
    """Accept evidence records from the local indexer."""
    records = payload.get("records", [])
    if db is None:
        raise HTTPException(status_code=503, detail="database not available")
    inserted = 0
    for record in records:
        try:
            db.insert_evidence(record)
            inserted += 1
        except Exception as exc:
            print("Failed to insert evidence: " + str(exc))
    return {"ingested": inserted}


@app.get("/stats")
def get_stats():
    if db is not None:
        try:
            return db.get_stats()
        except Exception:
            pass
    # Fallback to JSON
    providers = _load_providers()
    evidence = _load_evidence()
    
    status_counts = {}
    for p in providers:
        status = p.get("status", "UNKNOWN")
        status_counts[status] = status_counts.get(status, 0) + 1
    
    verdict_counts = {}
    source_counts = {}
    chain_counts = {}
    store_status_counts = {}
    
    for e in evidence:
        summary = e.get("summary", {})
        verdict = summary.get("auditVerdict", "UNKNOWN")
        verdict_counts[verdict] = verdict_counts.get(verdict, 0) + 1
        
        source = summary.get("evidenceSource", "UNKNOWN")
        source_counts[source] = source_counts.get(source, 0) + 1
        
        key = e.get("evidenceKey", {})
        chain = key.get("chainId", "UNKNOWN")
        chain_counts[chain] = chain_counts.get(chain, 0) + 1
        
        store_status = e.get("evidenceStoreStatus", "UNKNOWN")
        store_status_counts[store_status] = store_status_counts.get(store_status, 0) + 1
    
    return {
        "providers": {
            "total": len(providers),
            "by_status": status_counts,
        },
        "evidence": {
            "total": len(evidence),
            "by_verdict": verdict_counts,
            "by_source": source_counts,
            "by_chain": chain_counts,
            "by_store_status": store_status_counts,
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
