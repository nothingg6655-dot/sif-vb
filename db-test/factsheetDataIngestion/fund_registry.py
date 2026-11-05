"""Canonical SIF fund names, fund houses, and cross-system mappings."""
import re
import unicodedata

FUND_HOUSES = {
    "Magnum Hybrid Long-Short Fund": "SBI Mutual Fund",
    "qsif Equity Long-Short Fund": "quant Mutual Fund",
    "qsif Hybrid Long-Short Fund": "quant Mutual Fund",
    "qsif Equity Ex-Top 100 Long-Short Fund": "quant Mutual Fund",
    "Altiva Hybrid Long-Short Fund": "Edelweiss Mutual Fund",
    "Diviniti Equity Long-Short Fund": "ITI Mutual Fund",
    "Titanium Hybrid Long-Short Fund": "Tata Mutual Fund",
    "Arudha Hybrid Long-Short Fund": "Bandhan Mutual Fund",
    "iSIF Hybrid Long-Short Fund": "ICICI Prudential Mutual Fund",
    "iSIF Equity Ex-Top 100 Long-Short Fund": "ICICI Prudential Mutual Fund",
    "DynaSIF Equity Long-Short Fund": "360 ONE Asset",
    "DynaSIF Active Asset Allocator Long-Short Fund": "360 ONE Asset",
    "Arudha Equity Long-Short Fund": "Bandhan Mutual Fund",
    "Apex Hybrid Long-Short Fund": "Aditya Birla Sun Life Mutual Fund",
    "Sapphire Equity Long-Short Fund": "Franklin Templeton Mutual Fund",
    "WSIF Equity Long-Short Fund": "The Wealth Company Mutual Fund",
    "WSIF Equity Ex-Top 100 Long-Short Fund": "The Wealth Company Mutual Fund",
    "Titanium Equity Long-Short Fund": "Tata Mutual Fund",
    "qsif Sector Rotation Long-Short Fund": "quant Mutual Fund",
    "Arthaya Equity Long-Short Fund": "Union Mutual Fund",
    "qsif Active Asset Allocator Long-Short Fund": "quant Mutual Fund",
    "Altiva Equity Ex-Top 100 Long-Short Fund": "Edelweiss Mutual Fund",
    "Platinum Hybrid Long-Short Fund": "Mirae Asset Mutual Fund",
    "iSIF Equity Long-Short Fund": "ICICI Prudential Mutual Fund",
    "iSIF Active Asset Allocator Long-Short Fund": "ICICI Prudential Mutual Fund",
    "RedHex Hybrid Long-Short Fund": "HSBC Mutual Fund",
    "DynaSIF Equity Ex-Top 100 Long-Short Fund": "360 ONE Asset",
    "Infinity Hybrid Long-Short Fund": "Kotak Mahindra Mutual Fund",
    "Prism Hybrid Long-Short Fund": "Jio BlackRock Mutual Fund",
    "Summit Equity Long-Short Fund": "Invesco Mutual Fund",
    "Magnum Equity Ex-Top 100 Long-Short Fund": "SBI Mutual Fund",
    "Apex Equity Long-Short Fund": "Aditya Birla Sun Life Mutual Fund",
    "Apex Equity Ex-Top 100 Long-Short Fund": "Aditya Birla Sun Life Mutual Fund",
    "Altiva Equity Long-Short Fund": "Edelweiss Mutual Fund",
    "Summit Active Asset Allocator Long-Short Fund": "Invesco Mutual Fund",
    "Eminence Hybrid Long-Short Fund": "AlphaGrep Mutual Fund",
}

# Mapping between TIGZIG brand names and official AMC names
TIGZIG_TO_AMC = {
    'DynaSIF SIF': '360 ONE Asset',
    'Apex SIF': 'Aditya Birla Sun Life Mutual Fund',
    'Arudha SIF': 'Bandhan Mutual Fund',
    'Altiva SIF': 'Edelweiss Mutual Fund',
    'Sapphire SIF': 'Franklin Templeton Mutual Fund',
    'RedHex SIF': 'HSBC Mutual Fund',
    'iSIF SIF': 'ICICI Prudential Mutual Fund',
    'Diviniti SIF': 'ITI Mutual Fund',
    'Infinity SIF': 'Kotak Mahindra Mutual Fund',
    'Magnum SIF': 'SBI Mutual Fund',
    'Prism SIF': 'Jio BlackRock Mutual Fund',
    'Titanium SIF': 'Tata Mutual Fund',
    'WSIF': 'The Wealth Company Mutual Fund',
    'SUMMIT SIF': 'Invesco Mutual Fund',
    'qsif SIF': 'quant Mutual Fund',
    'Arthaya SIF': 'Union Mutual Fund',
    'Platinum SIF': 'Mirae Asset Mutual Fund',
    'Eminence SIF': 'AlphaGrep Mutual Fund',
}


def normalize_name(value: str) -> str:
    value = unicodedata.normalize('NFKD', value or '').encode('ascii', 'ignore').decode().lower()
    return ' '.join(re.findall(r'[a-z0-9]+', value))


def slugify(value: str) -> str:
    return normalize_name(value).replace(' ', '-')


def canonical_fund_house(fund_name: str) -> str | None:
    norm = normalize_name(fund_name)
    for name, house in FUND_HOUSES.items():
        if normalize_name(name) == norm:
            return house
    return None
