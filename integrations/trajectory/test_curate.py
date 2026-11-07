import json
from pathlib import Path
import tempfile
import unittest

from integrations.trajectory.curate import inventory, prepare, review, export, load_object, observable_hash
from integrations.trajectory.test_export_atif import fixture


class CurationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.result, self.transcript = fixture()
        self.trial = self.root / "trial"
        (self.trial / "results").mkdir(parents=True)
        self.result_path = self.trial / "result.json"
        self.transcript_path = self.trial / "results/transcript.json"
        self.result_path.write_text(json.dumps(self.result))
        self.transcript_path.write_text(json.dumps(self.transcript))
        self.manifest = self.root / "curation.json"
        self.data = inventory([self.trial], self.manifest)
        self.id = self.data["records"][0]["id"]
        self.copy = self.root / "review.json"
        self.splits = {"train":["proxy-routing"], "validation":[], "test":[]}

    def approve(self, **updates):
        if not self.copy.exists(): prepare(load_object(self.manifest),self.id,self.copy)
        values = dict(family="proxy-routing",reviewer="fixture-reviewer",quality="accepted",privacy="approved",rights="approved",evidence="Fixture-only permission, not a real provider authorization",copy=self.copy)
        values.update(updates)
        review(self.manifest,self.id,**values)

    def test_unreviewed_and_unknown_rights_never_export(self):
        self.assertTrue(self.data["records"][0]["verified_success"])
        with self.assertRaises(ValueError): export(self.data,self.splits,self.root/"out","v1")
        self.approve(rights="unknown")
        with self.assertRaises(ValueError): export(load_object(self.manifest),self.splits,self.root/"out","v1")
        self.approve(privacy="unreviewed")
        with self.assertRaises(ValueError): export(load_object(self.manifest),self.splits,self.root/"out","v1")

    def test_private_reasoning_excluded_and_export_has_provenance(self):
        self.approve()
        self.assertNotIn("private reasoning must not export",self.copy.read_text())
        output = self.root / "dataset"
        result = export(load_object(self.manifest),self.splits,output,"v1")
        self.assertEqual(result["splits"],{"train":1,"validation":0,"test":0})
        self.assertEqual(result["provenance"][0]["source_sha256"],self.data["records"][0]["source_sha256"])
        self.assertNotIn(str(self.root), (output/"manifest.json").read_text())
        with self.assertRaises(ValueError): export(load_object(self.manifest),self.splits,output,"v1")

    def test_redacted_review_copy_is_the_only_exported_content(self):
        prepare(load_object(self.manifest),self.id,self.copy)
        trajectory = load_object(self.copy)
        trajectory["steps"][1]["observation"]["results"][0]["content"] = "[REDACTED]"
        self.copy.write_text(json.dumps(trajectory))
        self.approve()
        export(load_object(self.manifest),self.splits,self.root/"out","v1")
        text = (self.root/"out/train.jsonl").read_text()
        self.assertIn("[REDACTED]",text)
        self.assertNotIn('inactive\\n',text)

    def test_stale_source_and_modified_review_revoke_approval(self):
        self.approve()
        self.transcript_path.write_text(self.transcript_path.read_text()+" ")
        with self.assertRaises(ValueError): export(load_object(self.manifest),self.splits,self.root/"out","v1")
        self.transcript_path.write_text(json.dumps(self.transcript))
        self.copy.write_text(self.copy.read_text()+" ")
        with self.assertRaises(ValueError): export(load_object(self.manifest),self.splits,self.root/"out","v1")
        self.assertFalse((self.root/"out").exists())

    def test_failed_lifecycle_and_forged_identity_cannot_be_approved(self):
        prepare(load_object(self.manifest),self.id,self.copy)
        trajectory = load_object(self.copy);trajectory["agent"]["model_name"]="different/model"
        self.copy.write_text(json.dumps(trajectory))
        with self.assertRaises(ValueError): self.approve()
        manifest = load_object(self.manifest);manifest["records"][0]["verified_success"]=False
        self.manifest.write_text(json.dumps(manifest))
        self.copy.unlink();prepare(manifest,self.id,self.copy)
        with self.assertRaises(ValueError): self.approve()

    def test_splits_are_explicit_and_family_disjoint(self):
        self.approve()
        for splits in ({"train":["proxy-routing"],"test":["proxy-routing"],"validation":[]}, {"train":[],"test":[],"validation":[]}):
            with self.assertRaises(ValueError): export(load_object(self.manifest),splits,self.root/"out","v1")
        self.assertFalse((self.root/"out").exists())

    def test_unsupported_transcripts_are_reported_and_inventory_is_immutable(self):
        self.transcript_path.write_text('[]')
        other = inventory([self.trial],self.root/"other.json")
        self.assertEqual(len(other["unsupported"]),1)
        self.assertFalse(other["records"])
        with self.assertRaises(ValueError): inventory([self.trial],self.manifest)

    def test_forged_inventory_success_does_not_override_source_verifier(self):
        self.result["reward"] = 0
        self.result["verification"]["host_reboot"] = False
        self.result_path.write_text(json.dumps(self.result))
        manifest = self.root/"failed.json"
        data = inventory([self.trial],manifest)
        data["records"][0]["verified_success"] = True
        manifest.write_text(json.dumps(data))
        identifier = data["records"][0]["id"]
        prepare(data,identifier,self.copy)
        with self.assertRaises(ValueError):
            review(manifest,identifier,family="proxy-routing",reviewer="test",quality="accepted",privacy="approved",rights="approved",evidence="test permission",copy=self.copy)

    def test_versions_of_a_scenario_cannot_leak_across_splits(self):
        other = self.root/"other"
        (other/"results").mkdir(parents=True)
        result = json.loads(json.dumps(self.result));result["scenario_version"]=2
        transcript = json.loads(json.dumps(self.transcript));transcript["tool_trace"][0]["output"]="different observation"
        (other/"result.json").write_text(json.dumps(result))
        (other/"results/transcript.json").write_text(json.dumps(transcript))
        manifest = self.root/"both.json"
        data = inventory([self.trial,other],manifest)
        families = []
        for index,record in enumerate(data["records"]):
            copy = self.root/f"review-{index}.json"
            family = f"incorrectly-separated-family-{index}"
            families.append(family)
            prepare(load_object(manifest),record["id"],copy)
            review(manifest,record["id"],family=family,reviewer="fixture",quality="accepted",privacy="approved",rights="approved",evidence="fixture permission",copy=copy)
        with self.assertRaisesRegex(ValueError,"same scenario"):
            export(load_object(manifest),{"train":[families[0]],"validation":[],"test":[families[1]]},self.root/"out","v1")

    def test_observable_duplicates_ignore_incidental_model_and_call_ids(self):
        prepare(self.data,self.id,self.copy)
        a=load_object(self.copy); b=json.loads(json.dumps(a))
        b["agent"]["model_name"]="another/model"
        b["steps"][1]["tool_calls"][0]["tool_call_id"]="another-call"
        b["steps"][1]["model_name"]="another/model"
        self.assertEqual(observable_hash(a),observable_hash(b))

if __name__ == "__main__": unittest.main()
