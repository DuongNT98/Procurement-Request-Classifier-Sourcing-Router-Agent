"""AgentCore Platform v1.0 — CMN-C2-213 procurement domain service.

Service layer: deterministic, config-driven procurement logic. No business
routing decisions about agent control flow, no credentials, no agenticstar
imports. Nodes call this; this owns the domain rules.

The defaults below are the authoritative offline source of truth (tests run
against them). The shipped ``config/*.yaml`` and ``data/*.csv`` files mirror
these defaults and are the citizen-editable override surface — a deployer
swaps them per industry and loads them via :meth:`Service.load_overrides`.
One CMN build -> many industry variants by editing config only.

LLM seam (design §6): :meth:`classify` is deterministic by default. It is the
single method to swap for a ``shared.services.llm`` call when higher-recall
reasoning over ambiguous free text is needed — the node contract and state
shape are unaffected.
"""

from __future__ import annotations

import csv
import os
import re
from typing import Any

# ── Default taxonomy (mirrors config/taxonomy.yaml) ──────────────────────────
# Ordered: first category whose keywords match the request text wins.
DEFAULT_SPEND_CATEGORIES: list[dict[str, Any]] = [
    {
        "category": "office_supplies",
        "catalog_eligible": True,
        "keywords": [
            "chair",
            "desk",
            "paper",
            "pen",
            "stationery",
            "supplies",
            "printer",
            "toner",
            "furniture",
            "notebook",
        ],
    },
    {
        "category": "it_software",
        "catalog_eligible": False,
        "keywords": [
            "software",
            "license",
            "licence",
            "saas",
            "subscription",
            "laptop",
            "server",
            "cloud",
            "api",
            "renewal",
        ],
    },
    {
        "category": "professional_services",
        "catalog_eligible": False,
        "keywords": ["consulting", "consultant", "audit", "legal", "contractor", "training", "advisory"],
    },
    {
        "category": "facilities",
        "catalog_eligible": False,
        "keywords": ["cleaning", "maintenance", "hvac", "repair", "facility", "lease", "rent"],
    },
    {
        "category": "logistics",
        "catalog_eligible": False,
        "keywords": ["shipping", "freight", "courier", "transport", "warehouse", "delivery"],
    },
    {
        "category": "raw_materials",
        "catalog_eligible": False,
        "keywords": ["steel", "plastic", "component", "material", "parts", "resin"],
    },
]
FALLBACK_CATEGORY = "other"

URGENCY_HIGH = ["urgent", "asap", "immediately", "critical", "emergency", "rush", "today"]
URGENCY_LOW = ["whenever", "no rush", "low priority", "eventually", "someday"]

# ── Default pathway rules / thresholds (mirrors config/pathway_rules.yaml) ────
DEFAULT_THRESHOLDS: dict[str, float] = {
    "catalog_max": 1_000.0,  # at/under -> catalog eligible
    "spot_max": 5_000.0,  # at/under -> spot buy eligible
    "rfq_min": 50_000.0,  # at/over  -> RFQ required
    "low_confidence": 0.6,  # under -> manual review
}

MANDATORY_FIELDS = ("item", "amount", "vendor")

# ── Default supplier / contract data (mirrors data/*.csv) ─────────────────────
DEFAULT_SUPPLIERS: dict[str, dict[str, Any]] = {
    "acme office supplies": {"id": "SUP-001", "categories": ["office_supplies"], "status": "active"},
    "globex software": {"id": "SUP-002", "categories": ["it_software"], "status": "active"},
    "initech consulting": {"id": "SUP-003", "categories": ["professional_services"], "status": "active"},
}
DEFAULT_CONTRACTS: list[dict[str, Any]] = [
    {
        "vendor": "globex software",
        "category_scope": "it_software",
        "amount_ceiling": 10_000_000.0,
        "ref": "C-2024-IT-001",
        "expired": False,
    },
    {
        "vendor": "initech consulting",
        "category_scope": "professional_services",
        "amount_ceiling": 5_000_000.0,
        "ref": "C-2025-PS-014",
        "expired": False,
    },
]

_AMOUNT_RE = re.compile(r"(?:([¥$€])\s?)?(\d[\d,]*(?:\.\d+)?)\s*([kKmM])?\s*(USD|JPY|EUR)?", re.IGNORECASE)
_CUE_WORDS = ("approx", "approximately", "about", "cost", "amount", "price", "budget", "total", "~")
_VENDOR_RE = re.compile(r"(?:vendor|supplier|from)[:\s]+([A-Za-z0-9][\w&.\- ]{1,48})", re.IGNORECASE)
MAX_REQUEST_CHARS = 8_000


class Service:
    """Deterministic procurement classification, coverage, and routing rules."""

    def __init__(
        self,
        categories: list[dict[str, Any]] | None = None,
        thresholds: dict[str, float] | None = None,
        suppliers: dict[str, dict[str, Any]] | None = None,
        contracts: list[dict[str, Any]] | None = None,
    ) -> None:
        self.categories = categories if categories is not None else DEFAULT_SPEND_CATEGORIES
        self.thresholds = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
        self.suppliers = suppliers if suppliers is not None else DEFAULT_SUPPLIERS
        self.contracts = contracts if contracts is not None else DEFAULT_CONTRACTS

    # ── citizen-editable override loading (best-effort; defaults otherwise) ──
    def load_overrides(self, data_dir: str) -> "Service":
        """Load supplier/contract overrides from CSVs in *data_dir* if present.

        Uses the stdlib ``csv`` module only — no third-party dependency. Missing
        files are ignored (defaults stand). Returns self for chaining.
        """
        sup_path = os.path.join(data_dir, "approved_suppliers.csv")
        con_path = os.path.join(data_dir, "contract_coverage.csv")
        if os.path.isfile(sup_path):
            suppliers: dict[str, dict[str, Any]] = {}
            with open(sup_path, encoding="utf-8", newline="") as fh:
                for row in csv.DictReader(fh):
                    suppliers[row["name"].strip().lower()] = {
                        "id": row.get("id", "").strip(),
                        "categories": [c.strip() for c in row.get("categories", "").split("|") if c.strip()],
                        "status": row.get("status", "active").strip(),
                    }
            self.suppliers = suppliers
        if os.path.isfile(con_path):
            contracts: list[dict[str, Any]] = []
            with open(con_path, encoding="utf-8", newline="") as fh:
                for row in csv.DictReader(fh):
                    contracts.append(
                        {
                            "vendor": row["vendor"].strip().lower(),
                            "category_scope": row.get("category_scope", "").strip(),
                            "amount_ceiling": float(row.get("amount_ceiling", "0") or 0),
                            "ref": row.get("ref", "").strip(),
                            "expired": str(row.get("expired", "false")).strip().lower() in ("true", "1", "yes"),
                        }
                    )
            self.contracts = contracts
        return self

    # ── NormalizeExtractNode ────────────────────────────────────────────────
    def normalize_extract(self, raw_text: str, fields: dict[str, Any] | None = None) -> dict[str, Any]:
        """Parse free text + pre-structured form fields into a canonical record.

        *fields* (from a web form / ERP webhook) are authoritative; free text
        fills any gaps. Returns ``{"request_record": {...}, "missing_fields": [...]}``.
        """
        fields = fields or {}
        text = (raw_text or "").strip()
        amount, currency = self._parse_amount(text)
        record: dict[str, Any] = {
            "item": fields.get("item") or self._parse_item(text),
            "amount": fields.get("amount") if fields.get("amount") is not None else amount,
            "currency": fields.get("currency") or currency,
            "vendor": fields.get("vendor") or self._parse_vendor(text),
            "requester": fields.get("requester", ""),
            "need_by": fields.get("need_by", ""),
            "justification": fields.get("justification") or text,
        }
        missing = [f for f in MANDATORY_FIELDS if not record.get(f)]
        return {"request_record": record, "missing_fields": missing}

    @staticmethod
    def _parse_amount(text: str) -> tuple[float | None, str]:
        best_value: float | None = None
        best_score = -1
        best_currency = ""
        for m in _AMOUNT_RE.finditer(text):
            sym, num_s, suffix, code = m.groups()
            try:
                value = float(num_s.replace(",", ""))
            except ValueError:
                continue
            suf = (suffix or "").lower()
            if suf == "k":
                value *= 1_000
            elif suf == "m":
                value *= 1_000_000
            score = 0
            currency = ""
            if sym or code:
                score = 2
                currency = {"¥": "JPY", "$": "USD", "€": "EUR"}.get(sym or "", (code or "").upper())
            else:
                prefix = text[max(0, m.start() - 14) : m.start()].lower()
                if any(cue in prefix for cue in _CUE_WORDS):
                    score = 1
            # Prefer higher score; tie-break on larger magnitude (amounts > quantities).
            if score > best_score or (score == best_score and value > (best_value or 0)):
                best_score, best_value, best_currency = score, value, currency
        if not best_currency:
            if "¥" in text or re.search(r"\bJPY\b", text, re.IGNORECASE):
                best_currency = "JPY"
            elif "$" in text or re.search(r"\bUSD\b", text, re.IGNORECASE):
                best_currency = "USD"
            elif "€" in text or re.search(r"\bEUR\b", text, re.IGNORECASE):
                best_currency = "EUR"
        return best_value, best_currency

    @staticmethod
    def _parse_vendor(text: str) -> str:
        m = _VENDOR_RE.search(text)
        if not m:
            return ""
        # Trim trailing prose after a comma / sentence boundary.
        return re.split(r"[,.;]", m.group(1).strip())[0].strip()

    _ITEM_FILLERS = frozenset(
        list(URGENCY_HIGH)
        + list(URGENCY_LOW)
        + [
            "need",
            "order",
            "please",
            "want",
            "require",
            "for",
            "a",
            "an",
            "the",
            "some",
            "to",
            "of",
            "can",
            "someone",
            "soon",
            "get",
            "buy",
            "purchase",
            "new",
            "boxes",
            "box",
            "reams",
            "ream",
            "units",
            "unit",
            "pcs",
        ]
    )

    def _parse_item(self, text: str) -> str:
        # Prefer a taxonomy keyword (reliable category anchor); else fall back to
        # the significant words of the first clause, with quantity/amount/vendor/
        # urgency/filler tokens stripped, so free-text items still register as present.
        low = text.lower()
        for cat in self.categories:
            keywords: list[str] = cat["keywords"]
            for kw in keywords:
                if kw in low:
                    return kw
        stripped = _AMOUNT_RE.sub(" ", _VENDOR_RE.sub(" ", text))
        first_clause = re.split(r"[,.;]", stripped)[0]
        words = [w for w in re.findall(r"[A-Za-z]+", first_clause) if w.lower() not in self._ITEM_FILLERS]
        return " ".join(words[:4]).strip()

    # ── ClassifyNode (deterministic; LLM seam) ──────────────────────────────
    def classify(self, record: dict[str, Any]) -> dict[str, Any]:
        """Assign spend category, urgency tier, compliance risk + confidence.

        Deterministic over the configured taxonomy. Swap this method for an
        LLM-backed classifier (design §6) without changing the node contract.
        """
        text = f"{record.get('item', '')} {record.get('justification', '')}".lower()
        category, cat_conf = self._classify_category(text)
        urgency, urg_conf = self._classify_urgency(text)
        amount = record.get("amount") or 0.0
        compliance = self._base_compliance(amount)

        conf = {"spend_category": cat_conf, "urgency_tier": urg_conf, "compliance_risk": 0.8}
        overall = round(min(conf.values()), 3)
        # Missing mandatory data caps confidence so routing sends it to review.
        if any(not record.get(f) for f in MANDATORY_FIELDS):
            overall = min(overall, 0.4)
        return {
            "spend_category": category,
            "urgency_tier": urgency,
            "compliance_risk": compliance,
            "confidence": conf,
            "confidence_overall": overall,
        }

    def _classify_category(self, text: str) -> tuple[str, float]:
        for cat in self.categories:
            matches = sum(1 for kw in cat["keywords"] if kw in text)
            if matches >= 1:
                return cat["category"], 0.95 if matches >= 2 else 0.75
        return FALLBACK_CATEGORY, 0.4

    @staticmethod
    def _classify_urgency(text: str) -> tuple[str, float]:
        if any(kw in text for kw in URGENCY_HIGH):
            return "high", 0.9
        if any(kw in text for kw in URGENCY_LOW):
            return "low", 0.85
        return "normal", 0.65

    def _base_compliance(self, amount: float) -> str:
        if amount >= self.thresholds["rfq_min"]:
            return "high"
        if amount >= self.thresholds["spot_max"]:
            return "medium"
        return "low"

    # ── CoverageCheckNode ───────────────────────────────────────────────────
    def check_coverage(self, record: dict[str, Any], classification: dict[str, Any]) -> dict[str, Any]:
        """Match vendor against approved suppliers and an active contract."""
        vendor = (record.get("vendor") or "").strip().lower()
        if not vendor:
            return {"status": "unknown", "matched_supplier": None, "matched_contract": None}

        supplier = self.suppliers.get(vendor)
        amount = record.get("amount") or 0.0
        category = classification.get("spend_category", FALLBACK_CATEGORY)
        matched_contract = None
        for con in self.contracts:
            if (
                con["vendor"] == vendor
                and not con.get("expired")
                and con["category_scope"] == category
                and amount <= con["amount_ceiling"]
            ):
                matched_contract = con["ref"]
                break

        if supplier and supplier.get("status") == "active":
            return {"status": "approved", "matched_supplier": supplier["id"], "matched_contract": matched_contract}
        return {"status": "off_contract", "matched_supplier": None, "matched_contract": matched_contract}

    # ── RouteExplainNode ─────────────────────────────────────────────────────
    def route_explain(
        self,
        record: dict[str, Any],
        classification: dict[str, Any],
        coverage: dict[str, Any],
        missing_fields: list[str],
    ) -> dict[str, Any]:
        """Apply pathway rules -> pathway + rationale + confidence + review flags."""
        amount = record.get("amount") or 0.0
        category = classification.get("spend_category", FALLBACK_CATEGORY)
        confidence = classification.get("confidence_overall", 0.0)
        cov_status = coverage.get("status", "unknown")
        matched_contract = coverage.get("matched_contract")
        catalog_eligible = self._is_catalog_eligible(category)
        review_flags: list[str] = []

        if missing_fields:
            review_flags.append("missing_fields")
        if cov_status == "off_contract":
            review_flags.append("off_contract")
        if classification.get("compliance_risk") == "high":
            review_flags.append("compliance_high")
        if amount >= self.thresholds["rfq_min"]:
            review_flags.append("high_value")

        # Rule order (most specific first).
        if missing_fields or confidence < self.thresholds["low_confidence"]:
            pathway = "manual_review"
            reason = "Incomplete request or low classification confidence; sent to buyer review queue."
        elif matched_contract:
            pathway = "contract_amendment"
            reason = f"Active contract {matched_contract} covers this {category} spend; routed to amendment."
        elif cov_status == "approved" and catalog_eligible and amount <= self.thresholds["catalog_max"]:
            pathway = "catalog_order"
            reason = "Approved supplier, catalog-eligible category, under catalog threshold; auto-routed."
        elif amount >= self.thresholds["rfq_min"]:
            pathway = "rfq"
            reason = "High-value spend at or above the RFQ threshold; competitive RFQ required."
        elif amount <= self.thresholds["spot_max"]:
            pathway = "spot_buy"
            reason = "Under the spot-buy threshold; routed to spot buy."
            if cov_status == "off_contract":
                reason += " Vendor is off-contract; flagged for compliance review."
        else:
            pathway = "rfq"
            reason = "Mid-band spend with no catalog/contract match; routed to RFQ."

        return {
            "routing_decision": {
                "pathway": pathway,
                "rationale": reason,
                "confidence": confidence,
                "review_flags": review_flags,
            }
        }

    def _is_catalog_eligible(self, category: str) -> bool:
        return any(c["category"] == category and c.get("catalog_eligible") for c in self.categories)
