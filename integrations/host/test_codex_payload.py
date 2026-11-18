from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest

ROOT=Path(__file__).parent
spec=importlib.util.spec_from_file_location('codex_payload',ROOT/'prepare-codex-payload.py')
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class CodexPayloadTests(unittest.TestCase):
    def fixture(self,root):
        binary=root/'native';binary.mkdir()
        main=binary/'codex'
        main.write_text('''#!/usr/bin/env bash
set -euo pipefail
"$(dirname "$0")/codex-code-mode-host"
output=""
while (( $# )); do
 if [[ "$1" == --output-last-message ]]; then output="$2"; shift 2; else shift; fi
done
cat >/dev/null
printf 'repair complete\\n' >"$output"
printf '%s\\n' '{"type":"turn.completed","usage":{"input_tokens":12,"output_tokens":3,"cached_input_tokens":0}}'
''')
        helper=binary/'codex-code-mode-host'
        helper.write_text('#!/usr/bin/env bash\nprintf present >"$CODEX_HOME/companion-ran"\n')
        for p in [main,helper]:p.chmod(0o755)
        return main

    def test_bundle_is_reproducible_and_includes_companion(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);main=self.fixture(root)
            outputs=[root/'one.tar.gz',root/'two.tar.gz']
            for p in outputs:module.bundle(main,p)
            self.assertEqual(hashlib.sha256(outputs[0].read_bytes()).digest(),hashlib.sha256(outputs[1].read_bytes()).digest())
            with tarfile.open(outputs[0]) as archive:
                self.assertEqual(archive.getnames(),['codex','codex-code-mode-host'])
                self.assertTrue(all(item.isfile() for item in archive))
            with self.assertRaises(ValueError):module.bundle(main,outputs[0])

    def test_missing_helper_is_rejected_before_creating_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);main=self.fixture(root)
            main.with_name('codex-code-mode-host').unlink()
            with self.assertRaises(ValueError):module.bundle(main,root/'payload.tar.gz')
            self.assertFalse((root/'payload.tar.gz').exists())

    def run_adapter(self,root,payload):
        evaluate=root/'eval';(evaluate/'results').mkdir(parents=True)
        instruction=root/'instruction.md';instruction.write_text('repair test service')
        env={**os.environ,'REPLAYBOOK_AGENT_PAYLOAD':str(payload),'REPLAYBOOK_EVAL_ROOT':str(evaluate),
             'REPLAYBOOK_INSTRUCTION_FILE':str(instruction),'REPLAYBOOK_MODEL':'test-model',
             'REPLAYBOOK_RESULT_FILE':str(evaluate/'results/agent.json'),
             'REPLAYBOOK_TRANSCRIPT_FILE':str(evaluate/'results/transcript.json'),
             'REPLAYBOOK_WORKSPACE':str(root),'CODEX_API_KEY':'fake-test-key'}
        env.pop('CODEX_AUTH_JSON_B64',None);env.pop('CODEX_ACCESS_TOKEN',None)
        return subprocess.run(['bash',str(ROOT/'adapters/codex.sh')],env=env,text=True,capture_output=True),evaluate

    def test_adapter_runs_companion_from_bundle_and_normalizes_result(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);main=self.fixture(root);payload=root/'payload'
            module.bundle(main,payload)
            completed,evaluate=self.run_adapter(root,payload)
            self.assertEqual(completed.returncode,0,completed.stderr)
            self.assertEqual((evaluate/'codex-home/companion-ran').read_text(),'present')
            result=json.loads((evaluate/'results/agent.json').read_text())
            self.assertEqual(result['usage']['input_tokens'],12)
            self.assertEqual(result['result'],'repair complete\n')

    def test_unsafe_archive_members_are_rejected_without_extraction(self):
        for kind in ['symlink','traversal']:
            with self.subTest(kind=kind),tempfile.TemporaryDirectory() as directory:
                root=Path(directory);payload=root/'payload'
                with tarfile.open(payload,'w:gz') as archive:
                    for name in ['codex','codex-code-mode-host']:
                        info=tarfile.TarInfo(name)
                        if kind=='symlink' and name=='codex':
                            info.type=tarfile.SYMTYPE;info.linkname='/bin/sh';archive.addfile(info)
                        else:
                            data=b'#!/bin/sh\nexit 0\n';info.size=len(data);archive.addfile(info,io.BytesIO(data))
                    if kind=='traversal':
                        data=b'bad';info=tarfile.TarInfo('../escape');info.size=3;archive.addfile(info,io.BytesIO(data))
                completed,evaluate=self.run_adapter(root,payload)
                self.assertNotEqual(completed.returncode,0)
                self.assertFalse((root/'escape').exists())
                self.assertFalse((evaluate/'codex-bin').exists())


if __name__=='__main__':unittest.main()
