"""Poll SDK jobs; healthy work is unlimited, cancellation/cleanup is bounded."""
import time


def wait_job(job, *, timeout=None, cancelled=None, cancel_grace=5.0):
    if job.job_id == 0:
        return job  # A rejected post never reaches a terminal SDK status.
    deadline = None if timeout is None else time.monotonic() + timeout
    while not job.done:
        now = time.monotonic()
        if deadline is None and cancelled is not None and cancelled():
            deadline = now + cancel_grace
        if deadline is not None and now >= deadline:
            raise TimeoutError("작업 중지 및 정리 응답 대기 시간이 초과되었습니다.")
        time.sleep(0.02)
    # The SDK's terminal status guarantees this wait no longer blocks.
    return job.wait()
