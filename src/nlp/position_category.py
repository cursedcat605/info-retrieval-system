"""岗位归类：把原始岗位/单位文本归入有限的几个"岗位类别"。

需求里的统计饼图（技术研发类 / 教育类 / 金融类 / 公务·事业单位 / 其他）
需要先有一个稳定的分类维度，否则原始 ``position`` 字段过于零散
（当前填充率仅约 15%），无法直接做比例统计。

判定顺序（先命中先返回，越靠前越"专指"）：

1. **教育类**：教师 / 辅导员 / 学校 / 教育局 …
2. **金融类**：银行 / 证券 / 保险 / 会计 / 财务 / 审计 / 财政 …
3. **技术研发类**：工程师 / 软件开发 / 数据 / 算法 / 计算机 …
4. **公务/事业单位**：政府 / 党委 / 纪委 / 乡镇 / 村 / 选调生 …（选调生的默认归属）
5. **其他**：既没有岗位、也没有单位时才落到这里。

两个关键设计（都是针对真实数据踩坑后改出来的）：

* **只用职位与单位做判定，不用学院**。学院名（"法学院""经济学院"）都落
  "学院"这个子串，把它放进匹配文本会把几乎所有记录都判成教育类；
* **有岗位/单位但是没命中具体规则时，默认归 "公务/事业单位"**，
  而不是 "其他"——选调生的身份本来就是公务员，归到 "其他" 会让饼图失真。
"""

from __future__ import annotations

from typing import Dict, Optional, Sequence, Tuple

#: 类别全集（顺序即展示顺序）
CATEGORIES: Tuple[str, ...] = (
    "公务/事业单位",
    "技术研发类",
    "教育类",
    "金融类",
    "其他",
)

OTHER = "其他"

#: 岗位/单位文本的触发词。顺序即优先级，越靠前越"专指"。
#:
#: 现实分布：``position`` 填充率仅约 15%，但 ``destination_org`` 接近 100%
#: （多为"XX省XX市人民政府办公室""XX市财政局"这类机关名），
#: 因此规则主要围绕**机关/单位名**设计，而不是围绕职务名。
RULES: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    (
        "教育类",
        (
            "教师", "老师", "辅导员", "讲师", "助教", "教授", "班主任", "教研",
            "教研室", "教育", "党校", "干部学院", "中学", "小学", "幼儿园",
            "学校", "大学", "学院", "书院", "职业技术", "高等专科",
            "招生办", "教务处", "培训", "支教",
        ),
    ),
    (
        "金融类",
        (
            "银行", "证券", "保险", "基金", "信托", "金融", "投资", "会计",
            "财务", "审计", "税务", "财政", "国资", "担保", "租赁", "交易所",
        ),
    ),
    (
        "技术研发类",
        (
            "工程师", "技术", "研发", "开发", "软件", "算法", "数据", "人工智能",
            "计算机", "信息化", "信息中心", "工业和信息化", "大数据", "网络",
            "运维", "测试", "产品经理", "架构", "系统", "通信", "电子",
            "自动化", "科研", "研究院", "研究所", "实验室", "设计院", "科技",
        ),
    ),
    (
        "公务/事业单位",
        (
            "政府", "党委", "党工委", "纪委", "监委", "纪检监察", "组织部",
            "宣传部", "统战部", "政法委", "人大", "政协", "法院", "检察",
            "公安", "司法", "公务员", "参公", "事业编", "事业单位", "选调",
            "街道", "乡镇", "乡村", "社区", "居委", "管委会", "管理局",
            "服务中心", "促进中心", "办公室", "委员会", "机关", "共青团",
            "妇联", "工会", "残联", "工商联", "红十字会", "发改", "住建",
            "交通", "民政", "人社", "水利", "农业", "农村", "林草", "应急",
            "市场监管", "生态环境", "文旅", "卫生健康", "医保", "自然资源",
            "城市管理", "综合执法", "编办", "机关事务",
        ),
    ),
)

#: 只能在*专业*上做弱推断时的触发词。
#:
#: 刻意把"学院/大学/学校"排除在外：**学院名不是岗位**，
#: 一旦用它做匹配，"法学院""经济学院"这类学院名会让几乎所有记录都被判成教育类
#: （这正是初版实现踩过的坑：155 条里 152 条变成教育类）。
WEAK_RULES: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    (
        "教育类",
        ("教育学", "师范", "学前教育", "学科教学", "课程与教学", "教育技术"),
    ),
    (
        "金融类",
        ("金融", "会计", "财务管理", "审计", "统计", "经济", "保险", "国际贸易"),
    ),
    (
        "技术研发类",
        (
            "计算机", "软件", "信息", "电子", "通信", "自动化", "数据科学",
            "人工智能", "网络工程", "工程", "数学",
        ),
    ),
)


def _first_match(
    text: str, rules: Tuple[Tuple[str, Tuple[str, ...]], ...]
) -> Tuple[Optional[str], Optional[str]]:
    """返回 ``(类别, 命中的触发词)``；都不命中时返回 ``(None, None)``。"""
    for category, keywords in rules:
        for keyword in keywords:
            if keyword in text:
                return category, keyword
    return None, None


def _job_text(position: Optional[str], destination_org: Optional[str]) -> str:
    """岗位判断只使用"与工作相关"的字段：职务 + 单位。"""
    return " ".join(part for part in (position, destination_org) if part).strip()


def classify(
    position: Optional[str] = None,
    destination_org: Optional[str] = None,
    major: Optional[str] = None,
    college: Optional[str] = None,
) -> str:
    """给一条记录归类到 :data:`CATEGORIES` 之一。

    判定顺序：

    1. 有 ``position`` / ``destination_org`` → 按 :data:`RULES` 命中更具体的类别；
       没有任何具体信号时**默认 ``公务/事业单位``**（选调生的身份本来就是公务员），
       这比一律回落到"其他"更能反映真实分布；
    2. 连岗位和单位都没有 → 只用 ``major`` 按 :data:`WEAK_RULES` 弱推断；
    3. 都推不出来 → :data:`OTHER`。

    :param college: **有意不参与判定**。学院名（"XX学院"）与岗位无关，
        用它做匹配会让教育类吞掉几乎所有记录。保留该参数只为调用方签名稳定。
    """
    return classify_detail(position, destination_org, major, college)["category"]


def classify_detail(
    position: Optional[str] = None,
    destination_org: Optional[str] = None,
    major: Optional[str] = None,
    college: Optional[str] = None,  # noqa: ARG001 - 见 classify 的说明
) -> Dict[str, Optional[str]]:
    """返回 ``{"category", "evidence", "basis"}``，便于排查误判。

    ``basis`` 表示结论来源：``"岗位/单位"`` / ``"默认"`` / ``"专业"`` / ``None``。
    """
    job = _job_text(position, destination_org)
    if job:
        category, keyword = _first_match(job, RULES)
        if category:
            return {"category": category, "evidence": keyword, "basis": "岗位/单位"}
        return {
            "category": "公务/事业单位",
            "evidence": "无更具体的岗位信号",
            "basis": "默认",
        }

    major_text = (major or "").strip()
    if major_text:
        category, keyword = _first_match(major_text, WEAK_RULES)
        if category:
            return {"category": category, "evidence": keyword, "basis": "专业"}

    return {"category": OTHER, "evidence": None, "basis": None}


# --------------------------------------------------------------------------- #
# 学历层次归一化：本科 / 硕士 / 博士
# --------------------------------------------------------------------------- #
#: 展示用学历层次（需求里的筛选维度）
DEGREE_LEVELS: Tuple[str, ...] = ("本科", "硕士", "博士")

_DEGREE_RULES: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("博士", ("博士", "博研", "博士后", "phd")),
    ("硕士", ("硕士", "研究生", "攻读", "mba", "mpa", "master")),
    ("本科", ("本科", "学士", "大学本科", "应届毕业生", "bachelor")),
)


def normalize_degree(degree: Optional[str]) -> Optional[str]:
    """把 ``硕士研究生`` / ``学士`` / ``博士研究生`` 归一为 ``硕士`` / ``本科`` / ``博士``。

    ``degree`` 为空时返回 ``None``（不做任何推测）。
    """
    text = (degree or "").strip().lower()
    if not text:
        return None
    for level, keywords in _DEGREE_RULES:
        if any(keyword in text for keyword in keywords):
            return level
    return None


def category_order(categories: Sequence[str]) -> Tuple[str, ...]:
    """按 :data:`CATEGORIES` 的固定顺序排列给定类别。"""
    known = [c for c in CATEGORIES if c in categories]
    extra = [c for c in categories if c not in CATEGORIES]
    return tuple(known + extra)


__all__ = [
    "CATEGORIES",
    "DEGREE_LEVELS",
    "OTHER",
    "RULES",
    "WEAK_RULES",
    "category_order",
    "classify",
    "classify_detail",
    "normalize_degree",
]
