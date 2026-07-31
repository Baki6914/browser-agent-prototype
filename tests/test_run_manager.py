"""Deterministic tests for the in-memory RunManager."""

from __future__ import annotations

import ast
import asyncio
from dataclasses import FrozenInstanceError, fields
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from browser_agent import (
    AgentPauseKind,
    AgentRunResult,
    AgentRunStatus,
    PendingConfirmation,
    RunConflictError,
    RunConstructionError,
    RunIdempotencyConflictError,
    RunManager,
    RunManagerClosedError,
    RunManagerError,
    RunNotFoundError,
    RunStatus,
    SecretField,
    SecretNotFoundError,
    TransientSecretStore,
)


def result(
    status: AgentRunStatus,
    *,
    question: str | None = None,
    pause_kind: AgentPauseKind | None = None,
    final_result: str | None = None,
    pending_confirmation: PendingConfirmation | None = None,
    secret_fields: tuple[SecretField, ...] | None = None,
) -> AgentRunResult:
    return AgentRunResult(
        status=status,
        steps=(),
        final_result=final_result,
        question=question,
        pause_kind=pause_kind,
        pending_confirmation=pending_confirmation,
        secret_fields=secret_fields,
    )


class FakeSession:
    def __init__(self, results: list[AgentRunResult | BaseException]) -> None:
        self.results = list(results)
        self.calls: list[tuple[str, object]] = []
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.release.set()

    async def _call(self, kind: str, value: object) -> AgentRunResult:
        self.calls.append((kind, value))
        self.entered.set()
        await self.release.wait()
        item = self.results.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    async def start(self, task: str) -> AgentRunResult:
        return await self._call("start", task)

    async def respond(self, text: str) -> AgentRunResult:
        return await self._call("respond", text)

    async def confirm(self, approved: bool) -> AgentRunResult:
        return await self._call("confirm", approved)


class FakeHandle:
    def __init__(
        self, session: FakeSession, close_error: BaseException | None = None
    ) -> None:
        self.session = session
        self.close_error = close_error
        self.close_calls = 0
        self.closed = asyncio.Event()

    async def close(self) -> None:
        self.close_calls += 1
        self.closed.set()
        if self.close_error is not None:
            raise self.close_error


class BlockingHandle(FakeHandle):
    def __init__(
        self, session: FakeSession, close_error: BaseException | None = None
    ) -> None:
        super().__init__(session, close_error)
        self.close_started = asyncio.Event()
        self.close_release = asyncio.Event()
        self.close_completed = asyncio.Event()

    async def close(self) -> None:
        self.close_calls += 1
        self.close_started.set()
        await self.close_release.wait()
        if self.close_error is not None:
            raise self.close_error
        self.closed.set()
        self.close_completed.set()


class FakeFactory:
    def __init__(
        self,
        handles: list[FakeHandle] | None = None,
        error: BaseException | None = None,
    ) -> None:
        self.handles = list(handles or [])
        self.error = error
        self.calls: list[tuple[str, str]] = []
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.release.set()

    async def create(self, start_url: str, task: str) -> FakeHandle:
        self.calls.append((start_url, task))
        self.entered.set()
        await self.release.wait()
        if self.error is not None:
            raise self.error
        return self.handles.pop(0)


class TrackingSecretStore(TransientSecretStore):
    def __init__(self) -> None:
        super().__init__()
        self.discard_run_calls: list[str] = []
        self.close_calls = 0
        self.fail_put = False
        self.fail_discard_run = False
        self.fail_close = False

    async def put(self, run_id, interaction_id, values):
        if self.fail_put:
            raise RuntimeError("SYNTHETIC-STORE-DETAIL")
        return await super().put(run_id, interaction_id, values)

    async def discard_run(self, run_id):
        self.discard_run_calls.append(run_id)
        await super().discard_run(run_id)
        if self.fail_discard_run:
            raise RuntimeError("SYNTHETIC-CLEANUP-DETAIL")

    async def close(self):
        self.close_calls += 1
        await super().close()
        if self.fail_close:
            raise RuntimeError("SYNTHETIC-CLOSE-DETAIL")


class ControlledSecretStore(TrackingSecretStore):
    def __init__(self) -> None:
        super().__init__()
        self.put_error: BaseException | None = None
        self.discard_run_error: BaseException | None = None
        self.close_started = asyncio.Event()
        self.close_release = asyncio.Event()
        self.close_release.set()

    async def put(self, run_id, interaction_id, values):
        if self.put_error is not None:
            raise self.put_error
        return await super().put(run_id, interaction_id, values)

    async def discard_run(self, run_id):
        self.discard_run_calls.append(run_id)
        if self.discard_run_error is not None:
            raise self.discard_run_error
        await TransientSecretStore.discard_run(self, run_id)

    async def close(self):
        self.close_calls += 1
        self.close_started.set()
        await self.close_release.wait()
        await TransientSecretStore.close(self)
        if self.fail_close:
            raise RuntimeError("SYNTHETIC-CLOSE-DETAIL")


class ShutdownTrackingRunManager(RunManager):
    def __init__(self, factory: FakeFactory) -> None:
        super().__init__(factory, secret_store=TransientSecretStore())
        self.shutdown_entered = asyncio.Event()

    async def _shutdown_record(self, record: object) -> None:
        self.shutdown_entered.set()
        await super()._shutdown_record(record)  # type: ignore[arg-type]


class RunManagerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.managers: list[RunManager] = []

    async def asyncTearDown(self) -> None:
        for manager in self.managers:
            try:
                await manager.close()
            except RunManagerError:
                pass

    def manager(self, factory: FakeFactory) -> RunManager:
        manager = RunManager(factory, secret_store=TransientSecretStore())
        self.managers.append(manager)
        return manager

    async def settle(self, manager: RunManager, run_id: str) -> None:
        record = manager._runs[run_id]
        task = record.active_task
        if task is not None:
            await task

    async def paused(
        self, kind: AgentPauseKind = AgentPauseKind.USER_INPUT
    ) -> tuple[RunManager, FakeSession, FakeHandle, object]:
        pending = (
            PendingConfirmation(1, "browser_click", "{}")
            if kind is AgentPauseKind.CONFIRMATION
            else None
        )
        session = FakeSession(
            [
                result(
                    AgentRunStatus.AWAITING_USER,
                    question="Question?",
                    pause_kind=kind,
                    pending_confirmation=pending,
                )
            ]
        )
        handle = FakeHandle(session)
        manager = self.manager(FakeFactory([handle]))
        created = await manager.create_run("https://example.com", "task")
        await self.settle(manager, created.run_id)
        return manager, session, handle, await manager.get_run(created.run_id)

    async def blocking_paused(
        self, close_error: BaseException | None = None
    ) -> tuple[RunManager, BlockingHandle, object]:
        session = FakeSession(
            [
                result(
                    AgentRunStatus.AWAITING_USER,
                    question="Question?",
                    pause_kind=AgentPauseKind.USER_INPUT,
                )
            ]
        )
        handle = BlockingHandle(session, close_error)
        manager = self.manager(FakeFactory([handle]))
        created = await manager.create_run("url", "task")
        await self.settle(manager, created.run_id)
        return manager, handle, await manager.get_run(created.run_id)

    async def test_create_is_queued_then_start_runs_and_finishes(self) -> None:
        session = FakeSession(
            [result(AgentRunStatus.FINISHED, final_result="done")]
        )
        handle = FakeHandle(session)
        factory = FakeFactory([handle])
        factory.release.clear()
        manager = self.manager(factory)

        created = await manager.create_run("https://example.com", "task")
        self.assertEqual((created.status, created.version), (RunStatus.QUEUED, 1))
        self.assertEqual(factory.calls, [])
        await factory.entered.wait()
        running = await manager.get_run(created.run_id)
        self.assertEqual(running.status, RunStatus.RUNNING)
        factory.release.set()
        await self.settle(manager, created.run_id)
        finished = await manager.get_run(created.run_id)
        self.assertEqual(finished.status, RunStatus.FINISHED)
        self.assertEqual(finished.final_result, "done")
        self.assertEqual(handle.close_calls, 1)
        self.assertLess(created.version, running.version)
        self.assertLess(running.version, finished.version)

    async def test_user_and_confirmation_pauses_have_interactions(self) -> None:
        for kind, expected in (
            (AgentPauseKind.USER_INPUT, RunStatus.AWAITING_USER),
            (AgentPauseKind.CONFIRMATION, RunStatus.AWAITING_CONFIRMATION),
        ):
            manager, _, _, snapshot = await self.paused(kind)
            self.assertEqual(snapshot.status, expected)
            self.assertEqual(snapshot.question, "Question?")
            self.assertTrue(snapshot.interaction_id)
            await manager.close()

    async def test_respond_reuses_session_and_new_pause_gets_new_identity(self) -> None:
        manager, session, _, first = await self.paused()
        session.results.append(
            result(
                AgentRunStatus.AWAITING_USER,
                question="Again?",
                pause_kind=AgentPauseKind.USER_INPUT,
            )
        )
        accepted = await manager.respond(
            first.run_id, first.interaction_id, "request-1", "answer"
        )
        self.assertEqual(accepted.status, RunStatus.RUNNING)
        self.assertIsNone(accepted.interaction_id)
        await self.settle(manager, first.run_id)
        second = await manager.get_run(first.run_id)
        self.assertNotEqual(first.interaction_id, second.interaction_id)
        self.assertEqual(session.calls[-1], ("respond", "answer"))
        with self.assertRaises(RunConflictError):
            await manager.respond(
                first.run_id, first.interaction_id, "request-2", "answer"
            )

    async def test_confirm_reuses_session_and_finishes(self) -> None:
        manager, session, handle, pause = await self.paused(
            AgentPauseKind.CONFIRMATION
        )
        session.results.append(result(AgentRunStatus.FINISHED, final_result="ok"))
        accepted = await manager.confirm(
            pause.run_id, pause.interaction_id, "confirm-1", True
        )
        self.assertEqual(accepted.status, RunStatus.RUNNING)
        await self.settle(manager, pause.run_id)
        self.assertEqual(session.calls[-1], ("confirm", True))
        self.assertEqual((await manager.get_run(pause.run_id)).status, RunStatus.FINISHED)
        self.assertEqual(handle.close_calls, 1)

    async def test_non_finished_terminal_status_mapping_and_cleanup(self) -> None:
        for agent_status, run_status in (
            (AgentRunStatus.STEP_LIMIT_REACHED, RunStatus.STEP_LIMIT_REACHED),
            (
                AgentRunStatus.DECISION_SOURCE_EXHAUSTED,
                RunStatus.DECISION_SOURCE_EXHAUSTED,
            ),
        ):
            session = FakeSession([result(agent_status)])
            handle = FakeHandle(session)
            manager = self.manager(FakeFactory([handle]))
            created = await manager.create_run("url", "task")
            await self.settle(manager, created.run_id)
            self.assertEqual((await manager.get_run(created.run_id)).status, run_status)
            self.assertEqual(handle.close_calls, 1)

    async def test_wrong_type_interaction_and_terminal_resumes_conflict(self) -> None:
        manager, _, _, pause = await self.paused()
        with self.assertRaises(RunConflictError):
            await manager.confirm(
                pause.run_id, pause.interaction_id, "wrong-type", True
            )
        with self.assertRaises(RunConflictError):
            await manager.respond(pause.run_id, "wrong", "wrong-id", "text")
        await manager.cancel(pause.run_id, "cancel")
        with self.assertRaises(RunConflictError):
            await manager.respond(
                pause.run_id, pause.interaction_id, "after-cancel", "text"
            )

    async def test_respond_idempotency_before_and_after_completion(self) -> None:
        manager, session, _, pause = await self.paused()
        session.results.append(result(AgentRunStatus.FINISHED, final_result="done"))
        first = await manager.respond(
            pause.run_id, pause.interaction_id, "same", "text"
        )
        retry = await manager.respond(
            pause.run_id, pause.interaction_id, "same", "text"
        )
        self.assertEqual(first.version, retry.version)
        await self.settle(manager, pause.run_id)
        terminal = await manager.respond(
            pause.run_id, pause.interaction_id, "same", "text"
        )
        self.assertEqual(terminal.status, RunStatus.FINISHED)
        self.assertEqual([call[0] for call in session.calls].count("respond"), 1)
        with self.assertRaises(RunIdempotencyConflictError):
            await manager.respond(
                pause.run_id, pause.interaction_id, "same", "different"
            )
        with self.assertRaises(RunIdempotencyConflictError):
            await manager.confirm(
                pause.run_id, pause.interaction_id, "same", True
            )

    async def test_confirm_idempotency_and_changed_bool_conflict(self) -> None:
        manager, session, _, pause = await self.paused(
            AgentPauseKind.CONFIRMATION
        )
        session.release.clear()
        session.results.append(result(AgentRunStatus.FINISHED, final_result="done"))
        calls = await asyncio.gather(
            manager.confirm(pause.run_id, pause.interaction_id, "same", True),
            manager.confirm(pause.run_id, pause.interaction_id, "same", True),
        )
        self.assertEqual(calls[0].version, calls[1].version)
        with self.assertRaises(RunIdempotencyConflictError):
            await manager.confirm(
                pause.run_id, pause.interaction_id, "same", False
            )
        session.release.set()
        await self.settle(manager, pause.run_id)
        self.assertEqual([call[0] for call in session.calls].count("confirm"), 1)

    async def test_concurrent_different_requests_execute_at_most_once(self) -> None:
        manager, session, _, pause = await self.paused()
        session.results.append(result(AgentRunStatus.FINISHED, final_result="done"))
        outcomes = await asyncio.gather(
            manager.respond(pause.run_id, pause.interaction_id, "one", "a"),
            manager.respond(pause.run_id, pause.interaction_id, "two", "b"),
            return_exceptions=True,
        )
        self.assertEqual(sum(not isinstance(x, Exception) for x in outcomes), 1)
        await self.settle(manager, pause.run_id)
        self.assertEqual([call[0] for call in session.calls].count("respond"), 1)

    async def test_cancel_active_paused_and_idempotent_cleanup(self) -> None:
        session = FakeSession([result(AgentRunStatus.FINISHED, final_result="x")])
        session.release.clear()
        handle = FakeHandle(session)
        manager = self.manager(FakeFactory([handle]))
        created = await manager.create_run("url", "task")
        await session.entered.wait()
        cancelled = await manager.cancel(created.run_id, "cancel")
        self.assertEqual(cancelled.status, RunStatus.CANCELLED)
        retry = await manager.cancel(created.run_id, "cancel")
        self.assertEqual(retry.version, cancelled.version)
        self.assertEqual(handle.close_calls, 1)
        with self.assertRaises(RunConflictError):
            await manager.cancel(created.run_id, "new-cancel")

        manager2, _, handle2, pause = await self.paused()
        cancelled2 = await manager2.cancel(pause.run_id, "paused-cancel")
        self.assertIsNone(cancelled2.interaction_id)
        self.assertIsNone(cancelled2.question)
        self.assertEqual(handle2.close_calls, 1)

    async def test_queued_cancel_does_not_construct_or_leak(self) -> None:
        factory = FakeFactory([])
        manager = self.manager(factory)
        created = await manager.create_run("url", "task")
        cancelled = await manager.cancel(created.run_id, "cancel")
        self.assertEqual(cancelled.status, RunStatus.CANCELLED)
        self.assertEqual(factory.calls, [])

    async def test_factory_session_and_invalid_result_failures(self) -> None:
        cases: list[tuple[FakeFactory, FakeHandle | None]] = [
            (FakeFactory(error=RuntimeError("factory failed")), None),
            (
                FakeFactory(
                    [FakeHandle(FakeSession([RuntimeError("start failed")]))]
                ),
                None,
            ),
            (
                FakeFactory(
                    [
                        FakeHandle(
                            FakeSession(
                                [
                                    result(
                                        AgentRunStatus.AWAITING_USER,
                                        question=None,
                                        pause_kind=AgentPauseKind.USER_INPUT,
                                    )
                                ]
                            )
                        )
                    ]
                ),
                None,
            ),
        ]
        for factory, _ in cases:
            manager = self.manager(factory)
            created = await manager.create_run("url", "task")
            await self.settle(manager, created.run_id)
            failed = await manager.get_run(created.run_id)
            self.assertEqual(failed.status, RunStatus.FAILED)
            self.assertTrue(failed.error_type)
            self.assertIsNone(failed.interaction_id)

    async def test_resume_failures_become_failed(self) -> None:
        for kind in (AgentPauseKind.USER_INPUT, AgentPauseKind.CONFIRMATION):
            manager, session, handle, pause = await self.paused(kind)
            session.results.append(RuntimeError("resume failed"))
            if kind is AgentPauseKind.USER_INPUT:
                await manager.respond(
                    pause.run_id, pause.interaction_id, "request", "text"
                )
            else:
                await manager.confirm(
                    pause.run_id, pause.interaction_id, "request", True
                )
            await self.settle(manager, pause.run_id)
            failed = await manager.get_run(pause.run_id)
            self.assertEqual(failed.status, RunStatus.FAILED)
            self.assertEqual(handle.close_calls, 1)

    async def test_cleanup_failure_fails_terminal_but_preserves_cancelled(self) -> None:
        session = FakeSession([result(AgentRunStatus.FINISHED, final_result="x")])
        handle = FakeHandle(session, RuntimeError("line 1\nline 2"))
        manager = self.manager(FakeFactory([handle]))
        created = await manager.create_run("url", "task")
        await self.settle(manager, created.run_id)
        failed = await manager.get_run(created.run_id)
        self.assertEqual(failed.status, RunStatus.FAILED)
        self.assertNotIn("\n", failed.error_message)
        self.assertEqual(handle.close_calls, 1)

        manager2, _, handle2, pause = await self.paused()
        handle2.close_error = RuntimeError("close")
        cancelled = await manager2.cancel(pause.run_id, "cancel")
        self.assertEqual(cancelled.status, RunStatus.CANCELLED)
        self.assertEqual(cancelled.error_type, "RuntimeError")

    async def test_terminal_cleanup_is_awaited_not_cancelled_by_close(self) -> None:
        session = FakeSession(
            [result(AgentRunStatus.FINISHED, final_result="done")]
        )
        handle = BlockingHandle(session)
        manager = ShutdownTrackingRunManager(FakeFactory([handle]))
        self.managers.append(manager)
        created = await manager.create_run("url", "task")
        await handle.close_started.wait()

        close_task = asyncio.create_task(manager.close())
        await manager.shutdown_entered.wait()
        self.assertFalse(close_task.done())
        self.assertFalse(handle.close_completed.is_set())

        handle.close_release.set()
        await close_task
        self.assertTrue(handle.close_completed.is_set())
        self.assertEqual(handle.close_calls, 1)
        self.assertEqual(
            (await manager.get_run(created.run_id)).status,
            RunStatus.FINISHED,
        )

    async def test_cancel_and_close_await_the_same_blocked_cleanup(self) -> None:
        manager, blocking_handle, pause = await self.blocking_paused()

        cancel_task = asyncio.create_task(manager.cancel(pause.run_id, "cancel"))
        await blocking_handle.close_started.wait()
        close_task = asyncio.create_task(manager.close())
        self.assertFalse(cancel_task.done())
        self.assertFalse(close_task.done())

        blocking_handle.close_release.set()
        cancelled, _ = await asyncio.gather(cancel_task, close_task)
        self.assertEqual(cancelled.status, RunStatus.CANCELLED)
        self.assertEqual(
            (await manager.get_run(pause.run_id)).status,
            RunStatus.CANCELLED,
        )
        self.assertTrue(blocking_handle.close_completed.is_set())
        self.assertEqual(blocking_handle.close_calls, 1)

    async def test_direct_cleanup_callers_share_one_task(self) -> None:
        manager, blocking_handle, pause = await self.blocking_paused()
        record = manager._runs[pause.run_id]

        first = asyncio.create_task(manager._close_handle(record))
        await blocking_handle.close_started.wait()
        shared = record.cleanup_task
        second = asyncio.create_task(manager._close_handle(record))
        self.assertIs(record.cleanup_task, shared)
        self.assertFalse(first.done())
        self.assertFalse(second.done())

        blocking_handle.close_release.set()
        await asyncio.gather(first, second)
        self.assertEqual(blocking_handle.close_calls, 1)

    async def test_cancelled_cleanup_waiter_does_not_cancel_shared_task(self) -> None:
        manager, blocking_handle, pause = await self.blocking_paused()
        record = manager._runs[pause.run_id]

        waiter = asyncio.create_task(manager._close_handle(record))
        await blocking_handle.close_started.wait()
        cleanup_task = record.cleanup_task
        self.assertIsNotNone(cleanup_task)
        waiter.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiter
        self.assertFalse(cleanup_task.cancelled())
        self.assertFalse(cleanup_task.done())

        blocking_handle.close_release.set()
        await cleanup_task
        self.assertTrue(blocking_handle.close_completed.is_set())
        self.assertEqual(blocking_handle.close_calls, 1)

    async def test_cancelled_cleanup_failure_increments_version_once(self) -> None:
        manager, handle, pause = await self.blocking_paused(
            RuntimeError("cleanup failed")
        )
        record = manager._runs[pause.run_id]

        cancel_task = asyncio.create_task(manager.cancel(pause.run_id, "cancel"))
        await handle.close_started.wait()
        before = await manager.get_run(pause.run_id)
        self.assertEqual(before.status, RunStatus.CANCELLED)

        handle.close_release.set()
        after = await cancel_task
        self.assertEqual(after.status, RunStatus.CANCELLED)
        self.assertEqual(after.error_type, "RuntimeError")
        self.assertEqual(after.version, before.version + 1)
        again = await manager._close_handle(record)
        self.assertFalse(again)
        self.assertEqual(
            (await manager.get_run(pause.run_id)).version, after.version
        )

    async def test_failed_cleanup_error_change_increments_version_once(self) -> None:
        session = FakeSession([RuntimeError("execution failed")])
        handle = BlockingHandle(session, ValueError("cleanup failed"))
        manager = self.manager(FakeFactory([handle]))
        created = await manager.create_run("url", "task")
        await handle.close_started.wait()
        before = await manager.get_run(created.run_id)
        self.assertEqual(before.status, RunStatus.FAILED)
        self.assertEqual(before.error_type, "RuntimeError")

        handle.close_release.set()
        await self.settle(manager, created.run_id)
        after = await manager.get_run(created.run_id)
        self.assertEqual(after.status, RunStatus.FAILED)
        self.assertEqual(after.error_type, "ValueError")
        self.assertEqual(after.version, before.version + 1)
        await manager._close_handle(manager._runs[created.run_id])
        self.assertEqual(
            (await manager.get_run(created.run_id)).version, after.version
        )

    async def test_result_translation_rejects_inconsistent_pause_state(self) -> None:
        pending = PendingConfirmation(1, "browser_click", "{}")
        cases = (
            result(
                AgentRunStatus.AWAITING_USER,
                question="Question?",
                pause_kind=AgentPauseKind.USER_INPUT,
                pending_confirmation=pending,
            ),
            result(
                AgentRunStatus.AWAITING_USER,
                question="Question?",
                pause_kind=AgentPauseKind.CONFIRMATION,
            ),
        )
        for inconsistent in cases:
            session = FakeSession([inconsistent])
            manager = self.manager(FakeFactory([FakeHandle(session)]))
            created = await manager.create_run("url", "task")
            await self.settle(manager, created.run_id)
            self.assertEqual(
                (await manager.get_run(created.run_id)).status,
                RunStatus.FAILED,
            )

    async def test_valid_confirmation_pause_requires_pending_state(self) -> None:
        pending = PendingConfirmation(1, "browser_click", "{}")
        session = FakeSession(
            [
                result(
                    AgentRunStatus.AWAITING_USER,
                    question="Approve?",
                    pause_kind=AgentPauseKind.CONFIRMATION,
                    pending_confirmation=pending,
                )
            ]
        )
        manager = self.manager(FakeFactory([FakeHandle(session)]))
        created = await manager.create_run("url", "task")
        await self.settle(manager, created.run_id)
        snapshot = await manager.get_run(created.run_id)
        self.assertEqual(snapshot.status, RunStatus.AWAITING_CONFIRMATION)
        self.assertEqual(snapshot.question, "Approve?")

    async def test_finished_result_requires_non_whitespace_text(self) -> None:
        for final_result in ("", " \t\n"):
            session = FakeSession(
                [result(AgentRunStatus.FINISHED, final_result=final_result)]
            )
            manager = self.manager(FakeFactory([FakeHandle(session)]))
            created = await manager.create_run("url", "task")
            await self.settle(manager, created.run_id)
            self.assertEqual(
                (await manager.get_run(created.run_id)).status,
                RunStatus.FAILED,
            )

    async def test_close_cancels_active_closes_paused_and_is_idempotent(self) -> None:
        active_session = FakeSession(
            [result(AgentRunStatus.FINISHED, final_result="x")]
        )
        active_session.release.clear()
        active_handle = FakeHandle(active_session)
        manager = self.manager(FakeFactory([active_handle]))
        active = await manager.create_run("url", "task")
        await active_session.entered.wait()
        await manager.close()
        await manager.close()
        self.assertEqual((await manager.get_run(active.run_id)).status, RunStatus.CANCELLED)
        self.assertEqual(active_handle.close_calls, 1)
        with self.assertRaises(RunManagerClosedError):
            await manager.create_run("url", "task")
        with self.assertRaises(RunManagerClosedError):
            await manager.cancel(active.run_id, "request")

        manager2, _, paused_handle, _ = await self.paused()
        await manager2.close()
        self.assertEqual(paused_handle.close_calls, 1)

    async def test_different_runs_are_independent(self) -> None:
        first = FakeSession([result(AgentRunStatus.FINISHED, final_result="one")])
        first.release.clear()
        second = FakeSession([result(AgentRunStatus.FINISHED, final_result="two")])
        manager = self.manager(
            FakeFactory([FakeHandle(first), FakeHandle(second)])
        )
        run1 = await manager.create_run("one", "one")
        await first.entered.wait()
        run2 = await manager.create_run("two", "two")
        await self.settle(manager, run2.run_id)
        self.assertEqual((await manager.get_run(run2.run_id)).status, RunStatus.FINISHED)
        self.assertEqual((await manager.get_run(run1.run_id)).status, RunStatus.RUNNING)
        first.release.set()
        await self.settle(manager, run1.run_id)

    async def test_validation_not_found_and_snapshot_contract(self) -> None:
        manager = self.manager(FakeFactory([]))
        for start_url, task in (("", "task"), ("url", " "), (True, "task")):
            with self.assertRaises(RunConstructionError):
                await manager.create_run(start_url, task)  # type: ignore[arg-type]
        with self.assertRaises(RunNotFoundError):
            await manager.get_run("missing")
        with self.assertRaises(RunManagerError):
            await manager.get_run(True)  # type: ignore[arg-type]
        with self.assertRaises(RunConstructionError):
            RunManager(None, secret_store=TransientSecretStore())  # type: ignore[arg-type]

        session = FakeSession([result(AgentRunStatus.FINISHED, final_result="x")])
        manager2 = self.manager(FakeFactory([FakeHandle(session)]))
        created = await manager2.create_run("url", "task")
        version = created.version
        self.assertEqual((await manager2.get_run(created.run_id)).version, version)
        with self.assertRaises(FrozenInstanceError):
            created.version = 9  # type: ignore[misc]
        names = {item.name for item in fields(created)}
        self.assertTrue(
            names.isdisjoint({"session", "handle", "task_handle", "lock", "requests"})
        )

    async def test_bool_validation_and_close_once_race(self) -> None:
        manager, _, handle, pause = await self.paused(
            AgentPauseKind.CONFIRMATION
        )
        with self.assertRaises(RunManagerError):
            await manager.confirm(
                pause.run_id, pause.interaction_id, "request", 1  # type: ignore[arg-type]
            )
        await asyncio.gather(
            manager.cancel(pause.run_id, "cancel"),
            manager.close(),
        )
        self.assertEqual(handle.close_calls, 1)


class RunManagerSecretTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.managers: list[RunManager] = []

    async def asyncTearDown(self) -> None:
        for manager in self.managers:
            await manager.close()

    async def settle(self, manager: RunManager, run_id: str) -> None:
        task = manager._runs[run_id].active_task
        if task is not None:
            await task

    async def secret_paused(self):
        session = FakeSession([result(
            AgentRunStatus.AWAITING_USER,
            question="Enter credentials",
            pause_kind=AgentPauseKind.SECRET,
            secret_fields=(SecretField.PASSWORD, SecretField.USERNAME),
        )])
        store = TransientSecretStore()
        manager = RunManager(FakeFactory([FakeHandle(session)]), secret_store=store)
        self.managers.append(manager)
        created = await manager.create_run("url", "task")
        await self.settle(manager, created.run_id)
        return manager, store, session, await manager.get_run(created.run_id)

    async def test_secret_pause_submission_and_exact_retry(self) -> None:
        manager, store, session, pause = await self.secret_paused()
        self.assertEqual(pause.status, RunStatus.AWAITING_SECRET)
        self.assertEqual(pause.secret_fields, (SecretField.PASSWORD, SecretField.USERNAME))
        values = {SecretField.USERNAME: "synthetic-user", SecretField.PASSWORD: "synthetic-pass"}
        accepted = await manager.submit_secret(
            pause.run_id, pause.interaction_id, "secret-request", values
        )
        self.assertEqual(accepted.status, RunStatus.AWAITING_SECRET_APPLICATION)
        self.assertIsNone(accepted.question)
        self.assertIsNone(accepted.interaction_id)
        self.assertEqual(len(session.calls), 1)
        record = manager._runs[pause.run_id]
        reference = record.secret_reference
        self.assertIsNotNone(reference)
        retry = await manager.submit_secret(
            pause.run_id, pause.interaction_id, "secret-request", dict(reversed(tuple(values.items())))
        )
        self.assertEqual(retry, accepted)
        consumed = await store.consume(reference)  # type: ignore[arg-type]
        self.assertEqual(consumed, values)

    async def test_mismatch_conflict_and_cancel_cleanup(self) -> None:
        manager, store, _, pause = await self.secret_paused()
        with self.assertRaises(RunConflictError):
            await manager.submit_secret(
                pause.run_id, pause.interaction_id, "missing",
                {SecretField.PASSWORD: "synthetic-pass"},
            )
        await manager.submit_secret(
            pause.run_id, pause.interaction_id, "accepted",
            {SecretField.PASSWORD: "synthetic-pass", SecretField.USERNAME: "synthetic-user"},
        )
        reference = manager._runs[pause.run_id].secret_reference
        with self.assertRaises(RunIdempotencyConflictError):
            await manager.submit_secret(
                pause.run_id, pause.interaction_id, "accepted",
                {SecretField.PASSWORD: "changed", SecretField.USERNAME: "synthetic-user"},
            )
        await manager.cancel(pause.run_id, "cancel")
        with self.assertRaises(SecretNotFoundError):
            await store.consume(reference)  # type: ignore[arg-type]

    async def test_constructor_and_invalid_secret_result(self) -> None:
        with self.assertRaises(TypeError):
            RunManager(FakeFactory([]))  # type: ignore[call-arg]
        with self.assertRaises(RunConstructionError):
            RunManager(FakeFactory([]), secret_store=object())  # type: ignore[arg-type]
        invalid = result(
            AgentRunStatus.AWAITING_USER,
            question="Q",
            pause_kind=AgentPauseKind.SECRET,
            secret_fields=(SecretField.USERNAME, SecretField.PASSWORD),
        )
        with self.assertRaises(ValueError):
            RunManager._translate(invalid)

    async def test_strict_value_validation_and_defensive_copy(self) -> None:
        manager, store, _, pause = await self.secret_paused()
        invalid = (
            None,
            {},
            {"password": "value"},
            {SecretField.PASSWORD: None},
            {SecretField.PASSWORD: True},
            {SecretField.PASSWORD: "   "},
        )
        for values in invalid:
            with self.subTest(kind=type(values).__name__):
                with self.assertRaises(RunManagerError):
                    await manager.submit_secret(
                        pause.run_id, pause.interaction_id, "invalid", values  # type: ignore[arg-type]
                    )
        values = {
            SecretField.PASSWORD: "synthetic-pass",
            SecretField.USERNAME: "synthetic-user",
        }
        await manager.submit_secret(
            pause.run_id, pause.interaction_id, "copy", values
        )
        reference = manager._runs[pause.run_id].secret_reference
        values[SecretField.PASSWORD] = "mutated"
        stored = await store.consume(reference)  # type: ignore[arg-type]
        self.assertEqual(stored[SecretField.PASSWORD], "synthetic-pass")

    async def test_request_id_conflicts_across_operations(self) -> None:
        operations = ("respond", "confirm", "cancel")
        for operation in operations:
            manager, _, _, pause = await self.secret_paused()
            await manager.submit_secret(
                pause.run_id,
                pause.interaction_id,
                "shared",
                {
                    SecretField.PASSWORD: "synthetic-pass",
                    SecretField.USERNAME: "synthetic-user",
                },
            )
            with self.assertRaises(RunIdempotencyConflictError):
                if operation == "respond":
                    await manager.respond(
                        pause.run_id, pause.interaction_id, "shared", "answer"
                    )
                elif operation == "confirm":
                    await manager.confirm(
                        pause.run_id, pause.interaction_id, "shared", True
                    )
                else:
                    await manager.cancel(pause.run_id, "shared")

    async def test_fingerprint_and_manager_repr_do_not_expose_key_or_digest(self) -> None:
        manager, _, _, pause = await self.secret_paused()
        first = manager._secret_fingerprint(
            pause.interaction_id, {SecretField.OTP: "café"}
        )
        second = manager._secret_fingerprint(
            pause.interaction_id, {SecretField.OTP: "cafe"}
        )
        self.assertNotEqual(first, second)
        fingerprint = manager._runs[pause.run_id].requests
        rendered = repr((manager, fingerprint))
        self.assertNotIn(first.hex(), rendered)
        self.assertNotIn(bytes(manager._hmac_key).hex(), rendered)

    async def test_concurrent_submissions_have_one_transition(self) -> None:
        manager, _, _, pause = await self.secret_paused()
        values = {
            SecretField.PASSWORD: "synthetic-pass",
            SecretField.USERNAME: "synthetic-user",
        }
        identical = await asyncio.gather(
            manager.submit_secret(pause.run_id, pause.interaction_id, "same", values),
            manager.submit_secret(pause.run_id, pause.interaction_id, "same", dict(values)),
        )
        self.assertEqual(identical[0], identical[1])
        self.assertEqual(identical[0].status, RunStatus.AWAITING_SECRET_APPLICATION)

    async def test_secret_store_failures_are_fixed_and_unchained(self) -> None:
        session = FakeSession([result(
            AgentRunStatus.AWAITING_USER,
            question="Question",
            pause_kind=AgentPauseKind.SECRET,
            secret_fields=(SecretField.OTP,),
        )])
        store = TrackingSecretStore()
        manager = RunManager(FakeFactory([FakeHandle(session)]), secret_store=store)
        created = await manager.create_run("url", "task")
        await self.settle(manager, created.run_id)
        pause = await manager.get_run(created.run_id)
        store.fail_put = True
        with self.assertRaisesRegex(RunManagerError, "^secret storage failed safely$") as raised:
            await manager.submit_secret(
                pause.run_id, pause.interaction_id, "request", {SecretField.OTP: "synthetic-otp"}
            )
        self.assertIsNone(raised.exception.__cause__)
        self.assertIsNone(raised.exception.__context__)
        self.assertNotIn("SYNTHETIC", str(raised.exception))
        await manager.close()

    async def test_cancelled_secret_put_preserves_pause_without_request_state(self) -> None:
        session = FakeSession([result(
            AgentRunStatus.AWAITING_USER,
            question="Question",
            pause_kind=AgentPauseKind.SECRET,
            secret_fields=(SecretField.OTP,),
        )])
        store = ControlledSecretStore()
        store.put_error = asyncio.CancelledError()
        manager = RunManager(FakeFactory([FakeHandle(session)]), secret_store=store)
        created = await manager.create_run("url", "task")
        await self.settle(manager, created.run_id)
        pause = await manager.get_run(created.run_id)

        with self.assertRaises(asyncio.CancelledError):
            await manager.submit_secret(
                pause.run_id,
                pause.interaction_id,
                "cancelled-request",
                {SecretField.OTP: "synthetic-otp"},
            )

        unchanged = await manager.get_run(created.run_id)
        record = manager._runs[created.run_id]
        self.assertEqual(unchanged.status, RunStatus.AWAITING_SECRET)
        self.assertEqual(unchanged.interaction_id, pause.interaction_id)
        self.assertEqual(unchanged.question, pause.question)
        self.assertIsNone(record.secret_reference)
        self.assertNotIn("cancelled-request", record.requests)
        store.put_error = None
        accepted = await manager.submit_secret(
            pause.run_id,
            pause.interaction_id,
            "usable-request",
            {SecretField.OTP: "synthetic-otp"},
        )
        self.assertEqual(accepted.status, RunStatus.AWAITING_SECRET_APPLICATION)
        await manager.close()

    async def test_cancelled_discard_run_propagates_unchanged(self) -> None:
        store = ControlledSecretStore()
        store.discard_run_error = asyncio.CancelledError()
        manager = RunManager(FakeFactory([]), secret_store=store)

        with self.assertRaises(asyncio.CancelledError):
            await manager._run_secret_cleanup("synthetic-run")

        store.discard_run_error = None
        await manager.close()

    def test_run_manager_has_no_silent_base_exception_handler(self) -> None:
        source_path = Path(__file__).resolve().parents[1] / "browser_agent/run_manager.py"
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        silent_handlers = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.ExceptHandler)
            and isinstance(node.type, ast.Name)
            and node.type.id == "BaseException"
            and any(isinstance(statement, ast.Pass) for statement in node.body)
        ]
        self.assertEqual(silent_handlers, [])

    async def test_cancelled_close_waiter_does_not_cancel_shared_worker(self) -> None:
        store = ControlledSecretStore()
        first_session = FakeSession([result(
            AgentRunStatus.AWAITING_USER,
            question="Q1",
            pause_kind=AgentPauseKind.USER_INPUT,
        )])
        second_session = FakeSession([result(
            AgentRunStatus.AWAITING_USER,
            question="Q2",
            pause_kind=AgentPauseKind.USER_INPUT,
        )])
        first_handle = BlockingHandle(first_session)
        second_handle = FakeHandle(second_session)
        manager = RunManager(
            FakeFactory([first_handle, second_handle]), secret_store=store
        )
        created = [
            await manager.create_run("url", "first"),
            await manager.create_run("url", "second"),
        ]
        for item in created:
            await self.settle(manager, item.run_id)

        first_waiter = asyncio.create_task(manager.close())
        await first_handle.close_started.wait()
        close_worker = manager._close_task
        self.assertIsNotNone(close_worker)
        first_waiter.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await first_waiter
        self.assertFalse(close_worker.cancelled())
        self.assertFalse(close_worker.done())
        self.assertEqual(second_handle.close_calls, 0)
        self.assertEqual(store.close_calls, 0)

        later_waiter = asyncio.create_task(manager.close())
        first_handle.close_release.set()
        await later_waiter
        self.assertEqual(first_handle.close_calls, 1)
        self.assertEqual(second_handle.close_calls, 1)
        self.assertEqual(store.close_calls, 1)
        self.assertEqual(bytes(manager._hmac_key), b"\x00" * 32)

    async def test_run_cleanup_failure_is_exhaustive_sticky_and_unchained(self) -> None:
        store = ControlledSecretStore()
        failed_handle = FakeHandle(
            FakeSession([result(
                AgentRunStatus.AWAITING_USER,
                question="Q1",
                pause_kind=AgentPauseKind.USER_INPUT,
            )]),
            RuntimeError("SYNTHETIC-RUN-CLEANUP-DETAIL"),
        )
        succeeding_handle = FakeHandle(FakeSession([result(
            AgentRunStatus.AWAITING_USER,
            question="Q2",
            pause_kind=AgentPauseKind.USER_INPUT,
        )]))
        manager = RunManager(
            FakeFactory([failed_handle, succeeding_handle]), secret_store=store
        )
        created = [
            await manager.create_run("url", "first"),
            await manager.create_run("url", "second"),
        ]
        for item in created:
            await self.settle(manager, item.run_id)

        for _ in range(2):
            with self.assertRaisesRegex(
                RunManagerError, "^run manager close failed safely$"
            ) as raised:
                await manager.close()
            self.assertIsNone(raised.exception.__cause__)
            self.assertIsNone(raised.exception.__context__)
        self.assertEqual(failed_handle.close_calls, 1)
        self.assertEqual(succeeding_handle.close_calls, 1)
        self.assertEqual(store.close_calls, 1)
        self.assertEqual(bytes(manager._hmac_key), b"\x00" * 32)

    async def test_close_is_exhaustive_sticky_and_zeroizes_key(self) -> None:
        store = TrackingSecretStore()
        store.fail_discard_run = True
        store.fail_close = True
        sessions = [FakeSession([result(AgentRunStatus.AWAITING_USER, question="Q", pause_kind=AgentPauseKind.USER_INPUT)]) for _ in range(2)]
        manager = RunManager(
            FakeFactory([FakeHandle(item) for item in sessions]), secret_store=store
        )
        created = [await manager.create_run("url", f"task-{index}") for index in range(2)]
        for item in created:
            await self.settle(manager, item.run_id)
        outcomes = await asyncio.gather(manager.close(), manager.close(), return_exceptions=True)
        self.assertTrue(all(type(item) is RunManagerError for item in outcomes))
        self.assertTrue(all(str(item) == "run manager close failed safely" for item in outcomes))
        self.assertTrue(all(item.__cause__ is None and item.__context__ is None for item in outcomes))
        self.assertEqual(set(store.discard_run_calls), {item.run_id for item in created})
        self.assertEqual(store.close_calls, 1)
        self.assertEqual(bytes(manager._hmac_key), b"\x00" * 32)
        with self.assertRaisesRegex(RunManagerError, "^run manager close failed safely$"):
            await manager.close()
        self.assertEqual(store.close_calls, 1)


if __name__ == "__main__":
    unittest.main()
