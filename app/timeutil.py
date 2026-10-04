"""IANA 时区与“带 UTC 偏移的当地时间”校验工具（仅依赖标准库 zoneinfo）。

语义约定：
- 未指定时区的时间（naive，如 ``2026-10-01T10:00``）保持既有语义，不做任何
  时区解释；
- 指定 IANA 时区（如 ``Asia/Shanghai``、``America/New_York``）的事件，已填写的
  最早/最晚开始必须是带 UTC 偏移的当地时间（如 ``2026-11-01T01:30-05:00``）：
    * 偏移必须符合该时区当日规则；
    * 夏令时“重复时刻”（fall back 时同一墙上时间出现两次）允许当日有效的两个
      历史偏移中的任一个——由偏移本身区分两个绝对时刻；
    * “不存在的当地时刻”（spring forward 被跳过的墙上时间）或错误偏移一律拒绝，
      由调用方整次保存拒绝（HTTP 400），不写任何记录。
"""

import re
from datetime import timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_UTC = timezone.utc

# IANA 时区名白名单形态：字母开头，仅含字母数字与 +-_/ ，长度受限，
# 不允许出现 "."、".." 等可能被 ZoneInfo 当作路径片段的内容。
_TZ_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+\-_/]{0,63}$")


class TimeError(ValueError):
    """时区名非法或带偏移的当地时间不合法。"""


def get_zone(name):
    """返回 IANA 时区对象；名称非法或不存在时抛 TimeError。"""
    if not isinstance(name, str) or not _TZ_NAME_RE.match(name):
        raise TimeError("时区格式不正确（应为 IANA 时区名，如 Asia/Shanghai、America/New_York）")
    if any(part in ("", ".", "..") for part in name.split("/")):
        raise TimeError("时区格式不正确（应为 IANA 时区名，如 Asia/Shanghai、America/New_York）")
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        raise TimeError("「{}」不是有效的 IANA 时区".format(name))


def offset_text(offset):
    """timedelta 偏移 → ``+HH:MM`` / ``-HH:MM``。"""
    total = int(offset.total_seconds())
    sign = "+" if total >= 0 else "-"
    minutes = abs(total) // 60
    hh, mm = divmod(minutes, 60)
    return "{}{:02d}:{:02d}".format(sign, hh, mm)


def local_options(zone, wall):
    """墙上当地时间（naive）在该时区的全部合法解释，按 UTC 偏移去重。

    普通时刻返回 1 个 aware datetime；夏令时重复时刻返回 2 个（偏移不同，
    分别对应两次出现）；不存在的当地时刻返回空列表。

    zoneinfo 的 fold 只消歧“重复时刻”，对被跳过的 gap 不做归一化（墙上时间
    原样保留、却挂了另一侧的偏移），因此用“按该偏移经 UTC 再转回时区”的
    往返判定（不能直接 astimezone 回同一时区——那是恒等操作）：往返后墙上
    时间与偏移都一致，才算真实存在的当地时刻。
    """
    options = {}
    for fold in (0, 1):
        zdt = wall.replace(tzinfo=zone, fold=fold)
        back = zdt.astimezone(_UTC).astimezone(zone)
        if back.replace(tzinfo=None) == wall and back.utcoffset() == zdt.utcoffset():
            options.setdefault(zdt.utcoffset(), zdt)
    return list(options.values())


def format_aware(dt):
    """aware datetime → 分钟精度的“当地时间 + UTC 偏移”字符串。"""
    return dt.replace(tzinfo=None).isoformat(timespec="minutes") + offset_text(dt.utcoffset())


def format_in_zone(dt, zone):
    """按目标事件的时区展示绝对时刻；zone=None 时按旧语义展示 naive 时间。"""
    if zone is None:
        return dt.isoformat(timespec="minutes")
    return format_aware(dt.astimezone(zone))


def check_zoned(dt, zone, zone_name, event_name, label):
    """校验已解析的 aware datetime 是否为该时区当日合法的带偏移当地时间。

    合法则返回规范化字符串（分钟精度、保留其有效偏移）；
    不存在时刻或偏移不符合当日规则时抛 TimeError（供调用方整次拒绝）。
    """
    wall = dt.replace(tzinfo=None)
    wall_text = wall.isoformat(timespec="minutes")
    options = local_options(zone, wall)
    claimed = dt.utcoffset()
    if not options:
        raise TimeError(
            "事件「{ev}」的{label} {wall} 是时区「{tz}」中不存在的当地时刻"
            "（夏令时切换时该墙上时间被跳过）".format(
                ev=event_name, label=label, wall=wall_text, tz=zone_name))
    valid_offsets = sorted({o.utcoffset() for o in options})
    if claimed not in valid_offsets:
        if len(valid_offsets) == 2:
            hint = "该重复时刻的有效偏移为 {a} 或 {b}（夏令时重复时刻，请用偏移区分）".format(
                a=offset_text(valid_offsets[0]), b=offset_text(valid_offsets[1]))
        else:
            hint = "该时刻的有效偏移应为 {}".format(offset_text(valid_offsets[0]))
        raise TimeError(
            "事件「{ev}」的{label} {wall} 携带的偏移 {off} 不符合时区「{tz}」当日规则；{hint}"
            .format(ev=event_name, label=label, wall=wall_text,
                    off=offset_text(claimed), tz=zone_name, hint=hint))
    return format_aware(dt)
