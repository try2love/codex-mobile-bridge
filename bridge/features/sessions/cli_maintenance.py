"""Run a single confirmed CLI maintenance transaction when native tasks settle."""
import threading


WAIT_INTERVAL = 5


class CliTasksPending(ValueError):
    """The verified service has busy tasks or insufficient task evidence."""


def maintenance_receipt(record):
    return {key: record[key] for key in ('purpose', 'operationId', 'threadId', 'targetThread', 'host', 'home', 'confirmation')}


class CliMaintenance:
    def __init__(self):
        self.lock = threading.RLock()
        self.operation_id = None
        self.worker = None
        self.cancelled = None

    def running(self, operation_id):
        return bool(self.operation_id == operation_id and self.worker and self.worker.is_alive()
                    and not self.cancelled.is_set())

    def status(self, record):
        phase, operation_id = record['phase'], record['operationId']
        waiting, active = phase == 'waiting', self.running(operation_id)
        result = record.get('maintenanceResult') or {}
        if waiting:
            state = 'waiting'
            message = record['message'] if active else '等待已暂停；重新确认后继续检查此配置的任务。'
        elif phase == 'cancelled':
            state, message = 'cancelled', '已取消等待，未退出服务。'
        elif phase == 'failed' or record.get('providerPhase') == 'failed':
            state, message = 'failed', record.get('maintenanceError') or 'CLI 恢复未完成，请重新检查。'
        elif result:
            state, message = 'complete', 'CLI 恢复已完成。'
        elif active:
            state, message = 'recovering', '正在恢复此配置的 CLI 服务。'
        else:
            state = 'unknown'
            message = record.get('maintenanceError') or 'CLI 恢复结果待核对；不会重复执行。'
        return {'operationId': operation_id, 'threadId': record['threadId'], 'state': state,
                'maintenancePending': waiting, 'requiresConfirmation': waiting, 'canCancel': waiting,
                'canRecover': waiting and not active, 'generation': record['confirmation']['generation'],
                'message': message, 'connected': result.get('connected', False)}

    def start(self, operation_id, attempt, finish):
        # The caller serializes admission using the existing home maintenance lock.
        if self.running(operation_id):
            return
        self.cancel()
        cancelled = threading.Event()

        def run():
            while not cancelled.is_set():
                try:
                    result = attempt()
                except CliTasksPending:
                    cancelled.wait(WAIT_INTERVAL)
                    continue
                except Exception as error:
                    finish(None, error)
                else:
                    finish(result, None)
                return

        self.operation_id, self.cancelled = operation_id, cancelled
        self.worker = threading.Thread(target=run, daemon=True, name='cli-maintenance-wait')
        self.worker.start()

    def cancel(self):
        if self.cancelled is not None:
            self.cancelled.set()
