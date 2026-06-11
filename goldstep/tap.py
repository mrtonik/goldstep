# SPDX-License-Identifier: BSD-2-Clause
# Copyright (c) 2026 Michael Hupp
"""A tiny TAP (Test Anything Protocol) reporter.

Each test file is one TAP stream on stdout; done() prints the plan line and
exits non-zero if anything failed, so a shell runner can aggregate pass/fail.
"""

import sys


class Test:
    def __init__(self, name):
        self.name = name
        self.n = 0
        self.failed = 0
        sys.stdout.write("# %s\n" % name); sys.stdout.flush()

    def _emit(self, ok, label, diag=None):
        self.n += 1
        sys.stdout.write("%s %d - %s\n" % ("ok" if ok else "not ok", self.n, label))
        if not ok:
            self.failed += 1
            if diag:
                for line in str(diag).splitlines():
                    sys.stdout.write("#   %s\n" % line)
        sys.stdout.flush()
        return ok

    def ok(self, cond, label): return self._emit(bool(cond), label)

    def eq(self, got, want, label):
        return self._emit(got == want, label,
                          None if got == want else "got %r, want %r" % (got, want))

    def ne(self, got, bad, label):
        return self._emit(got != bad, label,
                          None if got != bad else "got %r (should differ)" % (got,))

    def approx(self, got, want, tol, label):
        good = got is not None and abs(got - want) <= tol
        return self._emit(good, label,
                          None if good else "got %r, want %r +-%r" % (got, want, tol))

    def fail(self, label, diag=None): return self._emit(False, label, diag)

    def done(self):
        sys.stdout.write("1..%d\n" % self.n)
        sys.stdout.write("# %s: %d/%d passed\n" % (self.name, self.n - self.failed, self.n))
        sys.stdout.flush()
        sys.exit(1 if self.failed else 0)
