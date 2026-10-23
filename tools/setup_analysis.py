"""Install pinned project-local static-analysis tools, without device changes."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app_analysis import JADX_HASH, digest, environment


def main():
    folder = ROOT / 'qa_results/tools'
    folder.mkdir(parents=True, exist_ok=True)
    npm = shutil.which('npm.cmd' if os.name == 'nt' else 'npm')
    if not npm:
        raise RuntimeError('Node.js/npm is required.')
    subprocess.run([npm, 'install', '--prefix', str(folder / 'rea'),
                    'rea-agents@6.4.0', '--no-audit', '--no-fund'], check=True, timeout=600)
    jar = folder / 'jadx-headless-mcp-0.7.1-all.jar'
    if not jar.exists() or digest(jar) != JADX_HASH:
        temporary = jar.with_suffix('.download')
        try:
            urllib.request.urlretrieve(
                'https://github.com/1013503897/jadx-headless-mcp/releases/download/v0.7.1/'
                'jadx-headless-mcp-0.7.1-all.jar', temporary)
            if digest(temporary) != JADX_HASH:
                raise RuntimeError('Downloaded JADX checksum did not match. Nothing activated.')
            os.replace(temporary, jar)
        finally:
            temporary.unlink(missing_ok=True)
    import json
    print(json.dumps(environment(), indent=2))
    print('No device action or AI call performed. Appium is validated separately.')


if __name__ == '__main__':
    main()
