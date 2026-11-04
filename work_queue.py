"""Thread-safe FIFO within each attempt round; retries never overtake new work."""
import heapq
import queue
import uuid


class WorkQueue(queue.PriorityQueue):
    def __init__(self):
        super().__init__()
        self.sequence = 0

    def _put(self, task):
        self.sequence += 1
        heapq.heappush(self.queue, (int(task.get('_retry_round', 0)), self.sequence, task))

    def _get(self):
        return heapq.heappop(self.queue)[2]

    def snapshot(self):
        with self.mutex:
            return [item[2] for item in sorted(self.queue)]

    def get_for(self, allowed):
        with self.not_empty:
            candidates = [(entry[:2], i) for i, entry in enumerate(self.queue) if allowed(entry[2])]
            if not candidates:
                raise queue.Empty
            _, index = min(candidates)
            task = self.queue.pop(index)[2]
            heapq.heapify(self.queue)
            self.not_full.notify()
            return task


def snapshot(source):
    if hasattr(source, 'snapshot'):
        return source.snapshot()
    with source.mutex:
        return list(source.queue)


def take_for(source, allowed):
    if hasattr(source, 'get_for'):
        return source.get_for(allowed)
    with source.not_empty:
        for index, task in enumerate(source.queue):
            if allowed(task):
                del source.queue[index]
                source.not_full.notify()
                return task
    raise queue.Empty


def next_attempt(task):
    again = dict(task, _attempt=uuid.uuid4().hex, run_action='new',
                 _retry_round=int(task.get('_retry_round', 0)) + 1)
    for key in ('conversation_url', 'conversation_account', '_auto_deadline', 'output_path'):
        again.pop(key, None)
    return again
