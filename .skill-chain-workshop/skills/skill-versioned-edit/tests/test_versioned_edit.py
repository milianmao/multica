from __future__ import annotations
import argparse, importlib, json, sys, tempfile, unittest
from pathlib import Path
from unittest.mock import Mock, patch
S=Path(__file__).resolve().parents[1]/"scripts"; sys.path.insert(0,str(S))
prepare=importlib.import_module("prepare_change"); common=importlib.import_module("_common")

def w(p,x): p.parent.mkdir(parents=True,exist_ok=True); p.write_text(json.dumps(x))
def repo(root):
 (root/"benchmark").mkdir(); (root/"benchmark/run.py").write_text("#"); (root/"skills/a").mkdir(parents=True); (root/"skills/a/SKILL.md").write_text("---\nname: a\n---\n"); return root

class Basics(unittest.TestCase):
 def test_targets_safe_multiple_and_duplicate(self):
  with tempfile.TemporaryDirectory() as d:
   r=repo(Path(d)); (r/"skills/b").mkdir(); (r/"skills/b/SKILL.md").write_text("---\nname: b\n---\n")
   self.assertEqual(2,len(common.validate_targets(["skills/a/SKILL.md","skills/b/SKILL.md"],r)))
   with self.assertRaises(ValueError): common.validate_targets(["skills/a/SKILL.md","a/SKILL.md"],r)
   outside=r/"x/SKILL.md"; outside.parent.mkdir(); outside.write_text("x")
   with self.assertRaises(ValueError): common.validate_targets([str(outside)],r)
 def test_duplicate_change_id_rejected_before_command(self):
  with tempfile.TemporaryDirectory() as d:
   r=repo(Path(d)); w(common.record_path(r,"dup"),{})
   runner=Mock(); args=argparse.Namespace(target=["skills/a/SKILL.md"],change_id="dup",suite="x",mutations="y",repo_root=r)
   with self.assertRaises(FileExistsError): prepare.prepare(args,runner)
   runner.assert_not_called()
 def test_prepare_command_is_backup_only(self):
  with tempfile.TemporaryDirectory() as d:
   r=repo(Path(d)); suite=r/"benchmark/suites/s.json"; mut=r/"benchmark/mutations/m.json"; w(suite,{"id":"s"}); w(mut,{"mutations":[]})
   runner=Mock(return_value=argparse.Namespace(returncode=1,stderr="stop",stdout=""))
   args=argparse.Namespace(target=["skills/a/SKILL.md"],change_id="c",suite=str(suite),mutations=str(mut),repo_root=r)
   with self.assertRaises(RuntimeError): prepare.prepare(args,runner)
   cmd=runner.call_args.args[0]; self.assertIn("backup",cmd)
   self.assertFalse(any(x in cmd for x in ("skills","mutation","assess","snapshot","full")))
