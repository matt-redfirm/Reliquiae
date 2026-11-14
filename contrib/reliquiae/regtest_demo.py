#!/usr/bin/env python3
"""Run a disposable, loopback-only confidential transfer between two regtest nodes."""
import argparse
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import tempfile
import time


class DemoError(RuntimeError):
    pass


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


class Node:
    def __init__(self, bindir, datadir, owner):
        self.cli = bindir / 'elements-cli'
        self.datadir = datadir
        self.datadir.mkdir(mode=0o700)
        self.port = free_port()
        self.rpcport = free_port()
        self.log = (datadir / 'process.log').open('w')
        self.process = subprocess.Popen([
            str(bindir / 'elementsd'), '-chain=elementsregtest',
            '-datadir=' + str(datadir), '-server=1', '-listen=1',
            '-bind=127.0.0.1:' + str(self.port), '-port=' + str(self.port),
            '-rpcbind=127.0.0.1', '-rpcallowip=127.0.0.1',
            '-rpcport=' + str(self.rpcport), '-connect=0', '-dnsseed=0',
            '-discover=0', '-listenonion=0', '-validatepegin=0', '-fallbackfee=0.00001',
            '-initialfreecoins=10000000000', '-con_blocksubsidy=0',
            '-con_connect_genesis_outputs=1', '-blindedaddresses=1',
            '-anyonecanspendaremine=' + ('1' if owner else '0'),
        ], stdout=self.log, stderr=subprocess.STDOUT, start_new_session=True)

    def rpc(self, method, *args, wallet=False, named=False):
        command = [str(self.cli), '-chain=elementsregtest',
                   '-datadir=' + str(self.datadir), '-rpcport=' + str(self.rpcport)]
        if wallet:
            command.append('-rpcwallet=demo')
        if named:
            command.append('-named')
        command += [method, *map(str, args)]
        result = subprocess.run(command, text=True, capture_output=True, timeout=15)
        if result.returncode:
            (self.datadir / 'rpc-failure.log').write_text(method + '\n' + result.stderr)
            raise DemoError(method + ' failed; private diagnostic files remain in the temporary run')
        output = result.stdout.strip()
        if not output:
            return None
        try:
            return json.loads(output, parse_float=Decimal)
        except json.JSONDecodeError:
            return output

    def stop(self):
        if self.process.poll() is None:
            try:
                self.rpc('stop')
            except (DemoError, subprocess.TimeoutExpired):
                self.process.terminate()
            try:
                self.process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        self.log.close()


def wait_for(predicate, description, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if predicate():
                return
        except (DemoError, subprocess.TimeoutExpired):
            pass
        time.sleep(0.2)
    raise DemoError('Timed out waiting for ' + description)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bindir', required=True, type=Path,
                        help='Directory containing elementsd and elements-cli')
    parser.add_argument('--legacy-wallet', action='store_true',
                        help='Use a legacy recipient wallet as well as the legacy genesis-funding wallet')
    parser.add_argument('--publish', type=Path,
                        help='Parent directory for verified, secret-free results')
    args = parser.parse_args()
    bindir = args.bindir.resolve()
    binaries = [bindir / name for name in ('elementsd', 'elements-cli')]
    if not all(path.is_file() and os.access(path, os.X_OK) for path in binaries):
        parser.error('--bindir must contain executable elementsd and elements-cli')
    run = Path(tempfile.mkdtemp(prefix='reliquiae-regtest-demo-'))
    state = {
        'status': 'incomplete', 'run_id': run.name,
        'scope': 'No-value confidential transfer; not a Bitcoin peg or production network',
        'parent_validation': False, 'listen': '127.0.0.1 only',
        'binary_sha256': {p.name: digest(p) for p in binaries},
        'script_sha256': digest(Path(__file__).resolve()),
        'wallets': {'funder': 'legacy', 'recipient': 'legacy' if args.legacy_wallet else 'descriptor'},
    }
    nodes = []

    def save():
        (run / 'result.json').write_text(json.dumps(state, indent=2) + '\n')

    def interrupted(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupted)
    save()
    try:
        for label in ('a', 'b'):
            node = Node(bindir, run / label, owner=label == 'a')
            nodes.append(node)
            wait_for(lambda: node.rpc('getblockchaininfo').get('chain') == 'elementsregtest', 'node ' + label)
            node.rpc('createwallet', 'wallet_name=demo',
                     'descriptors=' + ('false' if label == 'a' or args.legacy_wallet else 'true'), named=True)
        a, b = nodes
        a.rpc('rescanblockchain', 0, wallet=True)
        a.rpc('addnode', '127.0.0.1:' + str(b.port), 'onetry')
        wait_for(lambda: a.rpc('getconnectioncount') == 1 and b.rpc('getconnectioncount') == 1,
                 'regtest peer handshake')
        if a.rpc('getblockhash', 0) != b.rpc('getblockhash', 0):
            raise DemoError('Regtest genesis mismatch')
        mining_address = a.rpc('getnewaddress', wallet=True)
        a.rpc('generatetoaddress', 101, mining_address)
        wait_for(lambda: a.rpc('getbestblockhash') == b.rpc('getbestblockhash'), 'initial chain synchronization')
        # Move the regtest genesis allocation to standard wallet outputs before peer relay.
        a.rpc('sendtoaddress', mining_address, '99', wallet=True)
        a.rpc('generatetoaddress', 1, mining_address)
        wait_for(lambda: a.rpc('getbestblockhash') == b.rpc('getbestblockhash'), 'funding confirmation')
        address = b.rpc('getnewaddress', wallet=True)
        address_info = b.rpc('getaddressinfo', address, wallet=True)
        if address_info.get('confidential') != address:
            raise DemoError('Recipient did not return a confidential address')
        txid = a.rpc('sendtoaddress', address, '1.25', wallet=True)
        wait_for(lambda: txid in b.rpc('getrawmempool'), 'transaction relay')
        a.rpc('generatetoaddress', 1, mining_address)
        wait_for(lambda: a.rpc('getbestblockhash') == b.rpc('getbestblockhash'), 'confirmation')
        outputs = b.rpc('listunspent', wallet=True)
        received = [o for o in outputs if o.get('txid') == txid]
        if sum((o['amount'] for o in received), Decimal(0)) != Decimal('1.25'):
            raise DemoError('Recipient amount mismatch')
        if not received or not all(o.get('amountcommitment') and o.get('assetcommitment') for o in received):
            raise DemoError('Recipient outputs were not confidential')
        if not all(o.get('confirmations', 0) >= 1 for o in received):
            raise DemoError('Recipient output is unconfirmed')
        state.update(status='passed', transaction_id=txid, recipient_amount='1.25',
                     confidential_outputs=True, height=b.rpc('getblockcount'))
    except KeyboardInterrupt:
        state.update(status='interrupted')
    except Exception as exc:
        state.update(status='failed', error=str(exc) if isinstance(exc, DemoError) else type(exc).__name__)
    finally:
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        for node in reversed(nodes):
            node.stop()
        state['children_exited'] = all(node.process.poll() is not None for node in nodes)
        save()
    if state['status'] == 'passed' and state['children_exited'] and args.publish:
        if any(digest(p) != state['binary_sha256'][p.name] for p in binaries):
            raise DemoError('A binary changed during the run; results were not published')
        destination = args.publish.resolve() / run.name
        destination.mkdir(parents=True, exist_ok=False)
        shutil.copy2(run / 'result.json', destination / 'result.json')
        print('Verified result: ' + str(destination / 'result.json'))
    print('Result: ' + state['status'])
    print('Temporary run: ' + str(run))
    if state['status'] == 'passed':
        print('Confirmed 1.25 test units in confidential recipient outputs. Both nodes stopped.')
        print('No Bitcoin parent or reserve withdrawal was tested.')
    return 0 if state['status'] == 'passed' and state['children_exited'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
