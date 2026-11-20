"""Plain-language explanations for the failures this system actually produces.

The guard reports codes like ``E_SWEEP_EXPORT_INCOMPLETE``; the solver produces stalls that look
like "nothing happened for forty minutes". Neither is useful to the person running a design, and
both were left unexplained in earlier sessions - which is how the same failure got diagnosed three
times. This module turns a raw code or message into a record a page can show:

    code      the stable identifier, upper case
    title     a short sentence in the user's terms
    detail    what was observed
    action    what to do about it, or "nothing: it retries automatically"
    retryable whether a rerun on a fresh project copy can plausibly succeed
"""
from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from typing import Any

__all__ = ["Failure", "classify", "STALLED", "SESSION_POISONED", "UNKNOWN"]

STALLED = "STALLED"
SESSION_POISONED = "SESSION_POISONED"
UNKNOWN = "RUN_FAILED"

_RULES: list[tuple[re.Pattern[str], dict[str, Any]]] = [
    (re.compile(r"E_NOT_CONVERGED", re.I),
     {"title": "求解没有收敛",
      "detail": "本次运行没有提供通过收敛判据的证据，结果未作为有效实测。",
      "action": "检查本次收敛曲线、网格和端口；需要时增加自适应次数与时间预算。",
      "retryable": True}),
    (re.compile(r"E_SWEEP_EXPORT_INCOMPLETE", re.I),
     {"title": "扫频导出不完整",
      "detail": "导出的频点不足以验证带宽；仅凭短文件不能确定是求解还是导出故障。",
      "action": "核对 setup、sweep、参数与频率覆盖；确认已有完整解后，优先重导出。",
      "retryable": True}),
    (re.compile(r"E_FF_SPHERE_MISSING|sphere", re.I),
     {"title": "远场球设置缺失",
      "detail": "工程里没有可用的 Infinite Sphere，远场无从导出。",
      "action": "在 HFSS 里补一个远场球（或换用带远场球的设计副本）。",
      "retryable": False}),
    (re.compile(r"Failed to start new AEDT gRPC session on port 0", re.I),
     {"title": "AEDT 会话断开了",
      "detail": "未能连接到有效的 AEDT gRPC 会话，可能是连接参数失效或 worker 状态异常。",
      "action": "核对 AEDT PID 与端口；隔离故障 worker 后重新连接并检查已有解。",
      "retryable": True}),
    (re.compile(r"InsertFrequencySweep", re.I),
     {"title": "扫频设置没写进去",
      "detail": "工程副本里已有同名扫频，重建时 AEDT 拒绝。",
      "action": "用新的 campaign 目录重跑；系统已改为只配置一次扫频。",
      "retryable": True}),
    (re.compile(r"CreateReport", re.I),
     {"title": "报告建不出来",
      "detail": "AEDT 拒绝创建报告；需核对报告量、类别、解与远场上下文。",
      "action": "在独立 worker 中核对该上下文的可用报告量，再决定是否启用指标。",
      "retryable": False}),
    (re.compile(r"INVALID_PARAMS|invalid parameter", re.I),
     {"title": "参数超出范围",
      "detail": "提交的参数不在该 case 的允许区间内。",
      "action": "按参数说明调整；页面会标出越界的那一项。",
      "retryable": False}),
    (re.compile(r"MODEL_FAILED", re.I),
     {"title": "模型拒绝这组参数",
      "detail": "几何检查没通过（例如环外径小于同轴壁半径），求解器没有开始。",
      "action": "把参数移回可行区间；推荐功能会自动把搜索范围收窄到可行区。",
      "retryable": False}),
    (re.compile(r"GEOMETRY_REJECTED", re.I),
     {"title": "几何无法生成",
      "detail": "这组参数会产生自交或负尺寸的实体。",
      "action": "调整环径/环高组合；靠近上下界时先小步试探。",
      "retryable": False}),
    (re.compile(r"AEDT_CONNECT_FAILED", re.I),
     {"title": "AEDT 连接失败",
      "detail": "未建立可用连接，已停止本次建模与求解。",
      "action": "检查 AEDT PID、gRPC 端口与 worker 日志，恢复连接后重试。",
      "retryable": True}),
    (re.compile(r"LICENSE|licence|license|check ?out|entitlement|feature .*not available", re.I),
     {"title": "许可证不可用",
      "detail": "求解器或某个功能没有取到许可证（或许可证服务不可达）。",
      "action": "检查许可证服务器与 entitlement，确认没有其它任务占满后重试。",
      "retryable": True}),
    (re.compile(r"NO_AEDT|AMBIGUOUS_AEDT", re.I),
     {"title": "找不到可用的 AEDT",
      "detail": "没有运行中的 AEDT，或同时有多个无法判定。",
      "action": "在配置里指定 aedt_exe，或先手动启动一个 AEDT。",
      "retryable": False}),
    (re.compile(r"REAL_DISABLED", re.I),
     {"title": "当前是只读模式",
      "detail": "启动时没有打开真实求解。",
      "action": "用 --real 启动，或在页面里打开真实运行开关。",
      "retryable": False}),
    (re.compile(r"RECOVERY_REQUIRED", re.I),
     {"title": "需要人工恢复",
      "detail": "上一次尝试在求解器被杀后仍未停止，为避免同时跑两次求解已停止重试。",
      "action": "确认任务管理器里没有残留的 ansysedt/hf3d 进程，再重试该任务。",
      "retryable": True}),
    (re.compile(r"STALLED|no solver progress", re.I),
     {"title": "求解停滞",
      "detail": "进度监测或墙钟预算触发了保护；具体触发原因见本次运行记录。",
      "action": "检查进度证据、时间预算及进程清理结果，确认旧尝试结束后再重试。",
      "retryable": True}),
    (re.compile(r"timeout|timed out|budget", re.I),
     {"title": "超出时间预算",
      "detail": "单次运行超过了配置的 max_run_minutes。",
      "action": "提高预算或减少自适应次数；也可以先只跑 24–30 GHz 的粗扫。",
      "retryable": True}),
]


@dataclass
class Failure:
    code: str
    title: str
    detail: str
    action: str
    retryable: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def classify(error_code: str | None = None, detail: str | None = None) -> Failure:
    """Map a code (and the message it came with) onto the explanation table."""
    haystack = " ".join(part for part in (error_code or "", detail or "") if part)
    for pattern, entry in _RULES:
        if pattern.search(haystack):
            return Failure(code=(error_code or pattern.pattern).upper()[:40], **entry)
    if error_code:
        return Failure(code=str(error_code)[:40].upper(),
                       title="未分类的失败",
                       detail=(detail or "")[:300] or "没有更多信息",
                       action="把这条错误码连同 run id 一起记录下来再排查。",
                       retryable=False)
    return Failure(code=UNKNOWN, title="未分类的失败",
                   detail=(detail or "")[:300], action="检查守护日志。", retryable=False)
