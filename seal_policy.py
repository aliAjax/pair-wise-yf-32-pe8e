"""封签判定层：只负责生成与核验的纯逻辑，不接触数据库和 HTTP。

记录落库见 seal_log.py，页面交互见 static/index.html。
"""
from __future__ import annotations

import secrets
from dataclasses import dataclass

SEAL_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"  # 去掉 0/O、1/I 等易混淆字符
SEAL_LENGTH = 8


def normalize_seal(value: object) -> str | None:
    """把上报封签规整为大写去空白形式；无效输入返回 None。"""
    if not isinstance(value, str):
        return None
    return value.strip().upper() or None


def generate_seal(in_use: set[str]) -> str:
    """生成随机封签码，不与仍在流转的封签重复。"""
    while True:
        code = "".join(secrets.choice(SEAL_ALPHABET) for _ in range(SEAL_LENGTH))
        if code not in in_use:
            return code


@dataclass(frozen=True)
class Verdict:
    ok: bool
    result: str  # matched | missing | mismatch | duplicate
    message: str


def verify_seal(expected: str | None, reported: str, foreign_seals: set[str]) -> Verdict:
    """核验上报封签：一致放行；本单缺封签、撞其他在途单封签或不一致都拒绝。"""
    if not expected:
        return Verdict(False, "missing", "该分配尚未生成封签，需补发后才能交接")
    if reported == expected:
        return Verdict(True, "matched", "封签一致")
    if reported in foreign_seals:
        return Verdict(False, "duplicate", "上报封签属于其他在途分配，疑似容器调换")
    return Verdict(False, "mismatch", "上报封签与分配单封签不一致")
