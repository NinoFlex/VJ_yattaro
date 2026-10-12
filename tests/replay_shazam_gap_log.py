"""Replay the supplied Q-evolution RESULT events, not audio/network activity.

Examples (run at the application source root):
    python tests/replay_shazam_gap_log.py
    python tests/replay_shazam_gap_log.py --source-root PATH_TO_PREVIOUS_SOURCE

Qt and metadata HTTP are replaced with test doubles. No microphone is opened.
"""
import argparse
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import types
from unittest.mock import patch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    source = args.source_root.resolve()
    sys.path.insert(0, str(source))
    from tests.test_webview_integration import ShazamService
    from tests.test_latency_prefetch import ControlledPrefetch

    fixture_path = Path(__file__).parent / 'fixtures' / 'shazam_q_gap.json'
    fixture = json.loads(fixture_path.read_text(encoding='utf-8'))
    clock = [100.0]
    trace, notifications = [], []
    captured = io.StringIO()
    config = types.SimpleNamespace(get=lambda _key, default=None: default)
    with tempfile.TemporaryDirectory() as folder, \
         patch('time.monotonic', side_effect=lambda: clock[0]), \
         patch('app.services.config_service.ConfigService', return_value=config), \
         patch.object(ShazamService, '_get_history_path', return_value=Path(folder) / 'history.json'), \
         contextlib.redirect_stdout(captured):
        service = ShazamService()
        service._active = True
        service._generation = 1
        service._next_temporal_sequence = 71
        service._metadata_prefetch = ControlledPrefetch()
        service.new_track_detected.connect(lambda entry: notifications.append({
            'sequence': service._latest_published_sequence,
            'elapsed_seconds': round(clock[0] - 100.0, 3), 'title': entry[1],
        }))
        for event in fixture['events']:
            clock[0] = 100.0 + event['elapsed_seconds']
            seq, raw = event['sequence'], dict(event['raw'])
            result = (raw.get('title', ''), raw.get('artist', ''), '')
            service._handle_confirmed_group_result(seq % 4, seq, [seq % 4], [result], [raw])
            trace.append({'input_sequence': seq, 'input_time': event['time'],
                          'published_sequence': service._latest_published_sequence,
                          'history_count': len(service.get_history())})
        service.shutdown()
    report = {
        'scope': fixture['scope'],
        'source_root': str(source),
        'source_log': fixture['source_file'],
        'events': trace,
        'notifications': notifications,
        'captured_service_log': captured.getvalue().splitlines(),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
