import logging
import sys
import os
import math
import time
import numpy as np
from datetime import datetime

import requests
from web3 import Web3, Account, HTTPProvider, IPCProvider
from web3.utils.threads import Timeout


def env(k, default=None):
    try:
        return os.environ[k]
    except KeyError as e:
        if default is not None:
            return default
        else:
            raise e


def env_int(k, default=None):
    return int(env(k, default))


CHAIN_ID = env_int('CHAIN_ID')


def now_str():
    return datetime.now().strftime("%Y-%m-%d.%H:%M:%S")


def get_arg(i=0):
    if len(sys.argv) < (2 + i):
        raise Exception(f"expected at least {i+1} command line argument/s")
    return sys.argv[1 + i]


class AccountWrapper:
    """Wrap around account and nonce. nonce is tracked in memory after initialization."""

    def __init__(self, private_key, nonce=None):
        self.account = Account.privateKeyToAccount(private_key)
        self.nonce = w3.eth.getTransactionCount(self.account.address) if nonce is None else nonce

    @property
    def address(self):
        return self.account.address

    @property
    def private_key(self):
        return to_hex(self.account.privateKey)

    def get_use_nonce(self):
        self.nonce += 1
        return self.nonce - 1

    def balance(self):
        return w3.eth.getBalance(self.account.address)


def create_account():
    return AccountWrapper(Account.create().privateKey, 0)


def sign_send_tx(from_account, tx_dict):
    signed_tx = w3.eth.account.signTransaction(tx_dict, from_account.privateKey)
    try:
        return w3.toHex(w3.eth.sendRawTransaction(signed_tx.rawTransaction))
    except Timeout as e:
        log(f"ipc timeout ({e}). ignoring.")
        return w3.toHex(signed_tx.hash)


def send_ether(from_account, nonce, to_address, val, gas_price, gas_limit):
    tx = {
        "to": to_address,
        "gas": gas_limit,
        "gasPrice": gas_price,
        "value": val,
        "chainId": CHAIN_ID,
        "nonce": nonce
    }
    return sign_send_tx(from_account, tx)


def send_tokens(from_account, nonce, to_address, val, gas_price, gas_limit):
    tx = {
        "gas": gas_limit,
        "gasPrice": gas_price,
        "chainId": CHAIN_ID,
        "nonce": nonce
    }
    tx = ERC20_CONTRACT.functions.transfer(to_address, val).buildTransaction(tx)
    return sign_send_tx(from_account, tx)


def wait_for_tx(tx_hash):
    while True:
        tx = w3.eth.getTransactionReceipt(tx_hash)
        if tx and tx.blockNumber:
            return
        time.sleep(1)


def get_gas_price(threshold):
    r = requests.get('https://ethgasstation.info/json/ethgasAPI.json')
    return int(r.json()[threshold] * math.pow(10, 8))


def get_gas_price_low():
    return get_gas_price("safeLow")


def stringify_list(l):
    return [str(v) for v in l]


def ignore_timeouts(f):
    def wrapper(*args, **kw):
        while True:
            try:
                return f(*args, **kw)
            except Timeout as e:
                log(f"timeout in {f.__name__} ({e}). retrying")

    return wrapper


@ignore_timeouts
def get_block(n):
    return w3.eth.getBlock(n)


@ignore_timeouts
def get_latest_block():
    return w3.eth.getBlock("latest")


@ignore_timeouts
def get_transaction_count(address):
    return w3.eth.getTransactionCount(address)


@ignore_timeouts
def get_balance(address):
    return w3.eth.getBalance(address)


def weighted_quantile(values, quantiles, sample_weight):
    """ Very close to numpy.percentile, but supports weights.
    NOTE: quantiles should be in [0, 1]!
    :param values: numpy.array with data
    :param quantiles: array-like with many quantiles needed
    :param sample_weight: array-like of the same length as `array`
    :return: numpy.array with computed quantiles.
    """

    values = np.array(values)
    quantiles = np.array(quantiles)
    sample_weight = np.array(sample_weight)
    assert np.all(quantiles >= 0) and np.all(quantiles <= 1), 'quantiles should be in [0, 1]'

    sorter = np.argsort(values)
    values = values[sorter]
    sample_weight = sample_weight[sorter]

    weighted_quantiles = np.cumsum(sample_weight) - 0.5 * sample_weight
    weighted_quantiles /= np.sum(sample_weight)
    return np.interp(quantiles, weighted_quantiles, values)


class CSVWriter:
    def __init__(self, path, cols):
        self.path = path
        self.cols = cols
        with open(path, "w") as csv_file:
            csv_file.write(",".join(cols) + "\n")

    def append(self, row):
        assert len(row) == len(self.cols)
        with open(self.path, "a+") as csv_file:
            csv_file.write(",".join(stringify_list(row)) + "\n")

    def append_all(self, rows):
        with open(self.path, "a+") as csv_file:
            csv_file.write('\n'.join([",".join(stringify_list(row)) for row in rows]) + "\n")


root = logging.getLogger()
root.setLevel(logging.DEBUG)
ch = logging.StreamHandler(sys.stdout)
ch.setLevel(logging.INFO)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
ch.setFormatter(formatter)
root.addHandler(ch)


def log(m):
    logging.info(m)


def get_w3():
    try:
        return Web3(IPCProvider(env("IPC_PROVIDER"), timeout=2))
    except KeyError:
        log("No IPC provider. using HTTP provider")
        return Web3(HTTPProvider(env("HTTP_PROVIDER")))


def ether_to_wei(eth):
    return w3.toWei(eth)


def wei_to_ether(wei):
    return w3.fromWei(wei, 'ether')


def wei_to_gwei(wei):
    return float(w3.fromWei(wei, 'gwei'))


w3 = get_w3()
w3.eth.enable_unaudited_features()
to_hex = w3.toHex

funder = AccountWrapper(env('FUNDER_PK'))

# contract
with open(env('ERC20_ABI_PATH'), 'r') as myfile:
    ERC20_CONTRACT = w3.eth.contract(address=env('ERC20_ADDRESS'), abi=myfile.read().replace('\n', ''))
