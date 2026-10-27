"""Full CI regression with immediate failure details and bounded hung tests."""
import faulthandler
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / '.build-env'))


class Result(unittest.TextTestResult):
    def startTest(self, test):
        super().startTest(test)
        # Includes teardown: an unexpected modal must not consume the entire job.
        faulthandler.dump_traceback_later(90, exit=True)

    def stopTest(self, test):
        faulthandler.cancel_dump_traceback_later()
        super().stopTest(test)

    def addFailure(self, test, error):
        super().addFailure(test, error)
        self.stream.writeln(self._exc_info_to_string(error, test))

    def addError(self, test, error):
        super().addError(test, error)
        self.stream.writeln(self._exc_info_to_string(error, test))


if __name__ == '__main__':
    faulthandler.enable()
    suite = unittest.defaultTestLoader.discover(str(ROOT / 'tests'))
    result = unittest.TextTestRunner(verbosity=2, resultclass=Result).run(suite)
    raise SystemExit(0 if result.wasSuccessful() else 1)
