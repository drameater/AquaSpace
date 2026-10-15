"""In-process Ghostty diagnostic boundary; importing it performs no native work."""
import ctypes
import json


class DriverError(RuntimeError):
    pass


class ScriptDriver:
    # v6 also requires fail-closed native background admission for cleanup.
    CAPABILITY = b'ghostty-topology-retained-source-v6'
    MUTATIONS = frozenset(('create', 'add', 'select', 'activate'))

    def __init__(self, path, *, load_library=None):
        library = (load_library or ctypes.CDLL)(str(path))
        try:
            capability = library.AquaSpaceGhosttyDriverCapabilities
            capability.argtypes, capability.restype = [], ctypes.c_char_p
            self._run = library.AquaSpaceGhosttyDriverRun
            self._run.argtypes = [ctypes.c_char_p, ctypes.c_void_p, ctypes.c_void_p]
            self._run.restype = ctypes.c_void_p
            self._free = library.AquaSpaceGhosttyDriverFree
            self._free.argtypes, self._free.restype = [ctypes.c_void_p], None
        except AttributeError as error:
            raise DriverError('Rebuild the retained-source diagnostic library') from error
        if capability() != self.CAPABILITY:
            raise DriverError('Unsupported diagnostic library; no fallback to subprocesses')
        self.library = library

    @staticmethod
    def _pointer(value):
        if (type(value) is not int or value <= 0
                or value >= 1 << (8 * ctypes.sizeof(ctypes.c_void_p))):
            raise DriverError('Missing retained native reference')
        return ctypes.c_void_p(value)

    def run(self, pid, operation, *arguments, source=None, confirm_source=None,
            expected_birth=None, protected_pids=None):
        if type(pid) is not int or not 0 < pid <= 0x7fffffff:
            raise DriverError('Invalid target PID')
        request = dict(argv=[str(pid), operation, *map(str, arguments)])
        if operation in ('close', 'terminate'):
            if ((operation == 'terminate' and arguments)
                    or (operation == 'close' and (len(arguments) != 2 or any(not isinstance(v,str) or not v for v in arguments)))
                    or not isinstance(expected_birth, (tuple, list))
                    or len(expected_birth) != 4
                    or any(type(value) is not int for value in expected_birth)
                    or expected_birth[0] != pid or not 0 < expected_birth[1] <= 0xffffffffffffffff
                    or not 0 <= expected_birth[2] < 1000000
                    or not 0 <= expected_birth[3] <= 0xffffffff
                    or not isinstance(protected_pids, (tuple, list)) or not protected_pids
                    or any(type(value) is not int or not 0 < value <= 0x7fffffff or value == pid
                           for value in protected_pids)):
                raise DriverError('Pinned disposable process identity and protected PIDs required')
            request['process'] = dict(birth=list(expected_birth), protected=list(protected_pids))
        app, window = ctypes.c_void_p(), ctypes.c_void_p()
        if operation in self.MUTATIONS:
            if source is None or not callable(confirm_source):
                raise DriverError('Exact retained foreground source is required')
            if (type(source.pid) is not int or source.pid <= 0
                    or (source.title is not None and not isinstance(source.title, str))):
                raise DriverError('Invalid foreground source metadata')
            app = self._pointer(source.app)
            if source.window is not None:
                window = self._pointer(source.window)
            request['foreground'] = dict(pid=source.pid, title=source.title,
                                         hasWindow=source.window is not None)
        payload = json.dumps(request).encode('utf-8')
        if operation in self.MUTATIONS:
            # Keep the original snapshot alive across serialization and the C
            # call. The library independently CFRetains these exact references.
            confirm_source(source)
        result = self._run(payload, app, window)
        if not result:
            raise DriverError('Native diagnostic returned no result')
        try:
            response = json.loads(ctypes.string_at(result).decode('utf-8'))
            if not isinstance(response, dict) or response.get('ok') is not True:
                raise DriverError(str(response.get('error', 'Native diagnostic failed'))
                                  if isinstance(response, dict) else 'Invalid native response')
            if not isinstance(response.get('result'), dict):
                raise DriverError('Invalid native result')
            return response['result']
        finally:
            self._free(result)
