"""Durable workspace inventory, deterministic map and bounded initial orientation.

A workspace receipt is not a claim to have read the whole repository. Worker
receipts may change on recovery; the revision-bound exploration ledger does not.
"""
from __future__ import annotations

import copy
import json
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from .operations import OperationStore
from .workspace import _atomic_json, _inventory, _load_json, revision, sync

SCHEMA_VERSION = 3
STAGES = ("scan", "map", "extract", "index", "explore", "graph")
MAP_SCHEMA_VERSION = 2
EXPLORATION_LIMIT = 12
MAX_REVISION_PASSES = 2


def _path(root):
    return Path(root).resolve() / "meetingvar" / "agent_memory" / "workspace_job.json"


def snapshot(project_root, job_id="") -> dict:
    """Read a receipt without starting work, creating files or renewing a budget."""
    root = Path(project_root).resolve()
    state = _load_json(_path(root))
    if state and state.get("project") != str(root):
        return {"ok": False, "error": "工作区项目身份不匹配", "project": str(root)}
    if job_id and state.get("job_id") != job_id:
        return {"ok": False, "error": "工作区任务已更替", "project": str(root), "job_id": job_id}
    result = {"ok": True, "project": str(root), "folder": str(root),
              "job_id": "", "job_status": "idle", "running": False,
              "observed_revision": "", "processed_revision": "", "first_sync": True,
              "stages": {}, "totals": {}, "map_totals": {}, "coverage": {},
              "errors": [], "map_status": "not_built", "exploration_status": "not_started", **state}
    current = result.get("schema_version") == SCHEMA_VERSION
    # Old summarize/completed receipts cannot certify this algorithm's orientation.
    result["first_sync"] = not (current and result.get("first_sync_completed") is True)
    result["phase_complete"] = bool(current and result.get("phase_complete"))
    result["complete"] = False
    result["semantic_exhaustive"] = False
    if state and not current:
        result.update(job_status="needs_orientation", phase_complete=False)
    return result


@contextmanager
def _lock(root):
    path = _path(root).with_suffix(".lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    stream = path.open("a+b")
    acquired = False
    try:
        stream.seek(0, 2)
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except (OSError, BlockingIOError):
            pass
        yield acquired
    finally:
        if acquired:
            stream.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_UN)
        stream.close()


def _errors(state):
    rows, seen = [], set()
    for stage, detail in state.get("stages", {}).items():
        for value in detail.get("errors") or []:
            row = dict(value) if isinstance(value, dict) else {"message": str(value)}
            row.setdefault("stage", stage)
            key = json.dumps(row, sort_keys=True, ensure_ascii=False, default=str)
            if key not in seen:
                rows.append(row)
                seen.add(key)
    for row in state.get("job_errors") or []:
        if row not in rows:
            rows.append(row)
    return rows


def _save(root, state):
    state["updated_at"] = time.time()
    state["first_sync"] = not (state.get("schema_version") == SCHEMA_VERSION
                              and state.get("first_sync_completed") is True)
    state["complete"] = False
    state["semantic_exhaustive"] = False
    state["errors"] = _errors(state)
    _atomic_json(_path(root), state, fsync=False)


def _archive_exploration(state, reason):
    """Keep old usage and reasons as audit, never recycle them into a new operation."""
    keys = ("model_operation", "model_budget", "exploration_checkpoint", "exploration", "summary")
    if any(state.get(key) for key in keys):
        archive = {key: copy.deepcopy(state[key]) for key in keys if key in state}
        archive.update(archive_reason=reason, archived_at=time.time(),
                       schema_version=state.get("schema_version", 0),
                       errors=copy.deepcopy(state.get("errors") or []))
        state.setdefault("exploration_history", []).append(archive)
    for key in (*keys, "exploration_id", "summarized"):
        state.pop(key, None)
    state.update(phase_complete=False, exploration_status="not_started")


def _migrate(state):
    if state.get("schema_version") == SCHEMA_VERSION:
        return False
    reason = "legacy_unbound_budget" if state.get("model_budget") and not state.get("model_operation") else "schema_migration"
    _archive_exploration(state, reason)
    state.update(schema_version=SCHEMA_VERSION, stages={}, coverage={},
                 processed_revision="", first_sync_completed=False, job_errors=[])
    return True


def _model_ready():
    # No implicit provider/fallback. Desktop supplies its explicit configuration.
    if os.environ.get("MEETINGVAR_WORKSPACE_MODEL_READY") != "1":
        return False
    from ..llm import client_profile
    try:
        client_profile()
        return True
    except Exception:
        return False


def _ready(value=None, client=None):
    if callable(value):
        return bool(value())
    return bool(value) if value is not None else (client is not None or _model_ready())


def start(project_root, *, launch=None, force=False, resume=False, model_ready=None) -> dict:
    """Attach by default; only changed sources, first orientation or explicit retry run.

    force starts a new investigation. resume retries the SAME operation with its
    remaining budget; periodic callers never set either flag.
    """
    root = Path(project_root).resolve()
    if not root.is_dir():
        return {"ok": False, "error": "项目文件夹无效"}
    with _lock(root) as acquired:
        state = snapshot(root)
        if not state.get("ok") or not acquired:
            return {**state, "attached": True}
        if state.get("job_status") == "pending" and time.time() - state.get("updated_at", 0) < 30:
            return {**state, "attached": True}
        store = OperationStore(root)
        store.reconcile_orphans()
        prior = store.get(state.get("operation_id", ""))
        if prior and prior["status"] in {"running", "cancel_requested"}:
            return {**state, "attached": True}
        migrated = _migrate(state)
        changed = False
        if state.get("observed_revision"):
            try:
                changed = revision(_inventory(root)) != state["observed_revision"]
            except OSError:
                # A failed observation must not spend a new model budget.
                if not (force or resume or migrated):
                    return {**state, "attached": True}
        waiting_ready = (state.get("exploration_status") in {"awaiting_model", "mapped_awaiting_model"}
                         and state.get("model_ready") is not True and _ready(model_ready))
        recovering = state.get("job_status") in {"pending", "running"}
        if (state.get("job_id") and not (force or resume or migrated or changed or waiting_ready or recovering)):
            return {**state, "attached": True}
        job_id = uuid4().hex if force or changed or not state.get("job_id") else state["job_id"]
        operation = store.create("workspace_refresh", {"project": str(root), "job_id": job_id,
                                 "force": bool(force), "resume": bool(resume)},
                                 run_id=job_id, origin="workspace")
        state.update(ok=True, job_id=job_id, operation_id=operation["id"], project_id=store.project_id,
                     job_status="pending", running=True, force=bool(force or state.get("force")))
        _save(root, state)
    if launch:
        try:
            launch(root, job_id)
        except Exception as exc:
            with _lock(root) as acquired:
                if acquired and snapshot(root).get("operation_id") == operation["id"]:
                    state.update(job_status="failed", running=False, job_errors=[{
                        "code": "launch_failed", "message": str(exc), "retryable": True}])
                    store.claim(operation["id"])
                    store.finish(operation["id"], {"ok": False, "errors": state["job_errors"], "job_id": job_id})
                    _save(root, state)
            return {**state, "ok": False}
    return state


def _index(root, progress):
    from .. import config
    from ..rag import RagIndex
    return RagIndex(root, config.DATA_DIR).index_project(include_meetings=False, progress=progress)


def _map(root, *, force=False, progress=None):
    from ..memory.repository_map import build_map
    return build_map(root, force=force, progress=progress)


def _map_snapshot(root):
    from ..memory.repository_map import read_map
    return read_map(root, limit=1, include_claims=False)


def _explore(root, **kwargs):
    from ..memory.repository_explorer import explore
    return explore(root, **kwargs)


def _graph(root, progress):
    from .. import config
    from ..kg.build import GraphBuilder
    from ..kg.map_graph import build_map_graph
    facts = GraphBuilder(root, config.DATA_DIR).build(include_meetings=False, reset=False)
    mapped = build_map_graph(root)
    errors = list(facts.get("errors") or []) + list(mapped.get("errors") or [])
    return {**facts, "ok": facts.get("ok") is not False and mapped.get("ok") is True,
            "complete": mapped.get("complete") is True and not errors,
            "errors": errors, "map_graph": mapped,
            "nodes": mapped.get("stats", {}).get("nodes", facts.get("nodes", 0)),
            "edges": mapped.get("stats", {}).get("edges", facts.get("edges", 0))}


def _budget():
    from .policy import AgentBudget
    # Same finite token/time defaults as other model tasks, never an unlimited scan.
    # These are task ceilings, not pre-spent quota. They are sized for a full
    # project-cognition pass while each model action remains individually
    # capped in the explorer.
    return AgentBudget(max_seconds=6 * 3600, max_input_tokens=20_000_000,
                       max_output_tokens=1_000_000)


def _bind_exploration(state, map_revision, *, force=False):
    previous = state.get("model_operation") or {}
    matching = (previous.get("exploration_id") and previous.get("operation_id") == previous["exploration_id"]
                and previous.get("source_revision") == map_revision
                and previous.get("map_revision") == map_revision
                and previous.get("observed_revision") == state["observed_revision"])
    if matching and not force:
        return
    reason = "explicit_force" if force else ("source_revision_changed" if previous else "legacy_unbound_budget")
    _archive_exploration(state, reason)
    ident = uuid4().hex
    state["exploration_id"] = ident
    state["model_operation"] = {"operation_id": ident, "exploration_id": ident,
                                "source_revision": map_revision, "map_revision": map_revision,
                                "observed_revision": state["observed_revision"]}
    state["model_budget"] = _budget().as_dict()
    state.setdefault("stages", {})["explore"] = {"status": "pending", "errors": []}


def _count(value):
    return len(value) if isinstance(value, (list, tuple, dict)) else max(0, int(value or 0))


def _exploration_coverage(report):
    coverage = report.get("coverage") or report
    complete = coverage.get("complete") is True
    exhaustive = complete and coverage.get("exhaustive") is True
    return {"scope": coverage.get("scope") or "targeted_repository_exploration",
            **{key: _count(coverage.get(key)) for key in ("selected", "processed", "failed", "pending")},
            "complete": complete, "exhaustive": exhaustive,
            "semantic_exhaustive": exhaustive}


def run(project_root, job_id, *, indexer=None, mapper=None, explorer=None, grapher=None,
        model_client=None, model_ready=None, progress=None):
    root = Path(project_root).resolve()
    with _lock(root) as acquired:
        if not acquired:
            return {**snapshot(root), "attached": True}
        state = snapshot(root, job_id)
        if not state.get("ok") or not state.get("operation_id"):
            return state
        store = OperationStore(root)
        operation = store.get(state["operation_id"])
        if not operation or operation["status"] not in {"pending", "approved"}:
            return state
        store.claim(operation["id"])
        _migrate(state)
        stopped = threading.Event()

        def heartbeat():
            while not stopped.wait(20):
                try:
                    store.heartbeat(operation["id"])
                except Exception:
                    return

        thread = threading.Thread(target=heartbeat, daemon=True)
        thread.start()
        state.update(job_status="running", running=True, job_errors=[])
        stages = state.setdefault("stages", {})

        def emit(stage, status, **fields):
            row = stages.setdefault(stage, {})
            row.update(status=status, observed_revision=state.get("observed_revision", ""), **fields)
            if status in {"completed", "ready"}:
                row["last_succeeded_revision"] = state.get("observed_revision", "")
            state["stage"] = stage
            _save(root, state)
            if progress:
                progress({"type": "workspace_status", **state})

        def fail(stage, exc):
            emit(stage, "failed", errors=[{"stage": stage, "code": type(exc).__name__,
                                           "message": str(exc), "retryable": True}])

        def done(stage):
            row = stages.get(stage, {})
            return (row.get("status") in {"completed", "ready"}
                    and row.get("last_succeeded_revision") == state.get("observed_revision"))

        try:
            force = bool(state.get("force", False))
            for attempt in range(MAX_REVISION_PASSES):
                emit("scan", "running")
                try:
                    documents = _inventory(root)
                    observed_revision = revision(documents)
                    if observed_revision != state.get("observed_revision") or force:
                        # Archive before clearing stage errors. The pending force
                        # flag remains durable until the fresh operation is bound.
                        _archive_exploration(state, "explicit_force" if force else "source_revision_changed")
                        stages.clear()
                        stages.update({stage: {"status": "pending", "errors": []} for stage in STAGES})
                        state.update(coverage={}, extracted=0, phase_complete=False,
                                     map_status="not_built", exploration_status="not_started")
                    state["observed_revision"] = observed_revision
                    state["discovered"] = len(documents)
                    state["source_refs"] = [{"path": key, "revision": row["sha256"]} for key, row in documents.items()]
                    state["workspace"] = sync(root, documents=documents)
                    emit("scan", "completed", count=len(documents), errors=[])
                except Exception as exc:
                    fail("scan", exc)
                    break

                if not done("map") or int(state.get("map_version") or 0) != MAP_SCHEMA_VERSION:
                    emit("map", "running")
                    try:
                        report = (mapper or _map)(root, force=force, progress=lambda p: emit("map", "running", progress=p))
                        state["map_report"] = {key: report[key] for key in
                            ("ok", "map_status", "map_revision", "totals", "coverage", "errors") if key in report}
                        state["map_status"] = report.get("map_status", "failed")
                        state["map_version"] = report.get("map_version", 0)
                        state["map_revision"] = report.get("map_revision", "")
                        state["map_totals"] = report.get("totals") or {}
                        state["coverage"]["map"] = {**(report.get("coverage") or {}), "semantic_exhaustive": False}
                        errors = report.get("errors") or []
                        if not state["map_revision"] or state["map_status"] not in {"complete", "partial"}:
                            errors = errors or [{"code": "map_unavailable", "message": "地图尚不可用，等待重新调查"}]
                        emit("map", "completed" if state["map_status"] == "complete" and state["map_revision"] else
                             (state["map_status"] if state["map_status"] in {"partial", "busy"} else "failed"), errors=errors)
                    except Exception as exc:
                        state["map_status"] = "failed"
                        fail("map", exc)

                if not done("index"):
                    emit("extract", "running")
                    emit("index", "running")
                    try:
                        report = (indexer or _index)(root, lambda p: emit("extract", "running", progress=p))
                        state["index_report"] = report
                        if isinstance(report.get("totals"), dict):
                            state["totals"] = report["totals"]  # Postcommit totals, never deltas/max.
                        coverage = report.get("coverage") or {}
                        state["coverage"]["index"] = coverage
                        errors = report.get("errors") or []
                        index_status = report.get("job_status", "failed")
                        extracted = coverage.get("extracted", coverage.get("documents_extracted", 0))
                        extraction_complete = extracted.get("complete") is True if isinstance(extracted, dict) else None
                        complete = (report.get("ok") is True and index_status == "completed" and not errors
                                    and isinstance(report.get("totals"), dict)
                                    and coverage.get("complete") is not False and extraction_complete is not False)
                        if not complete and not errors:
                            errors = [{"code": "index_busy" if index_status == "busy" else "index_incomplete",
                                       "message": report.get("error") or "索引尚未完整提交", "retryable": True}]
                        state["extracted"] = _count(extracted.get("processed", 0) if isinstance(extracted, dict) else extracted)
                        state["extracted_unit"] = "pages" if isinstance(extracted, dict) else "documents"
                        status = "completed" if complete else (index_status if index_status in {"failed", "busy"} else "partial")
                        emit("extract", "completed" if extraction_complete is True else ("pending" if status == "busy" else status),
                             count=state["extracted"], count_unit=state["extracted_unit"],
                             coverage=extracted if isinstance(extracted, dict) else {},
                             errors=[error for error in errors if isinstance(error, dict) and error.get("stage") == "extract"]
                             if extraction_complete is True else errors)
                        emit("index", status, errors=errors)
                    except Exception as exc:
                        fail("extract", exc)
                        fail("index", exc)

                # Partial extraction/indexing must not veto exploration of available map units.
                if state.get("map_revision") and state.get("map_status") in {"complete", "partial"}:
                    _bind_exploration(state, state["map_revision"], force=force)
                    state["force"] = False
                    force = False
                    _save(root, state)
                    if not done("explore"):
                        identity = state["model_operation"]
                        ledger = _budget()
                        saved_checkpoint = state.get("exploration_checkpoint") or {}
                        if saved_checkpoint and any(saved_checkpoint.get(key) != identity[key] for key in
                                ("exploration_id", "operation_id", "source_revision", "map_revision", "observed_revision")):
                            raise ValueError("探索预算检查点身份不匹配；保留旧账本，需显式重新调查")
                        ledger.restore(saved_checkpoint.get("budget") or state.get("model_budget") or {})

                        def checkpoint(payload):
                            if not isinstance(payload, dict):
                                raise ValueError("探索检查点格式无效")
                            for key in ("exploration_id", "operation_id", "source_revision", "map_revision", "observed_revision"):
                                if payload.get(key) and payload[key] != identity[key]:
                                    raise ValueError("探索检查点身份或来源版本不匹配")
                            # Payloads are freshly constructed by the explorer; a
                            # shallow copy avoids copying large evidence graphs on
                            # every checkpoint.
                            saved = {**payload, **identity, "workspace_operation_id": operation["id"]}
                            if isinstance(payload.get("budget"), dict):
                                # Includes conservative in-flight reservation before a model call.
                                state["model_budget"] = dict(payload["budget"])
                            state["exploration_checkpoint"] = saved
                            state["coverage"]["explore"] = _exploration_coverage(payload)
                            _save(root, state)

                        state["model_ready"] = _ready(model_ready, model_client)
                        if not state["model_ready"]:
                            state.update(exploration_status="awaiting_model", phase_complete=False)
                            state["coverage"]["explore"] = _exploration_coverage({"pending": state["map_totals"].get("units", 0)})
                            emit("explore", "awaiting_model", errors=[{"code": "awaiting_model",
                                 "message": "项目地图已建立；初步认知等待显式配置的模型", "retryable": True}])
                        else:
                            room, reason = ledger.can_step()
                            if not room:
                                state.update(exploration_status="paused_budget", phase_complete=False)
                                emit("explore", "paused_budget", errors=[{"code": reason,
                                     "message": "本次探索预算已用尽；保留进度，可显式重新调查", "retryable": False}])
                            else:
                                emit("explore", "running")
                                try:
                                    from ..model_client import resolve_model_client
                                    result = (explorer or _explore)(root, query="", scope="onboarding", limit=EXPLORATION_LIMIT,
                                        model_client=resolve_model_client(model_client), model_budget=ledger,
                                        progress=lambda p: emit("explore", "running", progress=p),
                                        **identity, workspace_operation_id=operation["id"], checkpoint=checkpoint)
                                    if result.get("exploration_id") != identity["exploration_id"]:
                                        raise ValueError("探索结果不属于当前操作")
                                    if result.get("map_revision") and result["map_revision"] != identity["map_revision"]:
                                        raise ValueError("探索结果来源版本已变化")
                                    state["model_budget"] = copy.deepcopy(result.get("budget") or ledger.as_dict())
                                    checkpoint({**result, **identity, "budget": state["model_budget"]})
                                    status = result.get("exploration_status", "failed")
                                    if status == "paused_steps":
                                        status = "paused_budget"
                                    oriented = (result.get("ok") is True and result.get("phase_complete") is True
                                                and status == "ready" and result.get("scope", "onboarding") in {"onboarding", "full"})
                                    state["exploration"] = {key: value for key, value in result.items() if key != "observations"}
                                    state["exploration"].update(complete=False, phase_complete=oriented, exploration_status=status)
                                    state["coverage"]["explore"] = _exploration_coverage(result)
                                    state.update(exploration_status=status, phase_complete=oriented)
                                    errors = result.get("errors") or []
                                    if not oriented and not errors:
                                        errors = [{"code": "exploration_incomplete", "message": "初步认知尚未完成；当前发现与未决问题已保留"}]
                                    emit("explore", "ready" if oriented else (status if status in
                                         {"partial", "paused_budget", "paused_provider", "paused_model", "failed", "awaiting_model", "mapped_awaiting_model"} else "partial"), errors=errors)
                                except Exception as exc:
                                    # Do not replace an admit checkpoint with a smaller in-memory ledger.
                                    state.update(exploration_status="failed", phase_complete=False)
                                    fail("explore", exc)
                else:
                    state.update(exploration_status="partial", phase_complete=False)
                    emit("explore", "pending", errors=[{"code": "map_unavailable", "message": "地图尚不可用；未调用模型"}])

                if not done("graph") and done("index"):
                    emit("graph", "running")
                    try:
                        result = (grapher or _graph)(root, lambda p: emit("graph", "running", progress=p))
                        state["graph"] = result
                        if result.get("ok") is not True or result.get("errors") or result.get("complete") is False or result.get("projection_pending"):
                            raise RuntimeError(result.get("error") or "图谱构建未完整完成")
                        emit("graph", "completed", errors=[])
                    except Exception as exc:
                        fail("graph", exc)
                if mapper is None and state.get("map_revision"):
                    # The explorer updates unit status. Read post-exploration
                    # totals instead of leaving the UI on pre-run counters.
                    try:
                        latest_map = _map_snapshot(root)
                        if latest_map.get("map_revision") == state["map_revision"]:
                            state["map_totals"] = latest_map.get("totals") or {}
                            state["map_status"] = latest_map.get("map_status", "partial")
                            state["coverage"]["map"] = {**(latest_map.get("coverage") or {}), "semantic_exhaustive": False}
                            state["map_report"] = {key: latest_map[key] for key in
                                ("ok", "map_status", "map_revision", "totals", "coverage", "errors") if key in latest_map}
                            emit("map", "completed" if state["map_status"] == "complete" else "partial",
                                 errors=latest_map.get("errors") or [])
                    except Exception as exc:
                        fail("map", exc)
                if revision(_inventory(root)) != observed_revision:
                    state["phase_complete"] = False
                    force = False
                    if attempt + 1 < MAX_REVISION_PASSES:
                        continue
                    emit("scan", "pending", errors=[{"code": "source_changed_during_job",
                         "message": "资料持续变化；暂停后台调查，等待来源稳定后重试"}])
                    break
                if state.get("phase_complete") and done("explore"):
                    state["processed_revision"] = observed_revision
                    state["first_sync_completed"] = True
                break

            status = state.get("exploration_status")
            oriented = (state.get("phase_complete") is True and done("explore") and done("scan")
                        and state.get("map_status") in {"complete", "partial"})
            state.update(job_status="oriented" if oriented else ("paused_budget" if status == "paused_budget" else
                         "paused_provider" if status == "paused_provider" else
                         "paused_model" if status == "paused_model" else
                         "mapped_awaiting_model" if status in {"awaiting_model", "mapped_awaiting_model"} else
                         "failed" if status == "failed" or stages.get("scan", {}).get("status") == "failed" else "partial"),
                         running=False)
            _save(root, state)
            store.finish(operation["id"], {"ok": oriented, "complete": False, "phase_complete": oriented,
                         "scope": "project_orientation", "job_id": job_id,
                         "job_status": state["job_status"], "exploration_id": state.get("exploration_id", ""),
                         "coverage": state["coverage"], "errors": state["errors"]})
            return state
        except Exception as exc:
            state.update(job_status="failed", running=False, phase_complete=False,
                         job_errors=[{"code": type(exc).__name__, "message": str(exc), "retryable": True}])
            _save(root, state)
            store.finish(operation["id"], {"ok": False, "errors": state["errors"]})
            return state
        finally:
            stopped.set()
            thread.join(timeout=1)
