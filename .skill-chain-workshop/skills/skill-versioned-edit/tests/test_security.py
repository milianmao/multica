from __future__ import annotations
import argparse, importlib, json, os, stat, sys, tempfile, unittest
from pathlib import Path
from unittest.mock import Mock
S=Path(__file__).resolve().parents[1]/"scripts"; sys.path.insert(0,str(S))
evaluate=importlib.import_module("evaluate_change"); common=importlib.import_module("_common")

def w(p,x): p.parent.mkdir(parents=True,exist_ok=True); p.write_text(json.dumps(x))
def chmod(root):
    for p in [root,*root.rglob("*")]: p.chmod(p.stat().st_mode|stat.S_IWUSR|(stat.S_IXUSR if p.is_dir() else 0))
def fixture(root,cid="c1"):
    sys.path.insert(0,str(root)); (root/"benchmark").mkdir(); (root/"skills"/"a").mkdir(parents=True); (root/"skills"/"a"/"SKILL.md").write_text("---\nname: a\n---\n")
    # use real benchmark package through namespace, create sealed backup with real API
    from benchmark.scripts.subject_loader import materialize_subject
    from benchmark.scripts.suite_loader import load_suite
    from benchmark.scripts.backup import create_backup
    suite_src=Path.cwd()/"benchmark/suites/skill-chain-core-v1.json"; mut_src=Path.cwd()/"benchmark/mutations/core.json"
    dest=root/"benchmark/change-backups"/cid
    with materialize_subject("current",root) as subject: create_backup(subject,load_suite(suite_src),suite_src,mut_src,dest)
    loaded=__import__("benchmark.scripts.backup",fromlist=["load_backup"]).load_backup(dest); m=loaded["manifest"]
    rec={"change_id":cid,"status":"prepared","before":{"backup_path":dest.relative_to(root).as_posix(),"subject":f"path:{(dest/'subject').relative_to(root).as_posix()}","manifest":(dest/'manifest.json').relative_to(root).as_posix(),"subject_hash":m["subject_hash"]},"frozen_evaluation":{"suite_id":m["suite_id"],"suite_hash":m["suite_hash"],"mutation_set_hash":m["mutation_set_hash"],"suite_path":(dest/m["suite_path"]).relative_to(root).as_posix(),"mutation_path":(dest/m["mutations_path"]).relative_to(root).as_posix()},"evaluation":{"status":"deferred"}}
    w(common.record_path(root,cid),rec); return dest,rec

class Security(unittest.TestCase):
 def test_record_redirect_rejected(self):
  with tempfile.TemporaryDirectory() as d:
   r=Path(d); _,rec=fixture(r); rec["before"]["subject"]="path:skills"; w(common.record_path(r,"c1"),rec)
   with self.assertRaisesRegex(ValueError,"redirects"): evaluate.evaluate(argparse.Namespace(change_id="c1",repo_root=r,retry=False),Mock())
 def test_frozen_case_tamper_rejected_by_seal(self):
  with tempfile.TemporaryDirectory() as d:
   r=Path(d); b,_=fixture(r); target=next((b/"evaluation/skill_cases").glob("*.json")); target.chmod(0o644); target.write_text(target.read_text()+"x")
   with self.assertRaisesRegex(ValueError,"hash mismatch|JSON integrity"): evaluate.evaluate(argparse.Namespace(change_id="c1",repo_root=r,retry=False),Mock())
 def test_stale_results_do_not_get_reused(self):
  with tempfile.TemporaryDirectory() as d:
   r=Path(d); fixture(r); stale=r/"benchmark/results/c1-before"; w(stale/"mutations.json",{})
   runner=Mock(side_effect=RuntimeError("stop"))
   with self.assertRaises(RuntimeError): evaluate.evaluate(argparse.Namespace(change_id="c1",repo_root=r,retry=False),runner)
   self.assertNotIn("c1-before", " ".join(runner.call_args.args[0]))
 def test_retry_state_machine_and_new_attempt(self):
  with tempfile.TemporaryDirectory() as d:
   r=Path(d); _,rec=fixture(r); rec["status"]="evaluation_failed"; rec["evaluation"]={"status":"failed","attempt_id":"old"}; w(common.record_path(r,"c1"),rec)
   with self.assertRaisesRegex(ValueError,"requires --retry"): evaluate.evaluate(argparse.Namespace(change_id="c1",repo_root=r,retry=False),Mock())
   runner=Mock(side_effect=RuntimeError("new attempt"))
   with self.assertRaises(RuntimeError): evaluate.evaluate(argparse.Namespace(change_id="c1",repo_root=r,retry=True),runner)
   updated=json.loads(common.record_path(r,"c1").read_text()); self.assertNotEqual("old",updated["evaluation"]["attempt_id"])
