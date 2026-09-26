#!/usr/bin/env python3
"""封签判定层（纯规则）：只负责封签生成、核验判定与状态汇总，不访问数据库或 HTTP。

记录层（app.Repository）负责持久化双方上报的封签与时刻，页面层（static/index.html）
只渲染本层给出的结论，判定规则集中在本模块维护。
"""
from __future__ import annotations

import secrets
from typing import Any, Mapping, Sequence

SEAL_PREFIX = "SEAL-"

# 交接两个环节：来源医院发起（离场）与接收医院确认（到场）
DEPARTURE = "departure"
ARRIVAL = "arrival"
STAGES = (DEPARTURE, ARRIVAL)

# 核验结论
MATCHED = "matched"        # 与转运封签一致
MISMATCH = "mismatch"      # 封签码不一致（容器可能被调换）
DUPLICATE = "duplicate"    # 封签重复：该码已在其他分配的通过记录中使用
EXPIRED = "expired"        # 器官已过期：只留拒绝记录，不得交接
REJECTIONS = (MISMATCH, DUPLICATE, EXPIRED)

# 协调台封签汇总状态
ABSENT = "absent"          # 尚未进入转运，无封签
PENDING = "pending"        # 转运中但缺封签，待补
SEALED = "sealed"          # 转运开始已生成封签，等待双方核验
VERIFIED = "verified"      # 接收方核验通过


class SealPolicy:
    """无状态判定规则：输入数据，输出结论；所有方法均为静态纯函数。"""

    @staticmethod
    def generate() -> str:
        """转运开始后生成随机一次性封签码。"""
        return SEAL_PREFIX + secrets.token_hex(5).upper()

    @staticmethod
    def normalize(code: Any) -> str:
        return str(code).strip() if code is not None else ""

    @staticmethod
    def judge(*, reported: str, expected: str | None, expired: bool, duplicate: bool) -> str:
        """核验单个环节上报的封签。过期优先，其次重复，最后比对一致性。"""
        if expired:
            return EXPIRED
        if duplicate:
            return DUPLICATE
        if not reported or reported != (expected or ""):
            return MISMATCH
        return MATCHED

    @staticmethod
    def is_rejection(result: str) -> bool:
        return result in REJECTIONS

    @staticmethod
    def summarize(expected: str | None, allocation_status: str,
                  verifications: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        """汇总某分配的封签情况供协调台展示，不在页面层重复判定。

        verifications 为按时间先后排列的上报记录，最新一条代表该环节当前结论；
        任何拒绝记录都会让该分配进入差异清单（即使后来补验通过也保留痕迹）。
        """
        latest: dict[str, Mapping[str, Any]] = {}
        rejections: list[Mapping[str, Any]] = []
        for item in verifications:
            latest[item["stage"]] = item
            if item["result"] != MATCHED:
                rejections.append(item)
        departure, arrival = latest.get(DEPARTURE), latest.get(ARRIVAL)
        if not expected:
            state = PENDING if allocation_status == "in_transit" else ABSENT
        elif arrival is not None and arrival["result"] == MATCHED:
            state = VERIFIED
        else:
            state = SEALED
        return {
            "seal_code": expected,
            "state": state,
            "pending": state == PENDING,
            "discrepancy": bool(rejections),
            "departure": dict(departure) if departure else None,
            "arrival": dict(arrival) if arrival else None,
            "rejections": [dict(item) for item in rejections],
        }
