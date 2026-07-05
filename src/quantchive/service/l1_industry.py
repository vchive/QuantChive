"""L1 行业分区派生（spec006 资金流向拓扑地基）。

一股属多个行业板块（申万 L1/L2/L3 三层），概念板块更是叠 12+ 个。要做守恒的
「大盘→行业→个股」桑基（Σ行业净额==大盘净额），每股必须**唯一归属**一个行业。

启发式：取该股所属行业中**成分股数最大**的那个（≈申万 L1 顶层）。实测 5596 股全覆盖、
塌缩为 31 个不重叠 L1 行业、每股唯一归属 Σ==大盘（见 test_l1_industry 守恒门禁）。

概念板块完全排除出可加拓扑（重叠严重，Σ≈市场数倍），只作旁路标签。
"""

from __future__ import annotations

import sqlite3


def build_l1_map(conn: sqlite3.Connection, *, as_of: str | None = None) -> dict[int, int]:
    """构建 stock_subject_id → L1_industry_subject_id 唯一归属映射。

    as_of 给定则只取该日有效的 membership（无前视，宪章 II）；None 取全部当前有效。
    返回 {股 subject_id: 行业 subject_id}，每股恰一个。
    """
    # 各行业板块成分股数（定"大小"）
    sector_size = {
        r[0]: r[1] for r in conn.execute(
            """SELECT m.parent_subject_id, COUNT(*)
               FROM subject_membership m JOIN subject s ON s.subject_id = m.parent_subject_id
               WHERE s.subject_kind = 'industry' GROUP BY m.parent_subject_id""").fetchall()
    }
    # 每股所属行业（as_of 过滤有效期）
    if as_of is not None:
        rows = conn.execute(
            """SELECT m.child_subject_id, m.parent_subject_id
               FROM subject_membership m JOIN subject s ON s.subject_id = m.parent_subject_id
               WHERE s.subject_kind = 'industry'
                 AND m.effective_from <= ?
                 AND (m.effective_to IS NULL OR m.effective_to > ?)""",
            (as_of, as_of)).fetchall()
    else:
        rows = conn.execute(
            """SELECT m.child_subject_id, m.parent_subject_id
               FROM subject_membership m JOIN subject s ON s.subject_id = m.parent_subject_id
               WHERE s.subject_kind = 'industry'""").fetchall()

    stock_inds: dict[int, list[int]] = {}
    for child, parent in rows:
        stock_inds.setdefault(child, []).append(parent)

    # 每股取成分数最大的行业（并列取 subject_id 最小，确定性）
    return {
        stock: max(inds, key=lambda p: (sector_size.get(p, 0), -p))
        for stock, inds in stock_inds.items()
    }
