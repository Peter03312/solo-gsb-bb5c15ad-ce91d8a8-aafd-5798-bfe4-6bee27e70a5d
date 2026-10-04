"""跨地区时区单元测试：python tests/test_timezones.py

覆盖：
- timeutil：IANA 名校验、普通时刻偏移校验、春令时不存在时刻拒绝、
  夏令时重复时刻两个有效偏移、错误偏移拒绝、跨区展示；
- compute：跨时区关系按绝对分钟推算（持续时长、最短/最长等待），
  结果按各事件当地时间 + 偏移展示；DST 重复时刻由偏移区分；
  带时区/未带时区事件关系不参与推算（防御层）。
"""
import pathlib
import sys
from datetime import datetime

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "app"))

import compute  # noqa: E402
import timeutil  # noqa: E402


NY = "America/New_York"
SH = "Asia/Shanghai"


def zev(i, name, tz, es, ls, dur):
    return {"id": i, "name": name, "timezone": tz,
            "earliest_start": es, "latest_start": ls, "duration_minutes": dur}


def zrel(i, a, b, min_g=None, max_g=None):
    r = {"id": i, "predecessor_id": a, "successor_id": b}
    if min_g is not None:
        r["min_gap_minutes"] = min_g
    if max_g is not None:
        r["max_gap_minutes"] = max_g
    return r


# --------------------------------------------------------------- timeutil

def test_get_zone_rejects_bad_names():
    for bad in ("Foo/Bar", "../etc/passwd", "", None, "Asia/..", " Asia/Shanghai"):
        try:
            timeutil.get_zone(bad)
        except timeutil.TimeError:
            continue
        raise AssertionError("应当拒绝时区名: {!r}".format(bad))
    assert timeutil.get_zone(SH) is not None


def test_normal_time_offset_must_match():
    ny = timeutil.get_zone(NY)
    # 6 月纽约为 EDT(-04)
    assert timeutil.check_zoned(
        datetime.fromisoformat("2026-06-01T12:00-04:00"), ny, NY, "A", "最早开始") \
        == "2026-06-01T12:00-04:00"
    try:
        timeutil.check_zoned(
            datetime.fromisoformat("2026-06-01T12:00-05:00"), ny, NY, "A", "最早开始")
    except timeutil.TimeError as e:
        assert "-04:00" in str(e)
    else:
        raise AssertionError("普通时刻错误偏移应拒绝")


def test_spring_forward_nonexistent_rejected():
    ny = timeutil.get_zone(NY)
    # 2026-03-08 02:30 在美东春令时被跳过：挂任何偏移都不合法
    for off in ("-05:00", "-04:00"):
        try:
            timeutil.check_zoned(
                datetime.fromisoformat("2026-03-08T02:30" + off), ny, NY, "A", "最早开始")
        except timeutil.TimeError as e:
            assert "不存在的当地时刻" in str(e)
        else:
            raise AssertionError("春令时不存在时刻 {} 应拒绝".format(off))
    # 跳过后的 03:30-04 合法
    assert timeutil.check_zoned(
        datetime.fromisoformat("2026-03-08T03:30-04:00"), ny, NY, "A", "最早开始")


def test_fall_back_repeated_time_distinguished_by_offset():
    ny = timeutil.get_zone(NY)
    # 2026-11-01 01:30 出现两次：EDT(-04) 与 EST(-05)，均合法且是不同绝对时刻
    a = datetime.fromisoformat("2026-11-01T01:30-04:00")
    b = datetime.fromisoformat("2026-11-01T01:30-05:00")
    assert timeutil.check_zoned(a, ny, NY, "A", "最早开始") == "2026-11-01T01:30-04:00"
    assert timeutil.check_zoned(b, ny, NY, "A", "最早开始") == "2026-11-01T01:30-05:00"
    assert a.astimezone(timeutil._UTC) != b.astimezone(timeutil._UTC)  # 相差 60 分钟
    # 该重复时刻的其它偏移（如 +00）必须拒绝
    try:
        timeutil.check_zoned(
            datetime.fromisoformat("2026-11-01T01:30+00:00"), ny, NY, "A", "最早开始")
    except timeutil.TimeError as e:
        assert "-05:00" in str(e) and "-04:00" in str(e)
    else:
        raise AssertionError("重复时刻的无效偏移应拒绝")


def test_format_in_zone_localizes_absolute_instant():
    dt = datetime.fromisoformat("2026-11-01T01:30-05:00")  # 纽约 EST
    ny, sh = timeutil.get_zone(NY), timeutil.get_zone(SH)
    assert timeutil.format_in_zone(dt, ny) == "2026-11-01T01:30-05:00"
    assert timeutil.format_in_zone(dt, sh) == "2026-11-01T14:30+08:00"
    # naive 保持旧格式
    assert timeutil.format_in_zone(datetime.fromisoformat("2026-10-01T10:00"), None) \
        == "2026-10-01T10:00"


# --------------------------------------------------------------- compute

def test_cross_zone_absolute_propagation():
    # 上海告警 12:00~13:00(+08) 持续 60 分钟；纽约响应 00:00~05:00(EST) 持续 30；
    # 关系无额外等待。
    # 最早：纽约 ≥ 上海最早结束 13:00+08 = 01:00-04(=00:00 EST)；
    # 最晚：上海 ≤ 纽约最晚开始 05:00-05 减 60 分钟 = 上海 13:00+08，恰好不收紧。
    events = [
        zev(1, "上海告警", SH, "2026-11-01T12:00+08:00", "2026-11-01T13:00+08:00", 60),
        zev(2, "纽约响应", NY, "2026-11-01T00:00-04:00", "2026-11-01T05:00-05:00", 30),
    ]
    res = compute.compute(events, [zrel(1, 1, 2)])
    assert res["status"] == "ok", res
    w1, w2 = res["windows"][1], res["windows"][2]
    assert w1["timezone"] == SH and w2["timezone"] == NY
    # 12:00+08 + 60 = 13:00+08 = 01:00-04（当日仍为 EDT）
    assert w2["earliest_start"] == "2026-11-01T01:00-04:00", w2["earliest_start"]
    # 纽约最晚输入 05:00-05 反推上海 -60 分钟：04:00-05 = 17:00+08；上海输入 13:00 更紧
    assert w1["latest_start"] == "2026-11-01T13:00+08:00"
    # 依据链按各事件当地时间+偏移展示
    text = w2["earliest_chain"][-1]["text"]
    assert "上海告警" in text and "纽约响应" in text and "01:00-04:00" in text


def test_cross_zone_min_max_gaps_absolute():
    # 上海固定 2026-06-01 12:00+08（=纽约 00:00-04，夏季全为 EDT）持续 0；
    # 纽约窗口 6/1 00:00~06:00(-04)；关系 最短等待 60、最长等待 120。
    # 最早 00:00+60=01:00-04；最晚 00:00+120=02:00-04。
    events = [
        zev(1, "A", SH, "2026-06-01T12:00+08:00", "2026-06-01T12:00+08:00", 0),
        zev(2, "B", NY, "2026-06-01T00:00-04:00", "2026-06-01T06:00-04:00", 0),
    ]
    res = compute.compute(events, [zrel(1, 1, 2, min_g=60, max_g=120)])
    assert res["status"] == "ok", res
    w = res["windows"][2]
    assert w["earliest_start"] == "2026-06-01T01:00-04:00", w
    assert w["latest_start"] == "2026-06-01T02:00-04:00", w


def test_end_window_localized_across_dst():
    # 纽约 00:30-04 开始持续 60 分钟 → 结束 01:30-04（重复时刻的较早一次）
    events = [zev(1, "B", NY, "2026-11-01T00:30-04:00", "2026-11-01T00:30-04:00", 60)]
    res = compute.compute(events, [])
    w = res["windows"][1]
    assert w["earliest_end"] == "2026-11-01T01:30-04:00", w
    assert w["latest_end"] == "2026-11-01T01:30-04:00", w


def test_zoned_vs_naive_relation_skipped_defensively():
    # 保存层已拒绝；引擎层防御：混建关系不参与推算，只算各自输入范围。
    events = [
        zev(1, "上海", SH, "2026-11-01T12:00+08:00", "2026-11-01T13:00+08:00", 10),
        {"id": 2, "name": "本地", "earliest_start": "2026-11-01T12:00",
         "latest_start": "2026-11-01T13:00", "duration_minutes": 10},
    ]
    res = compute.compute(events, [zrel(1, 1, 2)])
    assert len(res["skipped_relations"]) == 1
    assert "时区" in res["skipped_relations"][0]["reason"]
    # 两事件各自窗口不被关系改写
    assert res["windows"][2]["earliest_start"] == "2026-11-01T12:00"
    assert res["windows"][1]["earliest_start"] == "2026-11-01T12:00+08:00"


def test_fold_offsets_produce_distinct_absolute_ordering():
    # 跨夏令时回退：A=00:30-04（EDT），B=01:30-05（EST，重复时刻的第二次）。
    # 墙上 00:30→01:30 仅 +60 分钟，但绝对时刻 04:30Z→06:30Z 相差 120 分钟。
    # A 先于 B，A 持续 90 分钟：04:30Z+90=06:00Z ≤ 06:30Z，按绝对分钟可行；
    # 若错误地按墙上分钟（仅差 60 < 90）则会误判冲突。
    events = [
        zev(1, "A", NY, "2026-11-01T00:30-04:00", "2026-11-01T00:30-04:00", 90),
        zev(2, "B", NY, "2026-11-01T01:30-05:00", "2026-11-01T01:30-05:00", 0),
    ]
    res = compute.compute(events, [zrel(1, 1, 2)])
    assert res["status"] == "ok", res
    # B 的最早开始被 A 结束（06:00Z = 01:00-05）抬高，但输入 01:30-05 更晚，窗口不变
    assert res["windows"][2]["earliest_start"] == "2026-11-01T01:30-05:00"


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

if __name__ == "__main__":
    failed = 0
    for t in TESTS:
        try:
            t()
            print("PASS", t.__name__)
        except AssertionError as e:
            failed += 1
            print("FAIL", t.__name__, "->", e)
    sys.exit(1 if failed else 0)
