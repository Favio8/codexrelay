"""Stop repeated failed tools and repeated mutation reversals, not useful work."""
import hashlib
import json


class ProgressGuard:
    def __init__(self):
        self.failures = {}
        self.file_versions = {}

    def check(self, name: str, arguments: dict, revision: str) -> None:
        key = self._key(name, arguments)
        previous = self.failures.get(key)
        if previous and previous['revision'] == revision and previous['count'] >= 2:
            raise RuntimeError('NO_PROGRESS: two equivalent tool failures without changed inputs; return remaining work to the parent')

    def record(self, name: str, arguments: dict, revision: str, output: str) -> None:
        key = self._key(name, arguments)
        failed = output.startswith('ERROR:') or (name == 'Bash' and output.startswith('[exit ') and not output.startswith('[exit 0]'))
        if not failed:
            self.failures.pop(key, None)
            return
        digest = hashlib.sha256(output.encode('utf-8', errors='backslashreplace')).hexdigest()
        previous = self.failures.get(key)
        count = previous['count'] + 1 if previous and previous['revision'] == revision and previous['digest'] == digest else 1
        self.failures[key] = {'revision': revision, 'digest': digest, 'count': count}

    def mutation(self, path: str, digest: str) -> None:
        versions = self.file_versions.setdefault(path, [])
        if versions and versions[-1] == digest:
            return
        versions.append(digest)
        # One experimental revert is legitimate. Repeating the same cycle is not.
        for period in range(2, (len(versions) - 1) // 2 + 1):
            first = versions[-2 * period - 1:-period - 1]
            second = versions[-period - 1:-1]
            if first == second and versions[-1] == first[0]:
                raise RuntimeError('NO_PROGRESS: repeated file-content reversal; inspect existing changes before continuing')

    @staticmethod
    def _key(name, arguments):
        return hashlib.sha256(json.dumps([name, arguments], sort_keys=True).encode()).hexdigest()
