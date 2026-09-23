# CMN-C2-213 — Unit tests: procurement domain service (deterministic rules)

from src.services.service import Service


class TestNormalizeExtract:
    def setup_method(self):
        self.svc = Service()

    def test_parses_amount_currency_and_vendor(self):
        out = self.svc.normalize_extract("Order 10 reams of A4 paper, approx 800 USD, vendor Acme Office Supplies")
        rec = out["request_record"]
        assert rec["amount"] == 800.0
        assert rec["currency"] == "USD"
        assert rec["vendor"] == "Acme Office Supplies"
        assert rec["item"] == "paper"
        assert out["missing_fields"] == []

    def test_parses_million_suffix_jpy(self):
        out = self.svc.normalize_extract("Renew software license, 8M JPY, vendor Globex Software")
        rec = out["request_record"]
        assert rec["amount"] == 8_000_000.0
        assert rec["currency"] == "JPY"

    def test_amount_prefers_currency_token_over_quantity(self):
        # "50" is a quantity; "$5,000" is the amount.
        out = self.svc.normalize_extract("Need 50 chairs for $5,000 from vendor Acme")
        assert out["request_record"]["amount"] == 5_000.0

    def test_missing_mandatory_fields_listed(self):
        out = self.svc.normalize_extract("can someone order the thing asap")
        assert out["request_record"]["amount"] is None
        assert out["request_record"]["vendor"] == ""
        assert set(out["missing_fields"]) == {"amount", "vendor"}

    def test_form_fields_override_free_text(self):
        out = self.svc.normalize_extract("free text", fields={"item": "laptop", "amount": 1200, "vendor": "Globex Software"})
        rec = out["request_record"]
        assert rec["item"] == "laptop"
        assert rec["amount"] == 1200
        assert rec["vendor"] == "Globex Software"
        assert out["missing_fields"] == []


class TestClassify:
    def setup_method(self):
        self.svc = Service()

    def test_office_supplies_normal_urgency(self):
        c = self.svc.classify({"item": "paper", "justification": "order paper and stationery supplies",
                               "amount": 800, "vendor": "Acme Office Supplies"})
        assert c["spend_category"] == "office_supplies"
        assert c["urgency_tier"] == "normal"
        assert c["compliance_risk"] == "low"
        assert c["confidence_overall"] == 0.65

    def test_high_urgency_detected(self):
        c = self.svc.classify({"item": "chair", "justification": "URGENT need chairs", "amount": 4500})
        assert c["urgency_tier"] == "high"
        assert c["compliance_risk"] == "low"  # 4500 < spot_max(5000)

    def test_medium_compliance_at_spot_band(self):
        c = self.svc.classify({"item": "chair", "justification": "need chairs", "amount": 6000})
        assert c["compliance_risk"] == "medium"  # 5000 <= 6000 < 50000

    def test_high_value_compliance_high(self):
        c = self.svc.classify({"item": "software", "justification": "enterprise software license", "amount": 8_000_000})
        assert c["spend_category"] == "it_software"
        assert c["compliance_risk"] == "high"

    def test_unknown_category_low_confidence(self):
        c = self.svc.classify({"item": "widget", "justification": "buy a widget", "amount": 100})
        assert c["spend_category"] == "other"
        assert c["confidence_overall"] == 0.4


class TestCoverage:
    def setup_method(self):
        self.svc = Service()

    def test_approved_supplier_with_active_contract(self):
        cov = self.svc.check_coverage({"vendor": "Globex Software", "amount": 8_000_000}, {"spend_category": "it_software"})
        assert cov["status"] == "approved"
        assert cov["matched_supplier"] == "SUP-002"
        assert cov["matched_contract"] == "C-2024-IT-001"

    def test_approved_supplier_no_contract_match(self):
        cov = self.svc.check_coverage({"vendor": "Acme Office Supplies", "amount": 800}, {"spend_category": "office_supplies"})
        assert cov["status"] == "approved"
        assert cov["matched_contract"] is None

    def test_off_contract_vendor(self):
        cov = self.svc.check_coverage({"vendor": "QuickFurnish Co", "amount": 4500}, {"spend_category": "office_supplies"})
        assert cov["status"] == "off_contract"
        assert cov["matched_supplier"] is None

    def test_unknown_when_vendor_missing(self):
        cov = self.svc.check_coverage({"vendor": "", "amount": 100}, {"spend_category": "other"})
        assert cov["status"] == "unknown"

    def test_contract_not_matched_when_over_ceiling(self):
        cov = self.svc.check_coverage({"vendor": "Globex Software", "amount": 99_000_000}, {"spend_category": "it_software"})
        assert cov["status"] == "approved"
        assert cov["matched_contract"] is None


class TestRouteExplain:
    def setup_method(self):
        self.svc = Service()

    def _classify(self, **over):
        base = {"spend_category": "office_supplies", "compliance_risk": "low", "confidence_overall": 0.8}
        base.update(over)
        return base

    def test_manual_review_on_missing_fields(self):
        d = self.svc.route_explain({"amount": 800}, self._classify(), {"status": "approved"}, ["vendor"])
        assert d["routing_decision"]["pathway"] == "manual_review"
        assert "missing_fields" in d["routing_decision"]["review_flags"]

    def test_manual_review_on_low_confidence(self):
        d = self.svc.route_explain({"amount": 800}, self._classify(confidence_overall=0.4), {"status": "approved"}, [])
        assert d["routing_decision"]["pathway"] == "manual_review"

    def test_contract_amendment_when_contract_matched(self):
        d = self.svc.route_explain(
            {"amount": 8_000_000}, self._classify(spend_category="it_software", compliance_risk="high"),
            {"status": "approved", "matched_contract": "C-2024-IT-001"}, [])
        assert d["routing_decision"]["pathway"] == "contract_amendment"
        assert "C-2024-IT-001" in d["routing_decision"]["rationale"]

    def test_catalog_order_for_small_approved_catalog_item(self):
        d = self.svc.route_explain({"amount": 800}, self._classify(), {"status": "approved", "matched_contract": None}, [])
        assert d["routing_decision"]["pathway"] == "catalog_order"

    def test_spot_buy_off_contract_flagged(self):
        d = self.svc.route_explain({"amount": 4500}, self._classify(), {"status": "off_contract", "matched_contract": None}, [])
        assert d["routing_decision"]["pathway"] == "spot_buy"
        assert "off_contract" in d["routing_decision"]["review_flags"]

    def test_rfq_for_high_value(self):
        d = self.svc.route_explain(
            {"amount": 200_000}, self._classify(spend_category="raw_materials", compliance_risk="high"),
            {"status": "off_contract", "matched_contract": None}, [])
        assert d["routing_decision"]["pathway"] == "rfq"
        assert "high_value" in d["routing_decision"]["review_flags"]
