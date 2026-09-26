import sys, tempfile, unittest
from datetime import timedelta
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import ApiError, OrganAllocationService, iso, utcnow


class OrganFlowTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.svc = OrganAllocationService(Path(self.tmp.name) / "test.db"); self.now = utcnow()

    def tearDown(self): self.tmp.cleanup()

    def donor(self, expires_days=2):
        return self.svc.register_donor("coord", "coordinator", {"blood_type": "O", "organ": "kidney", "hospital": "H1", "region": "East", "available_at": iso(self.now - timedelta(days=3)), "expires_at": iso(self.now + timedelta(days=expires_days)), "clinical_match": 8})

    def candidate(self, name="患者甲", hospital="H2", urgency=5, wait=500):
        return self.svc.register_candidate("coord", "coordinator", {"patient_name": name, "blood_type": "B", "organ": "kidney", "hospital": hospital, "region": "East", "urgency": urgency, "wait_days": wait, "willing": True, "clinical_match": 9})

    def in_transit(self):
        donor, candidate = self.donor(), self.candidate()
        allocation = self.svc.propose("allocator", "allocation_officer", {"donor_id": donor["id"], "candidate_id": candidate["id"]})
        self.svc.accept(allocation["id"], "hospital-h2", "hospital", "H2", {"expected_revision": 1})
        return self.svc.mark_transit(allocation["id"], "allocator", "allocation_officer", {"cold_chain_temp": 3.5})

    def initiate(self, allocation, seal):
        return self.svc.initiate_handoff(allocation["id"], "hospital-h1", "hospital", "H1", {"expected_revision": allocation["revision"], "to_hospital": "H2", "cold_chain_temp": 3.0, "seal_code": seal})

    def test_complete_allocation_and_cold_chain_flow(self):
        donor, candidate = self.donor(), self.candidate()
        rank = self.svc.ranking(donor["id"], "allocation_officer", "")
        self.assertEqual(rank["candidates"][0]["id"], candidate["id"])
        allocation = self.svc.propose("allocator", "allocation_officer", {"donor_id": donor["id"], "candidate_id": candidate["id"]})
        accepted = self.svc.accept(allocation["id"], "hospital-h2", "hospital", "H2", {"expected_revision": 1})
        self.assertEqual(accepted["status"], "accepted")
        transit = self.svc.mark_transit(allocation["id"], "allocator", "allocation_officer", {"cold_chain_temp": 3.5})
        self.assertEqual(transit["status"], "in_transit")
        seal = transit["seal_code"]
        self.assertTrue(seal)
        handoff = self.initiate(transit, seal)
        self.assertEqual(handoff["handoff"]["status"], "initiated")
        self.assertEqual(handoff["handoff"]["initiator_seal"], seal)
        received = self.svc.accept_handoff(allocation["id"], "hospital-h2", "hospital", "H2", {"seal_code": seal})
        self.assertEqual(received["status"], "handed_off")
        self.assertEqual(received["handoff"]["receiver_seal"], seal)
        implanted = self.svc.implant(allocation["id"], "allocator", "allocation_officer", {})
        self.assertEqual(implanted["status"], "implanted")
        audit = self.svc.audit(allocation["id"], "auditor")
        self.assertEqual([item["action"] for item in audit], ["allocation_proposed", "allocation_accepted", "transfer_started", "handoff_initiated", "handoff_accepted", "organ_implanted"])

    def test_expiry_privacy_and_single_allocation(self):
        expired = self.donor(expires_days=-1); candidate = self.candidate()
        with self.assertRaises(ApiError) as ctx:
            self.svc.propose("allocator", "allocation_officer", {"donor_id": expired["id"], "candidate_id": candidate["id"]})
        self.assertEqual(ctx.exception.code, "organ_expired")
        donor2 = self.donor(); allocation = self.svc.propose("allocator", "allocation_officer", {"donor_id": donor2["id"], "candidate_id": candidate["id"]})
        with self.assertRaises(ApiError) as ctx:
            self.svc.accept(allocation["id"], "wrong", "hospital", "H1", {"expected_revision": 1})
        self.assertEqual(ctx.exception.status, 403)
        masked = self.svc.get_allocation(allocation["id"], "hospital", "H1")
        self.assertEqual(masked["patient_name"], "***")
        with self.assertRaises(ApiError) as ctx:
            self.svc.propose("allocator", "allocation_officer", {"donor_id": donor2["id"], "candidate_id": candidate["id"]})
        self.assertEqual(ctx.exception.code, "donor_unavailable")
        other = self.candidate("患者乙", "H2", 4, 300)
        self.assertNotEqual(other["id"], candidate["id"])
        with self.assertRaises(ApiError) as ctx:
            self.svc.mark_transit(allocation["id"], "allocator", "allocation_officer", {"cold_chain_temp": 12})
        self.assertEqual(ctx.exception.code, "cold_chain_violation")

    def test_seal_mismatch_keeps_allocation_in_transit(self):
        transit = self.in_transit()
        with self.assertRaises(ApiError) as ctx:
            self.initiate(transit, "TAMPERED1")
        self.assertEqual(ctx.exception.code, "seal_mismatch")
        after = self.svc.get_allocation(transit["id"], "allocation_officer", "")
        self.assertEqual(after["status"], "in_transit")
        self.assertEqual(after["revision"], transit["revision"])
        self.assertIsNone(after["handoff"])
        event = after["seal_events"][-1]
        self.assertEqual((event["stage"], event["result"], event["reported_seal"]), ("initiate", "mismatch", "TAMPERED1"))
        self.assertTrue(event["created_at"])
        issues = self.svc.state("coordinator", "")["seal_issues"]
        self.assertEqual([(i["allocation_id"], i["issue"], i["reported_seal"]) for i in issues], [(transit["id"], "mismatch", "TAMPERED1")])
        self.assertIn("seal_rejected", [a["action"] for a in self.svc.audit(transit["id"], "auditor")])
        self.assertNotIn("seal_issues", self.svc.state("viewer", ""))

    def test_receiver_report_saved_and_retry_with_correct_seal(self):
        transit = self.in_transit(); seal = transit["seal_code"]
        self.initiate(transit, seal)
        with self.assertRaises(ApiError) as ctx:
            self.svc.accept_handoff(transit["id"], "hospital-h2", "hospital", "H2", {"seal_code": "SWAPPED99"})
        self.assertEqual(ctx.exception.code, "seal_mismatch")
        after = self.svc.get_allocation(transit["id"], "allocation_officer", "")
        self.assertEqual(after["status"], "in_transit")
        self.assertEqual(after["handoff"]["status"], "initiated")
        self.assertEqual(after["handoff"]["receiver_seal"], "SWAPPED99")
        self.assertTrue(after["handoff"]["receiver_reported_at"])
        received = self.svc.accept_handoff(transit["id"], "hospital-h2", "hospital", "H2", {"seal_code": seal})
        self.assertEqual(received["status"], "handed_off")
        self.assertEqual(received["handoff"]["receiver_seal"], seal)
        results = [e["result"] for e in received["seal_events"] if e["stage"] == "confirm"]
        self.assertEqual(results, ["mismatch", "matched"])

    def test_foreign_seal_rejected_as_duplicate(self):
        first, second = self.in_transit(), self.in_transit()
        self.assertNotEqual(first["seal_code"], second["seal_code"])
        self.initiate(first, first["seal_code"])
        with self.assertRaises(ApiError) as ctx:
            self.svc.accept_handoff(first["id"], "hospital-h2", "hospital", "H2", {"seal_code": second["seal_code"]})
        self.assertEqual(ctx.exception.code, "seal_duplicate")
        after = self.svc.get_allocation(first["id"], "allocation_officer", "")
        self.assertEqual(after["status"], "in_transit")
        self.assertEqual(after["seal_events"][-1]["result"], "duplicate")

    def test_expired_verification_only_leaves_rejection(self):
        transit = self.in_transit(); seal = transit["seal_code"]
        self.initiate(transit, seal)
        self.svc.repo.conn.execute("UPDATE donors SET expires_at=? WHERE id=?", (iso(self.now - timedelta(hours=1)), transit["donor_id"]))
        with self.assertRaises(ApiError) as ctx:
            self.svc.accept_handoff(transit["id"], "hospital-h2", "hospital", "H2", {"seal_code": seal})
        self.assertEqual(ctx.exception.code, "organ_expired")
        after = self.svc.get_allocation(transit["id"], "allocation_officer", "")
        self.assertEqual(after["status"], "expired")
        self.assertEqual(after["donor_status"], "expired")
        self.assertEqual(after["handoff"]["status"], "initiated")
        event = after["seal_events"][-1]
        self.assertEqual((event["stage"], event["result"], event["reported_seal"]), ("confirm", "expired", seal))
        self.assertIn("seal_rejected", [a["action"] for a in self.svc.audit(transit["id"], "auditor")])

    def test_missing_seal_pending_then_supplemented(self):
        transit = self.in_transit()
        self.svc.repo.conn.execute("UPDATE allocations SET seal_code=NULL,seal_issued_at=NULL WHERE id=?", (transit["id"],))
        issues = self.svc.state("coordinator", "")["seal_issues"]
        self.assertEqual([(i["allocation_id"], i["issue"]) for i in issues], [(transit["id"], "seal_pending")])
        with self.assertRaises(ApiError) as ctx:
            self.initiate(transit, "WHATEVER1")
        self.assertEqual(ctx.exception.code, "seal_missing")
        updated = self.svc.supplement_seal(transit["id"], "allocator", "allocation_officer", {})
        self.assertTrue(updated["seal_code"])
        self.assertEqual(self.svc.state("coordinator", "")["seal_issues"], [])
        handoff = self.initiate(updated, updated["seal_code"])
        self.assertEqual(handoff["handoff"]["status"], "initiated")
        with self.assertRaises(ApiError) as ctx:
            self.svc.supplement_seal(transit["id"], "allocator", "allocation_officer", {})
        self.assertEqual(ctx.exception.code, "seal_exists")

    def test_seal_required_on_both_sides(self):
        transit = self.in_transit()
        with self.assertRaises(ApiError) as ctx:
            self.svc.initiate_handoff(transit["id"], "hospital-h1", "hospital", "H1", {"expected_revision": transit["revision"], "to_hospital": "H2", "cold_chain_temp": 3.0})
        self.assertEqual(ctx.exception.code, "seal_required")
        self.initiate(transit, transit["seal_code"])
        with self.assertRaises(ApiError) as ctx:
            self.svc.accept_handoff(transit["id"], "hospital-h2", "hospital", "H2", {})
        self.assertEqual(ctx.exception.code, "seal_required")


if __name__ == "__main__": unittest.main()
