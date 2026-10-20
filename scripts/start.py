"""金融资产监控看板：创建本地环境、安装依赖并启动服务。"""
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import venv

ROOT = Path(__file__).resolve().parents[1]


def main():
    if not (3, 11) <= sys.version_info[:2] <= (3, 13):
        raise RuntimeError('请先安装 Python 3.11、3.12 或 3.13。')
    environment = ROOT / '.venv'
    python = environment / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    if not python.exists():
        print('正在创建 Python 环境…', flush=True)
        venv.EnvBuilder(with_pip=True).create(environment)
    requirements = ROOT / 'backend/requirements.txt'
    fingerprint = hashlib.sha256(requirements.read_bytes()).hexdigest()
    marker = environment / '.requirements.sha256'
    if not marker.exists() or marker.read_text().strip() != fingerprint:
        print('正在安装依赖，首次运行需要联网，请稍候…', flush=True)
        subprocess.run([str(python), '-m', 'pip', 'install', '-r', str(requirements)], cwd=ROOT, check=True)
        marker.write_text(fingerprint)
    print('金融资产监控看板：http://127.0.0.1:19192\n请保持此窗口运行，按 Ctrl+C 停止。', flush=True)
    return subprocess.call([str(python), str(ROOT / 'backend/tests/serve_ui.py')], cwd=ROOT)


if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print('\n已停止。')
    except (RuntimeError, OSError, subprocess.CalledProcessError) as error:
        print(f'启动失败：{error}', file=sys.stderr)
        sys.exit(1)
