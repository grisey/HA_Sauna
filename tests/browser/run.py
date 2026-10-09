"""Run browser tests with immediate tracebacks, including subtest failures."""

from pathlib import Path
import sys
import traceback
import unittest


class ImmediateResult(unittest.TextTestResult):
    def report(self, test, error):
        self.stream.writeln(f"\n{self.separator1}\n{test.id()}")
        traceback.print_exception(*error, file=self.stream)
        self.stream.flush()

    def addError(self, test, error):
        super().addError(test, error)
        self.report(test, error)

    def addFailure(self, test, error):
        super().addFailure(test, error)
        self.report(test, error)

    def addSubTest(self, test, subtest, error):
        super().addSubTest(test, subtest, error)
        if error is not None:
            self.report(subtest, error)


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    suite = unittest.defaultTestLoader.discover(str(Path(__file__).parent))
    result = unittest.TextTestRunner(verbosity=2, resultclass=ImmediateResult).run(suite)
    raise SystemExit(0 if result.wasSuccessful() else 1)
