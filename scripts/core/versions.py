"""语义化版本比较。纯函数零 IO。

注意 1.10.0 > 1.9.0，不能按字符串比较。
"""


def parse(version: str) -> tuple[int, ...]:
    parts = version.strip().lstrip("v").split(".")
    nums = []
    for p in parts[:3]:
        nums.append(int(p) if p.isdigit() else 0)
    while len(nums) < 3:
        nums.append(0)
    return tuple(nums)


def compare(a: str, b: str) -> int:
    """a > b 返回 1，相等返回 0，a < b 返回 -1。"""
    pa, pb = parse(a), parse(b)
    return (pa > pb) - (pa < pb)
