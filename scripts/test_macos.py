#!/usr/bin/env python3
"""Exercise the deployed Cocoa/OpenGL app, including clean shutdown.

Run in a logged-in macOS GUI session (also available on GitHub macOS runners).
"""
import argparse
import json
import os
import re
from pathlib import Path
import subprocess
import struct
import tempfile
import time
import uuid


def run(*args, **kwargs):
    return subprocess.run(args, check=True, text=True, capture_output=True,
                          timeout=30, **kwargs).stdout


def assert_rendered_page(screenshot):
    # PNG byte size depends heavily on display resolution and compression.
    # Inspect actual pixels instead: the tutorial must contain dark text.
    bitmap = screenshot.with_suffix('.bmp')
    run('sips', '-s', 'format', 'bmp', str(screenshot), '--out', str(bitmap))
    data = bitmap.read_bytes()
    offset = struct.unpack_from('<I', data, 10)[0]
    width, height = struct.unpack_from('<ii', data, 18)
    bits = struct.unpack_from('<H', data, 28)[0]
    assert data[:2] == b'BM' and bits in (24, 32) and width > 100 and abs(height) > 100
    stride = ((width * bits + 31) // 32) * 4
    dark_pixels = sum(
        max(data[pos:pos + 3]) < 128
        for row in range(abs(height))
        for pos in range(offset + row * stride, offset + row * stride + width * (bits // 8), bits // 8)
    )
    bitmap.unlink()
    assert dark_pixels > 100, f'PDF framebuffer is blank ({dark_pixels} dark pixels)'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('app', type=Path)
    parser.add_argument('--output', type=Path, default=Path('build/smoke-test'))
    args = parser.parse_args()
    app = args.app.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    binary = app / 'Contents/MacOS/sioyek'
    pdf = app / 'Contents/Resources/tutorial.pdf'
    run('codesign', '--verify', '--deep', '--strict', str(app))
    run('lipo', str(binary), '-verify_arch', os.uname().machine)
    # Every embedded Mach-O must be native and independent of the build machine.
    for path in app.rglob('*'):
        if not path.is_file() or path.is_symlink():
            continue
        if 'Mach-O' not in run('file', '-b', str(path)):
            continue
        run('lipo', str(path), '-verify_arch', os.uname().machine)
        identities = set(run('otool', '-D', str(path)).splitlines())
        for line in run('otool', '-L', str(path)).splitlines():
            # Universal binaries have an unindented header for each slice.
            if not line.startswith('\t'):
                continue
            dep = line.strip().split(' (')[0]
            if dep in identities:  # LC_ID_DYLIB is metadata, not a load command.
                continue
            assert dep.startswith(('@', '/usr/lib/', '/System/Library/')), (path, dep)
    print(run(str(binary), '--version').strip(), flush=True)
    # Unset developer Qt paths to test deployment, rather than the installed SDK.
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(('QT_', 'QML', 'DYLD_'))}
    with tempfile.TemporaryDirectory(prefix='sioyek-smoke-') as data_dir:
        Path(data_dir, 'prefs_user.config').write_text(
            'should_check_for_latest_version_on_startup 0\n')
        common = [str(binary), '--instance-name', 'smoke-' + uuid.uuid4().hex,
                  '--data-dir', data_dir, '--no-auto-config']
        with (output / 'application.log').open('w') as log:
            proc = subprocess.Popen(common + [str(pdf)], env=env, stdout=log, stderr=log)
            try:
                def alive():
                    assert proc.poll() is None, f'Application exited: {proc.returncode}; see {log.name}'

                def command(name):
                    alive()
                    return run(*common, '--execute-command', name,
                               '--wait-for-response', env=env)

                def state():
                    result = command('get_state_json')
                    # Startup diagnostics can surround the JSON IPC response.
                    for match in re.finditer(r'\[\s*\{', result):
                        try:
                            states, _ = json.JSONDecoder().raw_decode(result[match.start():])
                            return states[0]
                        except json.JSONDecodeError:
                            continue
                    raise AssertionError(f'Missing window state in IPC response: {result!r}')

                time.sleep(5)
                initial = state()
                assert Path(initial['document_path']) == pdf
                command('goto_beginning')
                time.sleep(1)
                first = state()
                command('next_page')
                time.sleep(1)
                advanced = state()
                assert advanced['y_offset'] > first['y_offset'], (first, advanced)
                # At an exact page boundary Sioyek reports the preceding page.
                command('next_page')
                assert state()['page_number'] > first['page_number']
                command('zoom_in')
                assert state()['zoom_level'] > first['zoom_level']
                command('zoom_out')
                command('goto_end')
                time.sleep(1)
                assert state()['page_number'] > first['page_number']
                command('goto_beginning')
                command('search(sioyek)')
                time.sleep(3)
                search = state()
                assert search.get('num_search_results', 0) > 0, search
                command('escape')
                command('toggle_dark_mode')
                command('toggle_dark_mode')
                screenshot = output / 'rendered-page.png'
                command(f'framebuffer_screenshot({screenshot})')
                assert_rendered_page(screenshot)
                (output / 'state.json').write_text(json.dumps(state(), indent=2))
                # Exercise graceful Qt teardown, renderer thread joins and DB writes.
                command('quit')
                assert proc.wait(timeout=20) == 0
            finally:
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait()
    print('PASS: native bundle, PDF navigation, zoom, search, rendering and shutdown', flush=True)


if __name__ == '__main__':
    main()
