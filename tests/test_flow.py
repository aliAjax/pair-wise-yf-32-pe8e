import sys, tempfile, unittest
from datetime import timedelta
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import ApiError, OrganAllocationService, iso, utcnow
from seals import ARRIVAL, DEPARTURE, DUPLICATE, EXPIRED, MATCHED, MISMATCH


class OrganFlowTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.svc = OrganAllocationService(Path(self.tmp.name) / "test.db"); self.now = utcnow()

    def tearDown(self): self.tmp.cleanup()

    def donor(self, expires_days=2, hospital="H1"):
        return self.svc.register_donor("coord", "coordinator", {"blood_type": "O", "organ": "kidney", "hospital": hospital, "region": "East", "available_at": iso(self.now - timedelta(days=3)), "expires_at": iso(self.now + timedelta(days=expires_days)), "clinical_match": 8})

    def candidate(self, name="患者甲", hospital="H2", urgency=5, wait=500):
        return self.svc.register_candidate("coord", "coordinator", {"patient_name": name, "blood_type": "B", "organ": "kidney", "hospital": hospital, "region": "East", "urgency": urgency, "wait_days": wait, "willing": True, "clinical_match": 9})

    def flow_to_transit(self, expires_days=2, donor_hospital="H1", candidate_hospital="H2"):
        donor, candidate = self.donor(expires_days, donor_hospital), self.candidate(hospital=candidate_hospital)
        allocation = self.svc.propose("allocator", "allocation_officer", {"donor_id": donor["id"], "candidate_id": candidate["id"]})
        self.svc.accept(allocation["id"], "hospital-h2", "hospital", candidate_hospital, {"expected_revision": 1})
        transit = self.svc.mark_transit(allocation["id"], "allocator", "allocation_officer", {"cold_chain_temp": 3.5})
        return donor, candidate, transit

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
        self.assertTrue(seal.startswith("SEAL-"))
        handoff = self.svc.initiate_handoff(allocation["id"], "hospital-h1", "hospital", "H1", {"expected_revision": transit["revision"], "to_hospital": "H2", "cold_chain_temp": 3.0, "seal_code": seal})
        self.assertEqual(handoff["handoff"]["status"], "initiated")
        received = self.svc.accept_handoff(allocation["id"], "hospital-h2", "hospital", "H2", {"seal_code": seal})
        self.assertEqual(received["status"], "handed_off")
        # 双方上报的封签与时刻均已保存且核验一致
        self.assertEqual(received["seal"]["departure"]["reported_seal"], seal)
        self.assertEqual(received["seal"]["departure"]["result"], MATCHED)
        self.assertEqual(received["seal"]["arrival"]["reported_seal"], seal)
        self.assertEqual(received["seal"]["arrival"]["result"], MATCHED)
        self.assertTrue(received["seal"]["departure"]["created_at"])
        self.assertTrue(received["seal"]["arrival"]["created_at"])
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

    def test_departure_mismatch_blocks_handoff_and_stays_in_transit(self):
        _donor, _candidate, transit = self.flow_to_transit()
        aid = transit["id"]
        with self.assertRaises(ApiError) as ctx:
            self.svc.initiate_handoff(aid, "hospital-h1", "hospital", "H1",
                                      {"expected_revision": transit["revision"], "to_hospital": "H2", "cold_chain_temp": 3.0, "seal_code": "SEAL-WRONG"})
        self.assertEqual(ctx.exception.code, "seal_mismatch")
        allocation = self.svc.get_allocation(aid, "coordinator", "")
        self.assertEqual(allocation["status"], "in_transit")  # 继续停在转运中
        self.assertIsNone(allocation["handoff"])
        self.assertTrue(allocation["seal"]["discrepancy"])
        self.assertEqual(allocation["seal"]["departure"]["result"], MISMATCH)
        self.assertEqual(allocation["seal"]["departure"]["reported_seal"], "SEAL-WRONG")
        # 用正确封签重新核验后可以继续交接
        handoff = self.svc.initiate_handoff(aid, "hospital-h1", "hospital", "H1",
                                            {"expected_revision": transit["revision"], "to_hospital": "H2", "cold_chain_temp": 3.0, "seal_code": transit["seal_code"]})
        self.assertEqual(handoff["handoff"]["status"], "initiated")

    def test_arrival_mismatch_rejected_but_not_handed_off(self):
        _donor, _candidate, transit = self.flow_to_transit()
        aid = transit["id"]
        self.svc.initiate_handoff(aid, "hospital-h1", "hospital", "H1",
                                  {"expected_revision": transit["revision"], "to_hospital": "H2", "cold_chain_temp": 3.0, "seal_code": transit["seal_code"]})
        with self.assertRaises(ApiError) as ctx:
            self.svc.accept_handoff(aid, "hospital-h2", "hospital", "H2", {"seal_code": "SEAL-SWAPPED"})
        self.assertEqual(ctx.exception.code, "seal_mismatch")
        allocation = self.svc.get_allocation(aid, "coordinator", "")
        self.assertEqual(allocation["status"], "in_transit")
        self.assertEqual(allocation["handoff"]["status"], "initiated")  # 未记为已交接
        self.assertEqual(allocation["seal"]["arrival"]["result"], MISMATCH)
        # 重新核对同一封签后确认成功
        received = self.svc.accept_handoff(aid, "hospital-h2", "hospital", "H2", {"seal_code": transit["seal_code"]})
        self.assertEqual(received["status"], "handed_off")

    def test_duplicate_seal_rejected_even_though_present(self):
        _d, _c, transit1 = self.flow_to_transit()
        aid1 = transit1["id"]
        self.svc.initiate_handoff(aid1, "hospital-h1", "hospital", "H1",
                                  {"expected_revision": transit1["revision"], "to_hospital": "H2", "cold_chain_temp": 3.0, "seal_code": transit1["seal_code"]})
        self.svc.accept_handoff(aid1, "hospital-h2", "hospital", "H2", {"seal_code": transit1["seal_code"]})
        # 第二个分配复用同一封签码：重复优先于一致判定
        _d2, _c2, transit2 = self.flow_to_transit(donor_hospital="H1", candidate_hospital="H2")
        aid2 = transit2["id"]
        with self.assertRaises(ApiError) as ctx:
            self.svc.initiate_handoff(aid2, "hospital-h1", "hospital", "H1",
                                      {"expected_revision": transit2["revision"], "to_hospital": "H2", "cold_chain_temp": 3.0, "seal_code": transit1["seal_code"]})
        self.assertEqual(ctx.exception.code, "seal_duplicate")
        allocation = self.svc.get_allocation(aid2, "coordinator", "")
        self.assertEqual(allocation["status"], "in_transit")
        self.assertEqual(allocation["seal"]["departure"]["result"], DUPLICATE)

    def test_expired_verification_only_leaves_rejection_record(self):
        donor = self.donor(expires_days=2)
        candidate = self.candidate()
        allocation = self.svc.propose("allocator", "allocation_officer", {"donor_id": donor["id"], "candidate_id": candidate["id"]})
        self.svc.accept(allocation["id"], "hospital-h2", "hospital", "H2", {"expected_revision": 1})
        transit = self.svc.mark_transit(allocation["id"], "allocator", "allocation_officer", {"cold_chain_temp": 3.5})
        seal = transit["seal_code"]
        # 直接把器官窗口改到过去，模拟转运途中过期
        with self.svc.repo.tx() as conn:
            conn.execute("UPDATE donors SET expires_at=? WHERE id=?", (iso(self.now - timedelta(minutes=1)), donor["id"]))
        with self.assertRaises(ApiError) as ctx:
            self.svc.accept_handoff(allocation["id"], "hospital-h2", "hospital", "H2", {"seal_code": seal})
        self.assertEqual(ctx.exception.code, "organ_expired")
        allocation = self.svc.get_allocation(allocation["id"], "coordinator", "")
        self.assertEqual(allocation["status"], "expired")
        self.assertEqual(allocation["seal"]["arrival"]["result"], EXPIRED)  # 只留拒绝记录
        self.assertNotEqual(allocation["status"], "handed_off")
        self.assertIsNone(allocation["handoff"])

    def test_missing_seal_pending_until_supplemented(self):
        _donor, _candidate, transit = self.flow_to_transit()
        aid = transit["id"]
        with self.svc.repo.tx() as conn:
            conn.execute("UPDATE allocations SET seal_code=NULL WHERE id=?", (aid,))
        state = self.svc.state("coordinator", "")
        pending = [item for item in state["seal_board"]["pending"] if item["allocation_id"] == aid]
        self.assertEqual(len(pending), 1)
        with self.assertRaises(ApiError) as ctx:
            self.svc.initiate_handoff(aid, "hospital-h1", "hospital", "H1",
                                      {"expected_revision": transit["revision"], "to_hospital": "H2", "cold_chain_temp": 3.0, "seal_code": "SEAL-X"})
        self.assertEqual(ctx.exception.code, "seal_pending")
        with self.assertRaises(ApiError) as ctx:
            self.svc.supplement_seal(aid, "coord", "coordinator", {})
        self.assertEqual(ctx.exception.code, "seal_forbidden")
        supplemented = self.svc.supplement_seal(aid, "allocator", "allocation_officer", {})
        self.assertTrue(supplemented["seal"]["seal_code"].startswith("SEAL-"))
        handoff = self.svc.initiate_handoff(aid, "hospital-h1", "hospital", "H1",
                                            {"expected_revision": supplemented["revision"], "to_hospital": "H2", "cold_chain_temp": 3.0, "seal_code": supplemented["seal"]["seal_code"]})
        self.assertEqual(handoff["handoff"]["status"], "initiated")

    def test_handoff_requires_reported_seal(self):
        _donor, _candidate, transit = self.flow_to_transit()
        with self.assertRaises(ApiError) as ctx:
            self.svc.initiate_handoff(transit["id"], "hospital-h1", "hospital", "H1",
                                      {"expected_revision": transit["revision"], "to_hospital": "H2", "cold_chain_temp": 3.0})
        self.assertEqual(ctx.exception.status, 400)
        self.svc.initiate_handoff(transit["id"], "hospital-h1", "hospital", "H1",
                                  {"expected_revision": transit["revision"], "to_hospital": "H2", "cold_chain_temp": 3.0, "seal_code": transit["seal_code"]})
        with self.assertRaises(ApiError) as ctx:
            self.svc.accept_handoff(transit["id"], "hospital-h2", "hospital", "H2", {})
        self.assertEqual(ctx.exception.code, "seal_required")


if __name__ == "__main__": unittest.main()
