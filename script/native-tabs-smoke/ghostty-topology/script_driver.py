"""In-process Ghostty diagnostic boundary; importing it performs no native work."""
import ctypes
import json


class DriverError(RuntimeError):
    pass


class ScriptDriver:
    CAPABILITY = b'ghostty-topology-retained-source-v3'
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

    def run(self, pid, operation, *arguments, source=None, confirm_source=None):
        if type(pid) is not int or pid <= 0:
            raise DriverError('Invalid target PID')
        request = dict(argv=[str(pid), operation, *map(str, arguments)])
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
