from __future__ import annotations

import json
from urllib.error import URLError
from urllib.request import Request, urlopen


BINANCE_FAPI_EXCHANGE_INFO = "https://fapi.binance.com/fapi/v1/exchangeInfo"

HARDCODED_TRADFI_USDT = (
    "AAOIUSDT", "AAPLUSDT", "ADBEUSDT", "ALABUSDT", "AMATUSDT", "AMDUSDT", "AMZNUSDT",
    "ANTHROPICUSDT", "APPUSDT", "ARMUSDT", "ASMLUSDT", "ASTSUSDT", "AVGOUSDT", "AXTIUSDT",
    "BABAUSDT", "BBXUSDT", "BEUSDT", "BITOUSDT", "BMNRUSDT", "BNCUSDT", "BOTUSDT",
    "BRKBUSDT", "BSPUSDT", "BXUSDT", "BYDUSDT", "BZUSDT", "CATUSDT", "CBRSUSDT",
    "CIENUSDT", "CLUSDT", "COHRUSDT", "COINUSDT", "COPPERUSDT", "COSTUSDT", "CRCLUSDT",
    "CRDOUSDT", "CRMUSDT", "CRWDUSDT", "CRWVUSDT", "CSCOUSDT", "CSOPSAMSUNG2LUSDT",
    "CSOPSKHYNIX2LUSDT", "CXMTUSDT", "DDOGUSDT", "DELLUSDT", "DISUSDT", "DJTUSDT",
    "DKNGUSDT", "DRAMUSDT", "EBAYUSDT", "EWJUSDT", "EWTUSDT", "EWYUSDT", "EWZUSDT",
    "FLEXUSDT", "FLNCUSDT", "FWDIUSDT", "GDXUSDT", "GEVUSDT", "GIGADEVUSDT", "GLWUSDT",
    "GMEUSDT", "GOOGLUSDT", "GPROUSDT", "GSUSDT", "GTLBUSDT", "HANMIUSDT", "HDUSDT",
    "HIMSUSDT", "HK0625USDT", "HK0700USDT", "HK0992USDT", "HK1810USDT", "HOODUSDT",
    "HPEUSDT", "HYUNDAIUSDT", "IBMUSDT", "INTCUSDT", "INTWUSDT", "IONQUSDT", "IRENUSDT",
    "IWMUSDT", "JPMUSDT", "KLACUSDT", "KODEX200USDT", "KORUUSDT", "KOUSDT", "KSTRUSDT",
    "KUAISHOUUSDT", "LGELECTRONICSUSDT", "LITEUSDT", "LLYUSDT", "LRCXUSDT", "LYTEUSDT",
    "MARAUSDT", "MDBUSDT", "MEITUANUSDT", "METAUSDT", "MINIMAXUSDT", "MRKUSDT", "MRNAUSDT",
    "MRVLUSDT", "MSFTUSDT", "MSTRUSDT", "MUUSDT", "MUUUSDT", "MVLLUSDT", "NATGASUSDT",
    "NAVERUSDT", "NBISUSDT", "NETUSDT", "NFLXUSDT", "NOKUSDT", "NOWUSDT", "NVDAUSDT",
    "NVDLUSDT", "NVOUSDT", "ONDSUSDT", "OPENAIUSDT", "ORCLUSDT", "PANWUSDT", "PAYPUSDT",
    "PDDUSDT", "PENGUSDT", "PLTRUSDT", "POPMARTUSDT", "PYPLUSDT", "QCOMUSDT", "QNTXUSDT",
    "QQQUSDT", "RAMUSDT", "RDDTUSDT", "RIVNUSDT", "RKLBUSDT", "SAMSUNGEMUSDT", "SAMSUNGUSDT",
    "SHAZUSDT", "SHOPUSDT", "SKDDUSDT", "SKHYNIXUSDT", "SKHYUSDT", "SKUUUSDT", "SMCIUSDT",
    "SMHUSDT", "SNDKUSDT", "SNOWUSDT", "SNXXUSDT", "SOFIUSDT", "SONYUSDT", "SOXLUSDT",
    "SOXSUSDT", "SPCXUSDT", "SPYUSDT", "SQQQUSDT", "STRCUSDT", "STXXUSDT", "TBTUSDT",
    "TEAMUSDT", "TEMUSDT", "TENCENTUSDT", "TERUSDT", "TMFUSDT", "TQQQUSDT", "TSLAUSDT",
    "TSLLUSDT", "TSMUSDT", "TTWOUSDT", "TXNUSDT", "TZAUSDT", "UBERUSDT", "UNITREEUSDT",
    "URNMUSDT", "USARUSDT", "UVXYUSDT", "VRTUSDT", "VSTUSDT", "VUSDT", "WDCUSDT",
    "WENUSDT", "WMTUSDT", "XAGUSDT", "XAUUSDT", "XBIUSDT", "XLEUSDT", "XPDUSDT",
    "XPTUSDT", "ZHIPUUSDT", "ZHONGJIUSDT", "ZMUSDT", "ZSUSDT",
)

TRADFI_UNDERLYING = {"EQUITY", "HK_EQUITY", "COMMODITY", "KR_EQUITY", "CN_EQUITY", "PREMARKET"}


def normalize_scan_mode(value, default="ALL"):
    mode = str(value or default).upper().strip()
    if mode in {"BOTH", "SELECTED"}:
        return "ALL"
    if mode in {"ALL", "BINANCE", "TRADFI"}:
        return mode
    return default


def hardcoded_tradfi_symbols():
    return list(HARDCODED_TRADFI_USDT)


def _unique(symbols):
    out = []
    seen = set()
    for raw in symbols:
        symbol = str(raw or "").strip().upper()
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        out.append(symbol)
    return out


def _is_tradfi(item):
    if item.get("contractType") == "TRADIFI_PERPETUAL":
        return True
    if str(item.get("underlyingType") or "") in TRADFI_UNDERLYING:
        return True
    sub = item.get("underlyingSubType") or []
    return isinstance(sub, list) and "TradFi" in sub


def parse_binance_exchange_info(data):
    crypto = []
    tradfi = []
    for item in data.get("symbols") or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("status") or "").upper() != "TRADING":
            continue
        if item.get("quoteAsset") != "USDT":
            continue
        symbol = str(item.get("symbol") or "").upper()
        if not symbol.endswith("USDT"):
            continue
        if _is_tradfi(item):
            tradfi.append(symbol)
        elif item.get("contractType") == "PERPETUAL":
            crypto.append(symbol)
    return _unique(crypto), _unique(tradfi)


def fetch_binance_exchange_info(timeout=15.0):
    req = Request(BINANCE_FAPI_EXCHANGE_INFO, headers={"User-Agent": "crypto-bot-control/1.0"})
    try:
        with urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (URLError, TimeoutError, json.JSONDecodeError, OSError, ValueError) as exc:
        raise RuntimeError(f"Binance USD-M futures universe unavailable: {exc}") from exc


def fetch_binance_futures_universe(timeout=15.0):
    crypto_error = ""
    crypto = []
    live_tradfi = []
    try:
        data = fetch_binance_exchange_info(timeout=timeout)
        crypto, live_tradfi = parse_binance_exchange_info(data)
        if not crypto:
            crypto_error = "Binance USD-M futures universe unavailable: empty crypto list"
    except RuntimeError as exc:
        crypto_error = str(exc)
    tradfi = _unique(list(HARDCODED_TRADFI_USDT) + live_tradfi)
    return {
        "crypto": crypto,
        "tradfi": tradfi,
        "all": _unique(crypto + tradfi),
        "crypto_error": crypto_error,
        "tradfi_source": "binance+hardcoded" if live_tradfi else "hardcoded",
        "crypto_source": "binance" if crypto else "unavailable",
    }


def symbols_for_mode(universe, scan_mode):
    mode = normalize_scan_mode(scan_mode)
    if mode == "BINANCE":
        return list(universe.get("crypto") or [])
    if mode == "TRADFI":
        return list(universe.get("tradfi") or hardcoded_tradfi_symbols())
    return list(universe.get("all") or [])


def market_type_for(symbol, tradfi_set=None):
    name = str(symbol or "").upper()
    known = tradfi_set if tradfi_set is not None else set(HARDCODED_TRADFI_USDT)
    if name in known:
        return "US_TRADFI"
    return "BINANCE_FUTURES"
