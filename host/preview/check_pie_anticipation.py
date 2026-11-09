import argparse
import json
from pathlib import Path
import statistics
import time
import urllib.request


def read_status(url):
    with urllib.request.urlopen(url, timeout=5) as response:
        status = json.load(response)
    result = status.get('anticipation', {})
    if (result.get('model_id') != 'pie_anticipation_v2_20261101' or result.get('device') != 'RK3568_CPU'
            or result.get('eligibility') != 'unverified' or result.get('no_control') is not True):
        raise ValueError('Board does not expose the PIE v2 test contract')
    return status


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', default='http://127.0.0.1:9011/api/status')
    parser.add_argument('--seconds', type=float, default=0)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if not 0 <= args.seconds <= 300:
        parser.error('--seconds must be between 0 and 300')
    status = read_status(args.url)
    result = status['anticipation']
    print('PIE_BOARD_API_PASS', json.dumps(result, ensure_ascii=False))
    if not args.seconds:
        return
    started = time.monotonic()
    observations = []
    seen = set()
    while time.monotonic()-started < args.seconds:
        status = read_status(args.url)
        world = status.get('world') or {}
        key = (status.get('session'), world.get('producer'), world.get('object_seq'))
        if status.get('state') == 'RUNNING' and world.get('object_valid') is True and key not in seen:
            seen.add(key)
            prediction = status['anticipation']
            observations.append(dict(session=key[0], producer=key[1], sequence=key[2],
                                     object_ms=world.get('object_ms'), reason=prediction.get('reason'),
                                     age_ms=prediction.get('age_ms'), persons=prediction.get('persons', [])))
        time.sleep(.1)
    intervals = [later['object_ms']-earlier['object_ms'] for earlier, later in zip(observations, observations[1:])
                 if later['session'] == earlier['session'] and later['producer'] == earlier['producer']
                 and type(later['object_ms']) is int and type(earlier['object_ms']) is int
                 and later['object_ms'] > earlier['object_ms']]
    scored = sum(any(person.get('status') == 'SCORED' for person in row['persons']) for row in observations)
    report = dict(status='PIE_BOARD_OBSERVATION_COMPLETE', seconds=time.monotonic()-started,
                  unique_object_reports_observed=len(observations), reports_with_scores=scored,
                  observed_interval_ms_median=statistics.median(intervals) if intervals else None,
                  observed_intervals_ms=intervals, observations=observations,
                  note='HTTP-observed subset of real board reports; no accuracy ground truth; no driving control')
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print('PIE_BOARD_OBSERVATION_COMPLETE', json.dumps({key: value for key, value in report.items()
                                                     if key not in ('observations', 'observed_intervals_ms')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
