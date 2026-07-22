#!/usr/bin/env python3
"""Headless tests for the bench-profile store (issue #47).

Run: .venv/bin/python tests/test_bench_profiles.py
"""
# Runnable from anywhere: put the repo root (one level up) on sys.path
# so the app modules import when this file is executed directly.
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(
    _os.path.abspath(__file__))))
import json
import os
import shutil
import tempfile

from bench_profiles import (BenchProfileStore, write_profile_file,
                            read_profile_file)

PROFILE = {'lcr': {'mode': 'CPD', 'freq_hz': '1000'},
           'scope': {'hscale': '1e-3'},
           'siggen': {'1': {'waveform': 'SINE'}}}


def _store():
    d = tempfile.mkdtemp(prefix='bench_prof_')
    return BenchProfileStore(os.path.join(d, 'sub', 'profiles.json')), d


def test_round_trip():
    store, d = _store()
    try:
        assert store.names() == []
        assert store.save('  cap sweep  ', PROFILE) == 'cap sweep'
        assert store.names() == ['cap sweep']
        assert store.load('cap sweep') == PROFILE
    finally:
        shutil.rmtree(d)


def test_overwrite_and_delete():
    store, d = _store()
    try:
        store.save('a', PROFILE)
        store.save('a', {'lcr': {}})
        assert store.load('a') == {'lcr': {}}, "same name must overwrite"
        store.save('b', PROFILE)
        store.delete('a')
        assert store.names() == ['b']
        store.delete('nonexistent')          # deleting a ghost is a no-op
        assert store.names() == ['b']
    finally:
        shutil.rmtree(d)


def test_validation():
    store, d = _store()
    try:
        for bad in ('', '   ', None):
            try:
                store.save(bad, PROFILE)
                assert False, f"{bad!r} must raise"
            except ValueError:
                pass
        try:
            store.save('x', 'not-a-dict')
            assert False
        except ValueError:
            pass
        try:
            store.load('missing')
            assert False
        except KeyError:
            pass
    finally:
        shutil.rmtree(d)


def test_corrupt_file_reads_empty_and_recovers():
    store, d = _store()
    try:
        os.makedirs(os.path.dirname(store.path))
        with open(store.path, 'w') as f:
            f.write('{ not json !!')
        assert store.names() == [], "corrupt file must read as empty"
        store.save('fresh', PROFILE)         # ...and stay writable
        assert store.load('fresh') == PROFILE
        with open(store.path) as f:
            json.load(f)                     # file is valid JSON again
    finally:
        shutil.rmtree(d)


def test_names_sorted():
    store, d = _store()
    try:
        for n in ('zeta', 'Alpha', 'mid'):
            store.save(n, PROFILE)
        assert store.names() == sorted(['zeta', 'Alpha', 'mid'])
    finally:
        shutil.rmtree(d)


def test_profile_file_round_trip():
    d = tempfile.mkdtemp(prefix='bench_prof_file_')
    try:
        path = os.path.join(d, 'sub', 'setup.json')
        write_profile_file(path, PROFILE)              # makes parent dir
        got = read_profile_file(path)
        assert got['lcr'] == PROFILE['lcr']
        assert got['scope'] == PROFILE['scope']
        with open(path) as f:
            assert json.load(f)['kind'] == 'scpi_bench_profile'
    finally:
        shutil.rmtree(d)


def test_profile_file_imports_single_store():
    d = tempfile.mkdtemp(prefix='bench_prof_file_')
    try:
        store = BenchProfileStore(os.path.join(d, 'store.json'))
        store.save('only', PROFILE)
        got = read_profile_file(os.path.join(d, 'store.json'))
        assert got['scope'] == PROFILE['scope']
        store.save('second', {'lcr': {'mode': 'RX'}})
        try:
            read_profile_file(os.path.join(d, 'store.json'))
            assert False, "multi-profile store must raise"
        except ValueError:
            pass
    finally:
        shutil.rmtree(d)


def test_profile_file_rejects_junk():
    d = tempfile.mkdtemp(prefix='bench_prof_file_')
    try:
        p = os.path.join(d, 'junk.json')
        with open(p, 'w') as f:
            f.write('{"nothing": 1}')
        try:
            read_profile_file(p)
            assert False, "must raise on a non-profile file"
        except ValueError:
            pass
    finally:
        shutil.rmtree(d)


def _run():
    fns = [v for k, v in sorted(globals().items()) if k.startswith('test_')]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} tests passed")


if __name__ == '__main__':
    _run()
